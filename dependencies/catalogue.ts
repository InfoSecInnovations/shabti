/**
 * Asking the right registry about each dependency, and judging the answer.
 *
 * A lookup that fails becomes a failed row rather than a failed run: a report on seventy dependencies
 * that omits one because a registry hiccuped is far more useful than no report, and it follows the
 * precedent the earlier prototype set, where an unreachable registry produced a warning rather than
 * aborting a release. `set` is the opposite case and does not use this - refusing to write is the safe
 * failure there, so it lets the error through.
 */

import { type Client, type Options, client } from "./http";
import { npmCatalogue } from "./registries/npm";
import { ociCatalogue } from "./registries/oci";
import { pypiCatalogue } from "./registries/pypi";
import { type TagShape, shapeOf } from "./tag";
import type {
	Catalogue,
	Dependency,
	Freshness,
	ImageRef,
	Verdict,
} from "./types";

/** how a dependency is looked up */
export type Registry = (dependency: Dependency) => Promise<Catalogue>;

/** an untagged image is compared against plain version tags, which is what `latest` would resolve to */
const UNTAGGED: TagShape = {
	prefix: "",
	label: "",
	slot: "version",
	value: "",
};

const imageOf = (dependency: Dependency): ImageRef | undefined =>
	dependency.occurrences.find((pin) => pin.image)?.image;

/** why this dependency cannot be looked up at all, or null when it can */
export const unsupported = (dependency: Dependency): string | null => {
	if (dependency.precision === "alias")
		return "aliased, so its version is decided by another package";
	if (dependency.ecosystem !== "docker") return null;
	const image = imageOf(dependency);
	if (!image) return "no image reference to read";
	if (image.digest) return "pinned by digest";
	if (image.tag && !shapeOf(image.tag))
		return `the tag "${image.tag}" names no version`;
	return null;
};

export const registry =
	(deps: Client, options: { resolveLatest?: boolean } = {}): Registry =>
	(dependency) =>
		deps.once(`${dependency.ecosystem}:${dependency.id}`, async () => {
			switch (dependency.ecosystem) {
				case "python":
					return pypiCatalogue(dependency.id, deps);
				case "node":
					return npmCatalogue(dependency.id, deps);
				case "docker": {
					const image = imageOf(dependency);
					if (!image)
						throw new Error(`${dependency.id} has no image reference`);
					const shape = image.tag ? shapeOf(image.tag) : UNTAGGED;
					if (!shape)
						throw new Error(
							`${image.tag} names no version, so nothing compares`,
						);
					const catalogue = await ociCatalogue(image, shape, deps, options);
					return image.tag
						? catalogue
						: {
								...catalogue,
								notes: [
									"no tag, so docker implies latest and nothing here is pinned",
									...catalogue.notes,
								],
							};
				}
			}
		});

const positionOf = (catalogue: Catalogue, version: string) =>
	catalogue.releases.findIndex((release) => release.version === version);

/**
 * How the pin stands against what is published. A diverged dependency is judged by its lowest pin, so
 * `behind` is true when any occurrence is, and `behindBy` counts from the furthest behind.
 */
export const verdict = (
	dependency: Dependency,
	catalogue: Catalogue,
): Verdict => {
	const positions = dependency.versions.map((version) => ({
		version,
		at: positionOf(catalogue, version),
	}));
	const known = positions.filter(({ at }) => at >= 0);
	const unknownPin = positions.some(({ at }) => at < 0) || undefined;
	const withdrawn = known
		.map(({ at }) => catalogue.releases[at]?.withdrawn)
		.find((reason) => !!reason);

	const latest = catalogue.latestStable;
	// found by version rather than by object identity: a registry that assembles its own catalogue can
	// hand back an equal but distinct release, and indexOf would then silently find nothing at all
	const latestAt = latest ? positionOf(catalogue, latest.version) : -1;
	if (latestAt < 0 || !known.length)
		return { behind: false, unknownPin, pinWithdrawn: withdrawn };

	const lowest = Math.min(...known.map(({ at }) => at));
	const behind = lowest < latestAt;
	return {
		behind,
		behindBy: behind
			? catalogue.releases.filter(
					(release, at) =>
						at > lowest &&
						at <= latestAt &&
						!release.prerelease &&
						!release.withdrawn,
				).length
			: undefined,
		unknownPin,
		pinWithdrawn: withdrawn,
	};
};

const reasonOf = (error: unknown) =>
	error instanceof Error ? error.message : String(error);

/** every dependency, concurrently, never throwing */
export const freshness = async (
	dependencies: Dependency[],
	options: Options & { resolveLatest?: boolean } = {},
): Promise<Freshness[]> => {
	const look = registry(client(options), options);
	return Promise.all(
		dependencies.map(async (dependency): Promise<Freshness> => {
			const reason = unsupported(dependency);
			if (reason) return { status: "unsupported", dependency, reason };
			try {
				const catalogue = await look(dependency);
				return {
					status: "ok",
					dependency,
					catalogue,
					verdict: verdict(dependency, catalogue),
				};
			} catch (error) {
				return { status: "failed", dependency, reason: reasonOf(error) };
			}
		}),
	);
};
