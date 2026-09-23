import { getConnectivity } from "./connectivity";
import { composeBuild, composeServices, imageExists } from "./docker";
import { ImageBuildError } from "./errors";

/**
 * Builds the images for the local code, falling back to the last build when that fails offline.
 * Online a failed build is down to the code, so its own error is left to explain it.
 *
 * Returns whether the images were rebuilt, throws if any are missing.
 */
export default async (composeFile: string, env: Record<string, string>) => {
	try {
		await composeBuild(composeFile, env);
		return true;
	} catch (error) {
		if ((await getConnectivity()) != "offline") throw error;
	}
	const services = await composeServices(composeFile, env);
	const images = new Set(
		Object.values(services)
			.filter((service) => service.build && service.image)
			.map((service) => service.image!),
	);
	const missing: string[] = [];
	for (const image of images)
		if (!(await imageExists(image))) missing.push(image);
	if (missing.length) throw new ImageBuildError(missing);
	return false;
};
