import { composePull, composeServices, imageExists } from "./docker";
import { ImagesUnavailableError } from "./errors";

/**
 * Pulls the images a compose file uses, falling back to the ones already downloaded when the pull
 * fails. We don't try to work out from the error whether that was down to the connection: all
 * that matters is whether what's local is enough to carry on with.
 *
 * Returns whether the images were updated, throws if any are missing.
 */
export default async (
	composeFile: string,
	env: Record<string, string>,
	ignoreBuildable = false,
) => {
	if (await composePull(composeFile, env, ignoreBuildable)) return true;
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
};
