import { HTTPException } from "hono/http-exception";
import getEnvs from "./getEnvs";
import logMessage from "./logMessage";
import { credentialMode, providersFromForm } from "./providers";
import { userKeysEnabled } from "./userKeys";
import writeProviders, {
	type ProviderConfig,
	readProviders,
} from "./writeProviders";

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
	const userKeys = userKeysEnabled(await getEnvs());
	// before anything is written, so a mode or a provider that can't be used leaves them as they were
	const kept = config.providers
		.filter((provider) => !removed.includes(provider.id))
		.map((provider): ProviderConfig => {
			const field = options.get(`mode_${provider.id}`)?.toString();
			// only offered with security fully enabled, otherwise the mode stays as it was
			if (!field) return provider;
			const { credential_mode, ...rest } = provider;
			const mode = credentialMode(
				field,
				provider.name,
				userKeys,
				provider.id == embeddingsProvider,
			);
			return mode == "shared" ? rest : { ...rest, credential_mode: mode };
		});
	const added = providersFromForm(
		options,
		kept.map((provider) => provider.id),
		userKeys,
	);
	const newKeys = Object.fromEntries(
		Object.entries(keys).filter(([id]) => !removed.includes(id)),
	);
	for (const provider of config.providers.filter((p) => removed.includes(p.id)))
		yield logMessage(`removing ${provider.name}...`);
	for (const provider of kept) {
		const before = config.providers.find((p) => p.id == provider.id);
		if (
			(before?.credential_mode || "shared") !=
			(provider.credential_mode || "shared")
		)
			yield logMessage(`changing whose API key ${provider.name} uses...`);
		// the field is left empty to keep the key there is
		const key = options.get(`key_${provider.id}`)?.toString().trim();
		if (!key) continue;
		newKeys[provider.id] = key;
		yield logMessage(`replacing the API key for ${provider.name}...`);
	}
	for (const { provider, key } of added) {
		yield logMessage(`adding ${provider.name}...`);
		if (key) newKeys[provider.id] = key;
	}
	const providers = [...kept, ...added.map((a) => a.provider)];
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
