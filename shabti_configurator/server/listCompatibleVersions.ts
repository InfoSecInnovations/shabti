import path from "node:path";
import { Octokit } from "octokit";
import semver from "semver";
import compatibility from "../compatibility.json";
import packageJson from "../package.json";
import { imageExists } from "./docker";
import getEnvs from "./getEnvs";

/** the shabti-components.json asset each release publishes */
export interface ShabtiRelease {
	version: string;
	configuratorMinVersion: string;
	apiVersion?: string;
	webVersion?: string;
}

const REQUEST_TIMEOUT_MS = 10_000;
// GitHub only allows 60 unauthenticated requests an hour and every release is a request of its
// own, so a successful listing is reused across page renders
const ONLINE_CACHE_MS = 10 * 60_000;
// a failure is retried sooner, so reconnecting shows up without restarting the configurator
const OFFLINE_CACHE_MS = 30_000;

// next to the extracted compose files rather than inside docker_compose, so neither the zip
// extraction nor an uninstall touches it
export const getReleasesCachePath = () => path.resolve("shabti-releases.json");

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

const compatible = (releases: ShabtiRelease[]) =>
	releases
		.filter(
			(release) =>
				semver.gte(packageJson.version, release.configuratorMinVersion) &&
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
		});

const fetchReleases = async () => {
	const signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
	const octokit = new Octokit({ request: { signal } });
	const releases = await octokit.paginate(octokit.rest.repos.listReleases, {
		owner: "InfoSecInnovations",
		repo: "shabti",
		per_page: 100,
		headers: {
			"X-GitHub-Api-Version": "2026-03-10",
		},
	});
	const componentsAssets = releases.flatMap((release) =>
		release.assets.filter((asset) => asset.name == "shabti-components.json"),
	);
	return await Promise.all(
		componentsAssets.map(async (asset) => {
			const res = await fetch(asset.url, {
				headers: { Accept: "application/octet-stream" },
				signal,
			});
			if (!res.ok)
				throw new Error(
					`fetching ${asset.url} failed with status ${res.status}`,
				);
			return (await res.json()) as ShabtiRelease;
		}),
	);
};

const readCache = async (): Promise<ShabtiRelease[]> => {
	try {
		return await Bun.file(getReleasesCachePath()).json();
	} catch {
		return []; // missing or corrupt, either way there's nothing to offer
	}
};

const imagesExist = async (release: ShabtiRelease) =>
	(
		await Promise.all([
			imageExists(
				`infosecinnovations/shabti:${release.apiVersion || release.version}`,
			),
			imageExists(
				`infosecinnovations/shabti-web:${release.webVersion || release.version}`,
			),
		])
	).every(Boolean);

// the installed version can always be reinstalled from its own images, even if it was installed
// before there was a cache to find it in
const installedRelease = async (): Promise<ShabtiRelease | undefined> => {
	const envs = await getEnvs();
	if (!envs.SHABTI_VERSION || envs.SHABTI_LOCAL_VERSION == "True") return;
	return {
		version: envs.SHABTI_VERSION,
		configuratorMinVersion: packageJson.version,
		apiVersion: envs.SHABTI_API_VERSION,
		webVersion: envs.SHABTI_WEB_VERSION,
	};
};

// without GitHub we can only offer the versions whose images are already downloaded
const offlineReleases = async () => {
	const cached = await readCache();
	const installed = await installedRelease();
	const candidates =
		installed && !cached.some((release) => release.version == installed.version)
			? [...cached, installed]
			: cached;
	const available = await Promise.all(candidates.map(imagesExist));
	return compatible(candidates.filter((_, i) => available[i]));
};

const load = async () => {
	try {
		const releases = await fetchReleases();
		await Bun.write(
			getReleasesCachePath(),
			JSON.stringify(releases, null, "\t"),
		);
		return { releases: compatible(releases), fromCache: false };
	} catch (error) {
		console.error("Couldn't list the Shabti releases on GitHub:", error);
		return { releases: await offlineReleases(), fromCache: true };
	}
};

let cached:
	| {
			at: number;
			result: Promise<{ releases: ShabtiRelease[]; fromCache: boolean }>;
	  }
	| undefined;

export const getCompatibleReleases = async () => {
	if (cached) {
		const { fromCache } = await cached.result;
		const maxAge = fromCache ? OFFLINE_CACHE_MS : ONLINE_CACHE_MS;
		if (Date.now() - cached.at <= maxAge) return cached.result;
	}
	cached = { at: Date.now(), result: load() };
	return cached.result;
};

export const resetCompatibleReleases = () => {
	cached = undefined;
};

export default async () =>
	(await getCompatibleReleases()).releases.map((release) => release.version);
