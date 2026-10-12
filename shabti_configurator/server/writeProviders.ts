import crypto from "node:crypto";
import path from "node:path";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";

/** a model the admin knows more about than the provider's own listing says */
export interface ModelOverride {
	category?: "chat" | "embeddings";
	context_length?: number;
	label?: string;
}

/** an OpenAI-compatible API the API can send prompts to, as it reads it */
export interface ProviderConfig {
	id: string;
	name: string;
	base_url: string;
	headers?: Record<string, string>;
	credential_mode?: "shared";
	user_id_field?: string;
	models?: { allow?: string[]; overrides?: Record<string, ModelOverride> };
}

export interface ProvidersConfig {
	providers: ProviderConfig[];
	// qualified as <provider>/<model>, so it can name a local model as well as a remote one
	default_chat_model?: string;
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
