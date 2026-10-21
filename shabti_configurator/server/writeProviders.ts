import crypto from "node:crypto";
import path from "node:path";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";

/** a model the admin knows more about than the provider's own listing says */
export interface ModelOverride {
	category?: "chat" | "embeddings";
	context_length?: number;
	label?: string;
	// what a prompt can ask it to think at, in the provider's own words, or false and true for a
	// model that can only be switched. an empty list offers nothing
	reasoning?: (boolean | string)[];
	reasoning_default?: boolean | string;
}

// whose key a request is made with: the admin's, the requesting user's own, or the user's when
// they've added one and otherwise the admin's. Users' keys need security to be fully enabled
export const CREDENTIAL_MODES = ["shared", "user", "shared_or_user"] as const;
export type CredentialMode = (typeof CREDENTIAL_MODES)[number];

// which request fields a provider takes how a model should think in. Left out, the API tells it
// from the provider's host
export const REASONING_FORMATS = [
	"effort",
	"llama_cpp",
	"openrouter",
	"thinking",
	"enable_thinking",
	"anthropic",
] as const;
export type ReasoningFormat = (typeof REASONING_FORMATS)[number];

/** an OpenAI-compatible API the API can send prompts to, as it reads it */
export interface ProviderConfig {
	id: string;
	name: string;
	base_url: string;
	headers?: Record<string, string>;
	credential_mode?: CredentialMode;
	user_id_field?: string;
	reasoning_format?: ReasoningFormat;
	// without allow every model the provider lists is offered, and an empty one offers none
	models?: { allow?: string[]; overrides?: Record<string, ModelOverride> };
}

export interface ProvidersConfig {
	providers: ProviderConfig[];
	// qualified as <provider>/<model>, so it can name a local model as well as a remote one
	default_chat_model?: string;
	// a provider's model to embed with, left out to embed with the local one
	embeddings?: { model: string; dimensions?: number };
}

// the compiled installer unzips docker_compose next to the working directory, so these stay
// relative to the cwd by default like getEnvPath does
const getDependenciesDir = (baseDir = path.resolve()) =>
	path.join(baseDir, "docker_compose", "docker_compose_dependencies");

export const getProvidersPath = (baseDir = path.resolve()) =>
	path.join(
		getDependenciesDir(baseDir),
		"shabti_config",
		"shabti-providers.json",
	);

/** mounted into the API container alone, at /run/secrets */
export const getSecretsDir = (baseDir = path.resolve()) =>
	path.join(getDependenciesDir(baseDir), "secrets");

export const getProviderKeysPath = (baseDir = path.resolve()) =>
	path.join(getSecretsDir(baseDir), "shabti_provider_keys.json");

export const getInstanceSaltPath = (baseDir = path.resolve()) =>
	path.join(getSecretsDir(baseDir), "shabti_instance_salt");

export const getSecretsKeyPath = (baseDir = path.resolve()) =>
	path.join(getSecretsDir(baseDir), "shabti_secrets_key.json");

/** each version of the key users' own provider keys are encrypted with, and the one in use */
export interface SecretsKey {
	current: number;
	keys: Record<string, string>;
}

const replaceFile = async (
	filePath: string,
	contents: string,
	mode: number,
) => {
	await mkdir(path.dirname(filePath), { recursive: true });
	// removed first because the mode only applies to a file being created, and recursive because
	// Docker creates a directory here if the containers were launched before the file existed
	await rm(filePath, { force: true, recursive: true });
	await writeFile(filePath, contents, { mode });
};

/**
 * Writes the providers the API can use, and their keys apart from them so that only the API
 * container can read the keys. A provider with no key is written without one, as a self-hosted
 * server often takes none.
 */
export default async (
	config: ProvidersConfig,
	keys: Record<string, string>,
	baseDir = path.resolve(),
) => {
	const providersPath = getProvidersPath(baseDir);
	const keysPath = getProviderKeysPath(baseDir);
	await replaceFile(
		providersPath,
		`${JSON.stringify(config, null, "\t")}\n`,
		0o644,
	);
	await replaceFile(keysPath, JSON.stringify(keys), 0o600);
	return { providersPath, keysPath };
};

/**
 * A per-installation salt for the hashed user ids sent to providers, so that a provider can't
 * match one user across installations.
 */
export const writeInstanceSalt = async (baseDir = path.resolve()) => {
	const saltPath = getInstanceSaltPath(baseDir);
	await replaceFile(saltPath, crypto.randomBytes(32).toString("hex"), 0o600);
	return saltPath;
};

// AES-256, as the API decrypts with
const newSecretsKey = () => crypto.randomBytes(32).toString("base64");

/** The key the API encrypts users' own provider keys with before they're stored in Keycloak. */
export const writeSecretsKey = async (baseDir = path.resolve()) => {
	const keyPath = getSecretsKeyPath(baseDir);
	const secretsKey: SecretsKey = { current: 1, keys: { 1: newSecretsKey() } };
	await replaceFile(keyPath, JSON.stringify(secretsKey), 0o600);
	return keyPath;
};

/**
 * Adds a version of the key that new keys are encrypted with, and returns it. The older versions
 * are kept so the keys they encrypted can still be read until they're re-encrypted.
 */
export const rotateSecretsKey = async (baseDir = path.resolve()) => {
	const keyPath = getSecretsKeyPath(baseDir);
	const existing = await readJson<SecretsKey>(keyPath, {
		current: 0,
		keys: {},
	});
	const current = Math.max(0, ...Object.keys(existing.keys).map(Number)) + 1;
	const secretsKey: SecretsKey = {
		current,
		keys: { ...existing.keys, [current]: newSecretsKey() },
	};
	await replaceFile(keyPath, JSON.stringify(secretsKey), 0o600);
	return current;
};

const readJson = async <T>(filePath: string, fallback: T): Promise<T> => {
	try {
		return JSON.parse(await readFile(filePath, "utf8")) as T;
	} catch (error) {
		// before the first install, or after an uninstall
		if ((error as NodeJS.ErrnoException).code == "ENOENT") return fallback;
		throw error;
	}
};

/** what writeProviders last wrote, or no providers at all */
export const readProviders = async (baseDir = path.resolve()) => ({
	config: await readJson<ProvidersConfig>(getProvidersPath(baseDir), {
		providers: [],
	}),
	keys: await readJson<Record<string, string>>(
		getProviderKeysPath(baseDir),
		{},
	),
});
