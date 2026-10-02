import { getConnectivity } from "./connectivity";
import { composePull, composeServices, imageExists } from "./docker";
import { ImagesUnavailableError } from "./errors";
import logMessage from "./logMessage";
import type { OperationUpdate } from "./operationProtocol";
import pullProgress from "./pullProgress";

// a slow connection drops the odd download, and another attempt picks up where it left off, the
// layers that made it are kept
const PULL_ATTEMPTS = 3;
const RETRY_DELAY_MS = 5_000;

/**
 * Pulls the images a compose file uses, falling back to the ones already downloaded when the pull
 * fails. We don't try to work out from the error whether that was down to the connection: all
 * that matters is whether what's local is enough to carry on with.
 *
 * Returns whether the images were updated, throws if any are missing.
 */
export default async function* (
	composeFile: string,
	env: Record<string, string>,
	ignoreBuildable = false,
): AsyncGenerator<OperationUpdate, boolean> {
	for (let attempt = 1; ; attempt++) {
		if (yield* pullProgress(composePull(composeFile, env, ignoreBuildable)))
			return true;
		// offline every attempt fails straight away, so there's no point
		if (attempt == PULL_ATTEMPTS || (await getConnectivity()) == "offline")
			break;
		yield logMessage(
			`retrying the Docker image download (attempt ${attempt + 1} of ${PULL_ATTEMPTS})...`,
		);
		await Bun.sleep(RETRY_DELAY_MS);
	}
	const services = await composeServices(composeFile, env);
	// images which get built are never pulled, so they aren't expected to be here yet
	const images = new Set(
		Object.values(services)
			.filter((service) => !service.build && service.image)
			.map((service) => service.image!),
	);
	const missing: string[] = [];
	for (const image of images)
		if (!(await imageExists(image))) missing.push(image);
	if (missing.length) throw new ImagesUnavailableError(missing);
	return false;
}
