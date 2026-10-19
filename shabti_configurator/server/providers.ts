import { HTTPException } from "hono/http-exception";
import type { RemoteEmbeddings } from "../getDefaultModelSelection";
import { LOCAL_PROVIDER, providerId, qualify } from "../providerIds";
import {
	CREDENTIAL_MODES,
	type CredentialMode,
	type ProviderConfig,
	type ProvidersConfig,
} from "./writeProviders";

// a provider that hasn't answered by then is as good as unreachable for the admin waiting on it
const TIMEOUT_MS = 10_000;

// what people paste from a provider's docs: the endpoint they call rather than the API it belongs to
const ENDPOINT_SUFFIX = /\/(chat\/completions|completions|embeddings|models)$/;

const invalid = (message: string) => new HTTPException(400, { message });

/** the URL the API appends its paths to, from whatever the admin pasted */
export const normaliseBaseUrl = (raw: string) => {
	let url: URL;
	try {
		url = new URL(raw.trim());
	} catch {
		throw invalid(`${raw} is not a URL`);
	}
	if (url.protocol != "http:" && url.protocol != "https:")
		throw invalid(`${raw} is not an http or https URL`);
	url.search = "";
	url.hash = "";
	return url
		.toString()
		.replace(/\/+$/, "")
		.replace(ENDPOINT_SUFFIX, "")
		.replace(/\/+$/, "");
};

/** extra headers, one `Name: value` per line */
export const parseHeaders = (raw: string) =>
	Object.fromEntries(
		raw
			.split(/\r?\n/)
			.map((line) => line.trim())
			.filter((line) => line)
			.map((line) => {
				const separator = line.indexOf(":");
				const name = line.slice(0, separator).trim();
				if (separator < 1 || !/^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/.test(name))
					throw invalid(`"${line}" is not a header, write it as Name: value`);
				return [name, line.slice(separator + 1).trim()];
			}),
	);

export type TestResult =
	| { baseUrl: string; models: string[]; embeddingsModels: string[] }
	| { error: string; hint?: string };

// most OpenAI-compatible APIs are versioned, and a base URL without the version is the usual mistake
const versionHint = (baseUrl: string) =>
	/\/v\d+[a-z]*$/.test(baseUrl)
		? undefined
		: "Most providers' base URLs end in a version, such as /v1.";

type ListedModel = {
	id: unknown;
	architecture?: { output_modalities?: unknown };
};

const listModels = (
	url: string,
	headers: Record<string, string>,
	key?: string,
) =>
	fetch(url, {
		headers: {
			...headers,
			...(key ? { Authorization: `Bearer ${key}` } : {}),
		},
		// the key would follow a redirect wherever it pointed
		redirect: "manual",
		signal: AbortSignal.timeout(TIMEOUT_MS),
	});

type NamedModel = ListedModel & { id: string };

const modelList = async (response: Response) =>
	((await response.json()) as { data: ListedModel[] }).data.filter(
		(model): model is NamedModel => typeof model.id == "string",
	);

/**
 * The models a provider lists apart from its others, which OpenRouter does with its embeddings
 * models: its /models leaves them out. Not part of the OpenAI API, so a provider without the
 * endpoint just has none listed there.
 */
const separateEmbeddingsModels = async (
	baseUrl: string,
	headers: Record<string, string>,
	key?: string,
) => {
	try {
		const response = await listModels(
			`${baseUrl}/embeddings/models`,
			headers,
			key,
		);
		if (response.status != 200) return [];
		return (await modelList(response)).map((model) => model.id);
	} catch {
		return [];
	}
};

// the same rule the API uses to tell an embeddings model from a chat model, plus what a listing
// that describes its models says
const isEmbeddingsModel = (model: NamedModel) =>
	model.id.toLowerCase().includes("embed") ||
	(Array.isArray(model.architecture?.output_modalities) &&
		model.architecture.output_modalities.includes("embeddings"));

const sorted = (ids: Iterable<string>) =>
	[...new Set(ids)].sort((a, b) => a.localeCompare(b));

/**
 * Asks the provider for its models the way the API will, so a wrong URL or key shows up here
 * rather than as an empty model list after installing. Nothing it answers with is passed on but
 * the model ids, as an error body can echo the key back.
 */
export const testProvider = async (options: {
	baseUrl: string;
	key?: string;
	headers?: string;
}): Promise<TestResult> => {
	let baseUrl: string;
	let headers: Record<string, string>;
	try {
		baseUrl = normaliseBaseUrl(options.baseUrl);
		headers = parseHeaders(options.headers ?? "");
	} catch (error) {
		return { error: (error as Error).message };
	}
	let response: Response;
	try {
		response = await listModels(`${baseUrl}/models`, headers, options.key);
	} catch {
		return { error: `Couldn't reach ${baseUrl}.` };
	}
	if (response.status == 401 || response.status == 403)
		return { error: "The provider refused the key." };
	if (response.status != 200)
		return {
			error: `${baseUrl}/models answered with status ${response.status}.`,
			hint: versionHint(baseUrl),
		};
	let listed: NamedModel[];
	try {
		listed = await modelList(response);
	} catch {
		return {
			error: `${baseUrl}/models didn't list any models.`,
			hint: versionHint(baseUrl),
		};
	}
	const embeddingsModels = sorted([
		...(await separateEmbeddingsModels(baseUrl, headers, options.key)),
		...listed.filter(isEmbeddingsModel).map((model) => model.id),
	]);
	const models = sorted(
		listed
			.map((model) => model.id)
			.filter((id) => !embeddingsModels.includes(id)),
	);
	return { baseUrl, models, embeddingsModels };
};

/**
 * Whose key a provider is used with. Users' own keys need security to be fully enabled, and the
 * provider that embeds documents does it for everyone, so it can only have the shared key.
 */
export const credentialMode = (
	raw: string,
	name: string,
	userKeys: boolean,
	embeds: boolean,
): CredentialMode => {
	const mode = (raw || "shared") as CredentialMode;
	if (!CREDENTIAL_MODES.includes(mode))
		throw invalid(`${raw} is not a credential mode.`);
	if (mode == "shared") return mode;
	if (!userKeys)
		throw invalid(`Users' own keys for ${name} need security to be enabled.`);
	if (embeds)
		throw invalid(`${name} embeds the documents, so it needs a shared key.`);
	return mode;
};

/** the providers an installation has, and their keys, as readProviders reads them */
export type StoredProviders = {
	config: ProvidersConfig;
	keys: Record<string, string>;
};

/**
 * The installed provider a fieldset was prefilled from, and its key, so a reinstall can keep a key
 * the form never shows. Only while the base URL is the one it had, so the key goes nowhere else.
 */
export const storedProvider = (
	stored: StoredProviders | undefined,
	existingId: string,
	baseUrl: string,
) => {
	const provider = stored?.config.providers.find((p) => p.id == existingId);
	if (!stored || !provider) return undefined;
	try {
		if (normaliseBaseUrl(baseUrl) != provider.base_url) return undefined;
	} catch {
		return undefined;
	}
	return { provider, key: stored.keys[provider.id] };
};

/**
 * The provider a form adds, from the fields ProviderFieldset renders with the given prefix, or
 * undefined when they were left empty. Checked before an install or a change touches anything, so
 * a mistake leaves things as they were. One added only to embed with offers none of its chat models.
 * One prefilled from an installed provider keeps its key when none is typed, and what the form
 * can't change about it.
 */
export const providerFromForm = (
	form: FormData,
	existingIds: string[],
	embedsOnly = false,
	userKeys = false,
	prefix = "",
	stored?: StoredProviders,
): { provider: ProviderConfig; key?: string } | undefined => {
	const field = (name: string) =>
		form.get(`${prefix}${name}`)?.toString().trim() ?? "";
	const name = field("provider_name");
	const baseUrl = field("provider_base_url");
	if (!name && !baseUrl) return undefined;
	if (!name) throw invalid("Give the model provider a name.");
	const id = providerId(name);
	if (!id) throw invalid(`${name} needs at least one letter or digit.`);
	if (id == LOCAL_PROVIDER)
		throw invalid(`"${name}" is reserved for the local models.`);
	if (existingIds.includes(id))
		throw invalid(`There is already a model provider called ${name}.`);
	if (!baseUrl) throw invalid(`Give ${name} a base URL.`);
	const models = form
		.getAll(`${prefix}provider_model`)
		.map((v) => v.toString());
	if (!models.length && !embedsOnly)
		throw invalid(
			`Test the connection to ${name} and choose at least one of its models.`,
		);
	const headers = parseHeaders(field("provider_headers"));
	const previous = storedProvider(stored, field("provider_existing"), baseUrl);
	const key = field("provider_key") || previous?.key;
	const mode = credentialMode(
		field("provider_credential_mode"),
		name,
		userKeys,
		embedsOnly,
	);
	const userIdField = previous?.provider.user_id_field;
	const overrides = previous?.provider.models?.overrides;
	return {
		provider: {
			id,
			name,
			base_url: normaliseBaseUrl(baseUrl),
			...(Object.keys(headers).length ? { headers } : {}),
			...(mode != "shared" ? { credential_mode: mode } : {}),
			...(userIdField ? { user_id_field: userIdField } : {}),
			// an empty list rather than none, which would offer every model it lists
			models: { allow: models, ...(overrides ? { overrides } : {}) },
		},
		...(key ? { key } : {}),
	};
};

/**
 * The provider whose model the install form embeds with, and the model, or undefined to embed
 * with a local one. The form names the model after the prefix of the fieldset it came from, as
 * `prefix:model`: a prefix has no colon in it, while a model id can.
 */
export const embeddingsChoice = (form: FormData) => {
	if (form.get("embeddings_source")?.toString() != "provider") return undefined;
	const value = form.get("remote_embeddings_model")?.toString() ?? "";
	const separator = value.indexOf(":");
	if (separator < 0) return undefined;
	return {
		prefix: value.slice(0, separator),
		model: value.slice(separator + 1).trim(),
	};
};

export type AddedProvider = {
	prefix: string;
	provider: ProviderConfig;
	key?: string;
};

/**
 * Every provider a form adds, one for each ProviderFieldset that was filled in, in the order they
 * were. A name can't be used twice, among them or with the providers there already are.
 */
export const providersFromForm = (
	form: FormData,
	existingIds: string[],
	userKeys = false,
	stored?: StoredProviders,
) => {
	const embedsWith = embeddingsChoice(form)?.prefix;
	const added: AddedProvider[] = [];
	for (const prefix of new Set(
		form.getAll("provider_prefix").map((v) => v.toString()),
	)) {
		const provider = providerFromForm(
			form,
			[...existingIds, ...added.map((a) => a.provider.id)],
			prefix == embedsWith,
			userKeys,
			prefix,
			stored,
		);
		if (provider) added.push({ prefix, ...provider });
	}
	return added;
};

// what a provider's embeddings model is set up with when the admin doesn't say. a chunk is a passage
// someone gets shown, and a remote model is metered and rate limited per request
export const DEFAULT_REMOTE_CHUNK_SIZE = 512;
export const DEFAULT_REMOTE_BATCH_SIZE = 64;

export const TOKENIZERS = [
	"estimate",
	"tiktoken:cl100k_base",
	"tiktoken:o200k_base",
	"hf",
];

const wholeNumber = (raw: string, label: string) => {
	if (!raw) return undefined;
	const value = Number(raw);
	if (!Number.isInteger(value) || value < 1)
		throw invalid(`${label} must be a whole number above zero.`);
	return value;
};

/**
 * The provider's model the install embeds with, from the embeddings fields of the install form, or
 * undefined to embed with a local model. The provider is one of those the same form adds, since
 * there are no others before Shabti is installed.
 */
export const remoteEmbeddingsFromForm = (
	form: FormData,
	added: Pick<AddedProvider, "prefix" | "provider">[],
): RemoteEmbeddings | undefined => {
	const field = (name: string) => form.get(name)?.toString().trim() ?? "";
	if (field("embeddings_source") != "provider") return undefined;
	if (!added.length) throw invalid("Add a model provider to embed with it.");
	const choice = embeddingsChoice(form);
	// the fieldset the model was chosen from may have been emptied or removed since
	const provider = added.find((a) => a.prefix == choice?.prefix)?.provider;
	const model = choice?.model;
	if (!provider || !model)
		throw invalid(
			"Test the connection to a model provider and choose its embeddings model.",
		);
	let tokenizer = field("embeddings_tokenizer") || "estimate";
	if (!TOKENIZERS.includes(tokenizer))
		throw invalid(`${tokenizer} is not a tokenizer Shabti knows about.`);
	if (tokenizer == "hf") {
		const repo = field("embeddings_tokenizer_repo");
		if (!/^[A-Za-z0-9][\w.-]*\/[A-Za-z0-9][\w.-]*$/.test(repo))
			throw invalid(
				"Give the Hugging Face repository the tokenizer comes from, as owner/name.",
			);
		tokenizer = `hf:${repo}`;
	}
	const dimensions = wholeNumber(field("embeddings_dimensions"), "Dimensions");
	return {
		model: qualify(provider.id, model),
		...(dimensions ? { dimensions } : {}),
		settings: {
			chunk_size:
				wholeNumber(field("embeddings_chunk_size"), "Chunk size") ??
				DEFAULT_REMOTE_CHUNK_SIZE,
			tokenizer,
			batch_size:
				wholeNumber(field("embeddings_batch_size"), "Batch size") ??
				DEFAULT_REMOTE_BATCH_SIZE,
		},
	};
};

/** the Hugging Face repository a tokenizer setting names, if it names one */
export const tokenizerRepo = (tokenizer: string) =>
	tokenizer.startsWith("hf:") ? tokenizer.slice(3) : undefined;

/** every model the configured providers offer, qualified as the API names them */
export const remoteModels = (config: ProvidersConfig) =>
	config.providers.flatMap((provider) =>
		(provider.models?.allow ?? []).map((model) => qualify(provider.id, model)),
	);

/**
 * Where the chosen default chat model is recorded: a local one is tagged in the preset file
 * llama.cpp reads, and a remote one is named in the providers file, which the API reads first.
 * Anything no longer on offer falls back to the first local model.
 */
export const resolveDefaultModel = (
	requested: string | undefined,
	chatModels: string[],
	remote: string[],
) => {
	if (requested && remote.includes(requested))
		return { defaultModel: chatModels[0], defaultChatModel: requested };
	return {
		defaultModel:
			requested && chatModels.includes(requested) ? requested : chatModels[0],
		defaultChatModel: undefined,
	};
};
