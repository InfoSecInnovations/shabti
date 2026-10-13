import { HTTPException } from "hono/http-exception";
import logMessage from "./logMessage";
import { providerFromForm } from "./providers";
import writeProviders, { readProviders } from "./writeProviders";

// adds, removes and rekeys the providers of an existing installation. The API re-reads both files
// when they change, so nothing has to be restarted
export default async function* (options: FormData) {
	const { config, keys } = await readProviders();
	const removed = options.getAll("remove_provider").map((v) => v.toString());
	// the embeddings model can't change after install, and its provider is what serves it
	const embeddingsProvider = config.embeddings?.model.split("/")[0];
	if (embeddingsProvider && removed.includes(embeddingsProvider))
		throw new HTTPException(400, {
			message: "The embeddings provider can't be removed.",
		});
	const kept = config.providers.filter(
		(provider) => !removed.includes(provider.id),
	);
	// before anything is written, so a provider that can't be added leaves the others as they were
	const added = providerFromForm(
		options,
		kept.map((provider) => provider.id),
	);
	const newKeys = Object.fromEntries(
		Object.entries(keys).filter(([id]) => !removed.includes(id)),
	);
	for (const provider of config.providers.filter((p) => removed.includes(p.id)))
		yield logMessage(`removing ${provider.name}...`);
	for (const provider of kept) {
		// the field is left empty to keep the key there is
		const key = options.get(`key_${provider.id}`)?.toString().trim();
		if (!key) continue;
		newKeys[provider.id] = key;
		yield logMessage(`replacing the API key for ${provider.name}...`);
	}
	if (added) {
		yield logMessage(`adding ${added.provider.name}...`);
		if (added.key) newKeys[added.provider.id] = added.key;
	}
	const providers = added ? [...kept, added.provider] : kept;
	// a default whose provider is gone would only be skipped by the API, so it's dropped here
	const { default_chat_model, ...rest } = config;
	const defaultKept =
		default_chat_model &&
		!removed.some((id) => default_chat_model.startsWith(`${id}/`));
	await writeProviders(
		{
			...rest,
			providers,
			...(defaultKept ? { default_chat_model } : {}),
		},
		newKeys,
	);
	if (default_chat_model && !defaultKept)
		yield logMessage(
			`${default_chat_model} was the default chat model, users will start on another one.`,
		);
}
