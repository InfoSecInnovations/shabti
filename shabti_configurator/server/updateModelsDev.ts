import path from "node:path";
import { mkdir, rename, writeFile } from "node:fs/promises";

const SOURCE = "https://models.dev/api.json";
// the full catalogue is a few megabytes, which a slow connection needs a while for
const TIMEOUT_MS = 30_000;

// only what the API reads: a remote model's context length, when its provider's listing doesn't say
const MODEL_KEYS = ["limit"] as const;

type Catalogue = Record<
	string,
	{ api?: string; models?: Record<string, Record<string, unknown>> }
>;

/** next to the providers, in the directory the API mounts at /opt/shabti_config */
export const getModelsDevPath = (baseDir = path.resolve()) =>
	path.join(
		baseDir,
		"docker_compose",
		"docker_compose_dependencies",
		"shabti_config",
		"models-dev.json",
	);

export const trim = (catalogue: Catalogue) => ({
	providers: Object.fromEntries(
		Object.entries(catalogue).map(([providerId, provider]) => [
			providerId,
			{
				...(provider.api ? { api: provider.api } : {}),
				models: Object.fromEntries(
					Object.entries(provider.models ?? {}).map(([modelId, model]) => [
						modelId,
						Object.fromEntries(
							MODEL_KEYS.filter((key) => key in model).map((key) => [
								key,
								model[key],
							]),
						),
					]),
				),
			},
		]),
	),
});

/**
 * Fetches what models.dev knows about each model, for the API to fill in what a remote provider's
 * model list leaves out. Throws if it can't, leaving the copy from the last launch in place: the
 * API falls back on defaults without one, so being offline is no reason to stop.
 */
export default async (baseDir = path.resolve()) => {
	const response = await fetch(SOURCE, {
		signal: AbortSignal.timeout(TIMEOUT_MS),
	});
	if (!response.ok)
		throw new Error(`${SOURCE} answered with status ${response.status}`);
	const snapshot = trim((await response.json()) as Catalogue);
	const snapshotPath = getModelsDevPath(baseDir);
	await mkdir(path.dirname(snapshotPath), { recursive: true });
	// renamed into place, so a running API never reads a half written file
	const partialPath = `${snapshotPath}.partial`;
	await writeFile(partialPath, JSON.stringify(snapshot));
	await rename(partialPath, snapshotPath);
	return snapshotPath;
};
