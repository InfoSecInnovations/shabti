import { HTTPException } from "hono/http-exception";
import { LOCAL_PROVIDER, providerId, qualify } from "../providerIds";
import type { ProviderConfig, ProvidersConfig } from "./writeProviders";

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
	| { baseUrl: string; models: string[] }
	| { error: string; hint?: string };

// most OpenAI-compatible APIs are versioned, and a base URL without the version is the usual mistake
const versionHint = (baseUrl: string) =>
	/\/v\d+[a-z]*$/.test(baseUrl)
		? undefined
		: "Most providers' base URLs end in a version, such as /v1.";

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
		response = await fetch(`${baseUrl}/models`, {
			headers: {
				...headers,
				...(options.key ? { Authorization: `Bearer ${options.key}` } : {}),
			},
			// the key would follow a redirect wherever it pointed
			redirect: "manual",
			signal: AbortSignal.timeout(TIMEOUT_MS),
		});
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
	try {
		const body = (await response.json()) as { data: { id: unknown }[] };
		const models = body.data
			.map((model) => model.id)
			.filter((id): id is string => typeof id == "string")
			.sort((a, b) => a.localeCompare(b));
		return { baseUrl, models };
	} catch {
		return {
			error: `${baseUrl}/models didn't list any models.`,
			hint: versionHint(baseUrl),
		};
	}
};

/**
 * The provider a form adds, from the fields ProviderFieldset renders, or undefined when they
 * were left empty. Checked before an install or a change touches anything, so a mistake leaves
 * things as they were.
 */
export const providerFromForm = (
	form: FormData,
	existingIds: string[],
): { provider: ProviderConfig; key?: string } | undefined => {
	const field = (name: string) => form.get(name)?.toString().trim() ?? "";
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
	const models = form.getAll("provider_model").map((v) => v.toString());
	if (!models.length)
		throw invalid(
			`Test the connection to ${name} and choose at least one of its models.`,
		);
	const headers = parseHeaders(field("provider_headers"));
	const key = field("provider_key");
	return {
		provider: {
			id,
			name,
			base_url: normaliseBaseUrl(baseUrl),
			...(Object.keys(headers).length ? { headers } : {}),
			models: { allow: models },
		},
		...(key ? { key } : {}),
	};
};

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
