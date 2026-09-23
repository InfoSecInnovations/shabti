import { getCompatibleReleases } from "./listCompatibleVersions";

// every version we offer comes from a shabti-components.json, whether fetched or cached, so a
// version that isn't in there is a legacy one whose images are tagged with the version itself
export default async (version: string) =>
	(await getCompatibleReleases()).releases.find(
		(release) => release.version == version,
	);
