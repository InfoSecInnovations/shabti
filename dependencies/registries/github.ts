/**
 * Which builds of a per-build image are stable releases.
 *
 * llama.cpp publishes an image for every build, `server-cuda-b11669`, and cuts its stable releases on
 * GitHub, but stopped tagging images with them after v0.5.0. A build pin is what actually ran, so it is
 * kept, and the releases are asked which builds are stable. Without that every nightly reads as a stable
 * release, and a bare `deps:set` moves onto whatever was built last night.
 *
 * Each release carries a `nightly-tag.txt` naming the build it was cut from. Listing the tags is the only
 * call to the API, whose anonymous limit is 60 an hour; the assets are plain downloads.
 */

import type { Client } from "../http";
import { parse } from "../pep440";
import type { TagShape } from "../tag";
import type { Catalogue } from "../types";

/** images tagged by build, and the GitHub repository whose `v` releases say which builds are stable */
export const STABLE_RELEASES: Record<string, string> = {
	"ghcr.io/ggml-org/llama.cpp": "ggml-org/llama.cpp",
};

export type Stable = { name: string; prerelease: boolean };

const BUILD = /^b(\d+)$/;

/** every build a release was cut from, by build number */
export const stableBuilds = async (repo: string, client: Client) => {
	const response = await client.request(
		`https://api.github.com/repos/${repo}/git/matching-refs/tags/v`,
		{ headers: { accept: "application/vnd.github+json" } },
	);
	// no fallback: without the releases every build would be reported as stable again
	if (!response.ok)
		throw new Error(
			`GitHub returned ${response.status} listing the releases of ${repo}`,
		);
	const names = ((await response.json()) as { ref: string }[])
		.map(({ ref }) => ref.replace(/^refs\/tags\//, ""))
		// matching-refs is a prefix match, so a tag like `vulkan-fix` comes back too
		.filter((name) => parse(name));

	const builds = new Map<number, Stable>();
	await Promise.all(
		names.map(async (name) => {
			const asset = await client.request(
				`https://github.com/${repo}/releases/download/${name}/nightly-tag.txt`,
			);
			// a tag not released yet, or a release from before the asset existed
			if (asset.status === 404) return;
			if (!asset.ok)
				throw new Error(
					`GitHub returned ${asset.status} for the build of ${repo} ${name}`,
				);
			const build = BUILD.exec((await asset.text()).trim())?.[1];
			if (!build) throw new Error(`${repo} ${name} names no build`);
			builds.set(Number(build), {
				name,
				prerelease: parse(name)?.prerelease ?? false,
			});
		}),
	);
	return builds;
};

/**
 * The catalogue with only the builds a release was cut from counted as stable, and every other build a
 * prerelease. A release counts once its build has an image on the pin's template: they can lag the
 * release, and a stable with nothing to pull is nowhere to move to.
 */
export const withStableReleases = (
	catalogue: Catalogue,
	stable: Map<number, Stable>,
	shape: TagShape,
): Catalogue => {
	const releases = catalogue.releases.map((release) => {
		const cut = stable.get(Number(release.version.slice(1)));
		return cut
			? { ...release, name: cut.name, prerelease: cut.prerelease }
			: { ...release, prerelease: true };
	});
	// picked from the new list, since verdict() finds the latest by looking it up in the releases
	const latestStable = releases.findLast(({ prerelease }) => !prerelease);
	const newest = releases.at(-1);

	const notes = [...catalogue.notes];
	const [newestBuild, newestStable] =
		[...stable]
			.filter(([, { prerelease }]) => !prerelease)
			.sort(([a], [b]) => b - a)[0] ?? [];
	if (
		newestStable &&
		!releases.some(({ version }) => version === `b${newestBuild}`)
	)
		notes.push(
			`${newestStable.name} is b${newestBuild}, which has no ${shape.prefix}b${newestBuild}${shape.label} image yet`,
		);

	return {
		...catalogue,
		releases,
		latestStable,
		latestPrerelease: newest?.prerelease ? newest : undefined,
		notes,
	};
};
