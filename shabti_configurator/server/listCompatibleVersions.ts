import { Octokit } from "octokit";
import semver from "semver";
import compatibility from "../compatibility.json";
import packageJson from "../package.json";

// Releases declare the oldest configurator that can install them, and this configurator declares the
// oldest release it can install. Keeping the second half here rather than as a ceiling in each
// release's shabti-components.json is what makes it settable at all: a published release asset can't
// be changed after the fact.
const supportsRelease = (version: string) => {
	try {
		const coerced = semver.coerce(version, { loose: true });
		// legacy versions we can't parse are left to the rest of the filter
		return coerced ? semver.gte(coerced, compatibility.minShabtiVersion) : true;
	} catch {
		return true;
	}
};

export default async () => {
	const octokit = new Octokit();
	const releases = await octokit
		.paginate(octokit.rest.repos.listReleases, {
			owner: "InfoSecInnovations",
			repo: "shabti",
			per_page: 100,
			headers: {
				"X-GitHub-Api-Version": "2026-03-10",
			},
		})
		.catch((rej) => {
			// TODO: filter by error type
			// if this failed, we might be operating offline
			// Shabti should still be able to run offline
			return undefined;
		});
	if (!releases) return;
	const componentsAssets = releases
		.map((release) =>
			release.assets.filter((asset) => asset.name == "shabti-components.json"),
		)
		.flat();
	const configuratorVersion = packageJson.version;
	return await Promise.all(
		componentsAssets.map((asset) =>
			fetch(asset.url, {
				headers: { Accept: "application/octet-stream" },
			}).then((res) => res.json() as any),
		),
	).then((releases) =>
		releases
			.filter(
				(release) =>
					semver.gte(configuratorVersion, release.configuratorMinVersion) &&
					supportsRelease(release.version),
			)
			.sort((a, b) => {
				// sort prerelease versions after "stable"
				try {
					if (
						semver.prerelease(a.version, true)?.length &&
						!semver.prerelease(b.version, true)?.length
					)
						return 1;
					if (
						semver.prerelease(b.version, true)?.length &&
						!semver.prerelease(a.version, true)?.length
					)
						return -1;
				} catch {}
				return semver.rcompare(
					semver.coerce(a.version, { loose: true }) || "",
					semver.coerce(b.version, { loose: true }) || "",
					true,
				); // sort by highest first
			})
			.map((release) => release.version),
	);
};
