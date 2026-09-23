import path from "node:path";
import getModelsConfig from "../getModelsConfig";
import { isOnline } from "./connectivity";
import {
	MODELS_VOLUME,
	composeServices,
	imageExists,
	removeContainer,
	startModelProbe,
	volumeExists,
} from "./docker";
import { LlamaCppUnavailableError, ModelsUnavailableError } from "./errors";

const LLAMA_CPP_URL = "http://localhost:11434";
const PROBE_NAME = "shabti-model-probe";
const PROBE_PORT = 11435;
const PROBE_ATTEMPTS = 60;
const PROBE_DELAY_MS = 500;
// starting a probe takes a few seconds, and the page asks for this once per form
const CACHE_MS = 30_000;

const loaderComposeFile = path.join(
	"docker_compose",
	"docker-compose-download-model.yml",
);

// llama.cpp's router lists every file in its cache as a model of its own, with the repository and
// quantization as the id, which is exactly what the catalogue's hf values are
const cachedModelIds = async (url: string) => {
	const res = await fetch(`${url}/models`, {
		signal: AbortSignal.timeout(2000),
	});
	if (!res.ok)
		throw new Error(`listing models failed with status ${res.status}`);
	const body = (await res.json()) as {
		data: { id: string; source?: string }[];
	};
	return new Set(
		body.data
			.filter((model) => model.source == "cache")
			.map((model) => model.id),
	);
};

const probe = async () => {
	try {
		return await cachedModelIds(LLAMA_CPP_URL);
	} catch {} // Shabti isn't running, so we have to ask a llama.cpp of our own
	// checked first as docker run would otherwise create an empty volume
	if (!(await volumeExists(MODELS_VOLUME))) return new Set<string>();
	const image = (await composeServices(loaderComposeFile))["llama-cpp"]?.image;
	// without the image, nothing could run the models anyway
	if (!image || !(await imageExists(image))) return new Set<string>();
	await removeContainer(PROBE_NAME); // in case a previous probe didn't get cleaned up
	await startModelProbe(image, PROBE_NAME, PROBE_PORT);
	try {
		for (let attempt = 0; attempt < PROBE_ATTEMPTS; attempt++) {
			try {
				return await cachedModelIds(`http://localhost:${PROBE_PORT}`);
			} catch {} // still starting up
			await Bun.sleep(PROBE_DELAY_MS);
		}
		throw new LlamaCppUnavailableError(
			(PROBE_ATTEMPTS * PROBE_DELAY_MS) / 1000,
		);
	} finally {
		await removeContainer(PROBE_NAME);
	}
};

let cached: { at: number; result: Promise<Set<string>> } | undefined;

/** the hf ids of every model in the models volume */
const listDownloadedModels = () => {
	if (!cached || Date.now() - cached.at > CACHE_MS) {
		const result = probe();
		cached = { at: Date.now(), result };
		// a failed probe is worth retrying straight away
		result.catch(() => {
			if (cached?.result == result) cached = undefined;
		});
	}
	return cached.result;
};

export default listDownloadedModels;

export const resetDownloadedModels = () => {
	cached = undefined;
};

/**
 * Checks that models which can't be downloaded right now are already there. Returns whether we're
 * online, as that decides whether the caller should download anything.
 */
export const requireModels = async (modelNames: string[]) => {
	if (await isOnline()) return true;
	const [models, downloaded] = await Promise.all([
		getModelsConfig(),
		listDownloadedModels(),
	]);
	const missing = modelNames.filter(
		(name) => !downloaded.has(models[name]?.hf),
	);
	if (missing.length) throw new ModelsUnavailableError(missing);
	return false;
};
