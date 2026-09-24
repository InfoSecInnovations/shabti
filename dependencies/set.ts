/**
 * Sets third party dependencies to exact versions everywhere the repo pins them, then regenerates
 * whichever lockfiles that invalidated.
 *
 * bun ./dependencies/set.ts <name[@version]...> [--ecosystem <python|node|docker|system>] [--tag] [--no-lock] [--json]
 *
 * A version is never compared against the one already pinned: moving backwards out of a bad release is
 * as legitimate as moving forwards, so the only question asked is whether the version exists. That check
 * is against the registry's whole version list rather than a probe for the one version, which is what
 * lets `1.7` be accepted for `1.7.0` - the same version under PEP 440 - while the canonical spelling is
 * what gets written, and what lets a miss suggest what was meant instead. A name given without a
 * version is the one case where the version is chosen for you, and it is chosen from the same
 * `latestStable` the report prints, so `deps` and a bare name can never disagree.
 *
 * The whole list is one run rather than several: the working tree is read once, one HTTP client serves
 * every lookup, and the lockfiles are regenerated once at the end however many dependencies moved.
 * That makes the batch atomic for the reason a single dependency already was - every name resolved and
 * every edit planned before anything is written - so one unknown name writes nothing at all. Every
 * refusal is collected rather than the first one thrown, because three typos are worth hearing about in
 * one run rather than three.
 *
 * `bun` and `uv` name the tools installed on this machine. Setting one runs its own upgrade - `bun
 * upgrade`, Bun's installer for any other version, `uv self update` - before any file is written.
 * Setting bun also sets the oven/bun image to the same version.
 */

import { Command, Option } from "commander";
import semver from "semver";
import { normalise } from "../versioning/manifest";
import { type Registry, registry, unsupported } from "./catalogue";
import { type Options, client } from "./http";
import { commandFor, lockActionsFor, regenerate } from "./lock";
import { compare, parse } from "./pep440";
import { group, readPins } from "./read";
import { type Tagged, byEcosystem, count, tabulate, where } from "./render";
import { type Edit, applyEdits, plannedEdits } from "./rewrite";
import { SYSTEM_TOOLS, systemTools, upgrade } from "./system";
import {
	ECOSYSTEMS,
	type Catalogue,
	type Dependency,
	type Ecosystem,
	type Release,
} from "./types";

/** what one dependency became */
export type Change = {
	name: string;
	ecosystem: Ecosystem;
	/** the versions that were pinned before, which may be more than one if the files disagreed */
	from: string[];
	to: string;
	files: string[];
	/** system only: the executable that was upgraded */
	executable?: string;
	warnings: string[];
};

export type Result = {
	changes: Change[];
	files: string[];
	/** the system tool upgrades that ran */
	upgraded: string[];
	locked: string[];
	/** about the run rather than about any one dependency */
	warnings: string[];
};

/** a spec as the command line spells it, split */
export type Target = {
	name: string;
	/** absent when the spec named none, which means the latest stable release */
	version?: string;
};

/**
 * `name@version`, or a bare name meaning the latest. The split is at the last `@` and only past the
 * start, which is what tells `@types/semver@7.8.0` and a bare `@types/bun` apart. An `@` with nothing
 * after it is a typo rather than either, so it is refused.
 */
export const parseSpec = (spec: string): Target => {
	const at = spec.lastIndexOf("@");
	if (at <= 0) {
		if (!spec) throw new Error("a dependency was named as an empty string");
		return { name: spec };
	}
	const version = spec.slice(at + 1);
	if (!version) throw new Error(`${spec} names no version after its @`);
	return { name: spec.slice(0, at), version };
};

/** the dependency this name refers to, refusing rather than guessing when it refers to more than one */
export const find = (
	dependencies: Dependency[],
	name: string,
	ecosystem?: Ecosystem,
) => {
	const wanted = name.toLowerCase();
	const matches = dependencies.filter(
		(dependency) =>
			(!ecosystem || dependency.ecosystem === ecosystem) &&
			(dependency.id === name ||
				dependency.name === name ||
				dependency.id.toLowerCase() === wanted ||
				(dependency.ecosystem === "python" &&
					dependency.id === normalise(name))),
	);
	if (!matches.length) throw new Error(`nothing in this repo pins ${name}`);
	if (matches.length > 1)
		throw new Error(
			`${name} is pinned in more than one ecosystem (${matches
				.map((match) => match.ecosystem)
				.join(", ")}), so pass --ecosystem`,
		);
	return matches[0] as Dependency;
};

const distance = (a: string, b: string) => {
	let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
	for (let i = 1; i <= a.length; i++) {
		const row = [i];
		for (let j = 1; j <= b.length; j++)
			row[j] = Math.min(
				(previous[j] as number) + 1,
				(row[j - 1] as number) + 1,
				(previous[j - 1] as number) + (a[i - 1] === b[j - 1] ? 0 : 1),
			);
		previous = row;
	}
	return previous[b.length] as number;
};

/** the same version however it was spelled, since 1.7 and 1.7.0 are one version under PEP 440 */
const matching = (
	catalogue: Catalogue,
	ecosystem: Ecosystem,
	requested: string,
): Release | undefined => {
	const exact = catalogue.releases.find(
		(release) => release.version === requested || release.raw === requested,
	);
	if (exact) return exact;
	if (ecosystem === "node") {
		const valid = semver.valid(requested);
		return valid
			? catalogue.releases.find((release) => release.version === valid)
			: undefined;
	}
	const wanted = parse(requested);
	if (!wanted) return undefined;
	return catalogue.releases.find((release) => {
		const have = parse(release.version);
		return have && compare(have, wanted) === 0;
	});
};

const suggestions = (catalogue: Catalogue, requested: string) =>
	[
		...new Set([
			...catalogue.releases
				.map((release) => release.version)
				.sort((a, b) => distance(a, requested) - distance(b, requested))
				.slice(0, 3),
			...(catalogue.latestStable ? [catalogue.latestStable.version] : []),
		]),
	].slice(0, 4);

const reasonOf = (error: unknown) =>
	error instanceof Error ? error.message : String(error);

/** one spec resolved to the version to write, or the reason it cannot be */
type Resolution =
	| {
			ok: true;
			dependency: Dependency;
			to: string;
			/** the registry's latest stable, which decides how bun upgrades */
			latest?: string;
			warnings: string[];
	  }
	| { ok: false; reason: string };

/**
 * What one spec resolves to. Every failure comes back as a reason rather than a throw, which is only so
 * they can all be reported together: any one of them still fails the whole run, because refusing to
 * write is the safe failure here, unlike in the report.
 */
const resolve = async (
	dependencies: Dependency[],
	spec: string,
	{
		ecosystem,
		literalTag,
		look,
	}: { ecosystem?: Ecosystem; literalTag?: boolean; look: Registry },
): Promise<Resolution> => {
	try {
		const { name, version } = parseSpec(spec);
		const dependency = find(dependencies, name, ecosystem);

		// a tag with no version in it has nothing to validate against, so --tag is taken on trust
		if (literalTag && dependency.ecosystem !== "system") {
			if (!version)
				return {
					ok: false,
					reason: `${dependency.name} was given no version, and --tag has no tag to write without one`,
				};
			return {
				ok: true,
				dependency,
				to: version,
				warnings: [`wrote the tag ${version} without checking it exists`],
			};
		}

		const reason = unsupported(dependency);
		if (reason)
			return { ok: false, reason: `cannot set ${dependency.name}: ${reason}` };
		const catalogue = await look(dependency);
		const latest = catalogue.latestStable?.version;

		if (!version) {
			// the same release the report calls the latest, so it is already neither a prerelease nor
			// withdrawn; a package with only prereleases has none, and choosing one of those is a decision
			if (!latest)
				return {
					ok: false,
					reason: `${dependency.name} has no stable release to move to, so name the version you want`,
				};
			return { ok: true, dependency, to: latest, latest, warnings: [] };
		}

		const release = matching(catalogue, dependency.ecosystem, version);
		if (!release)
			return {
				ok: false,
				reason: `${dependency.name} ${version} is not published; did you mean ${suggestions(catalogue, version).join(", ")}?`,
			};
		return {
			ok: true,
			dependency,
			// the registry's own spelling, so the manifest gets the canonical one
			to: release.version,
			latest,
			warnings: release.withdrawn
				? [`${release.version} is ${release.withdrawn}`]
				: [],
		};
	} catch (error) {
		return { ok: false, reason: reasonOf(error) };
	}
};

type Planned = { resolution: Extract<Resolution, { ok: true }>; edits: Edit[] };

/**
 * `image@version` for every image that follows a system tool being set, when the repo pins it. Asked
 * for even when the tool is already at the version, which is how an image left behind by an upgrade
 * made outside this command is brought back into line.
 */
const followingImages = (
	dependencies: Dependency[],
	resolutions: Resolution[],
) =>
	resolutions.flatMap((resolution) => {
		if (!resolution.ok || resolution.dependency.ecosystem !== "system")
			return [];
		const image = SYSTEM_TOOLS[resolution.dependency.id]?.image;
		const pinned = dependencies.some(
			(dependency) =>
				dependency.ecosystem === "docker" && dependency.id === image,
		);
		return image && pinned ? [`${image}@${resolution.to}`] : [];
	});

export const set = async ({
	repoDir,
	specs,
	ecosystem,
	literalTag,
	lock = true,
	...options
}: Options & {
	repoDir: string;
	/** each `name@version`, or a bare name for the latest */
	specs: string[];
	ecosystem?: Ecosystem;
	literalTag?: boolean;
	lock?: boolean;
}): Promise<Result> => {
	if (!specs.length) throw new Error("name at least one dependency to set");
	const dependencies = [
		...group(await readPins(repoDir)),
		...(await systemTools(repoDir)),
	];
	// one client for the whole run, so its memo, its concurrency limit and its per host serialisation
	// hold across every lookup instead of being rebuilt for each dependency
	const look = registry(client(options), { resolveLatest: false });
	// every lookup settles before any is judged, so one failure cannot abandon the others in flight
	const resolutions = await Promise.all(
		specs.map((spec) =>
			resolve(dependencies, spec, { ecosystem, literalTag, look }),
		),
	);

	// an image that follows a system tool is set to the same version, and checked like any other spec
	resolutions.push(
		...(await Promise.all(
			followingImages(dependencies, resolutions).map((spec) =>
				resolve(dependencies, spec, { ecosystem: "docker", look }),
			),
		)),
	);

	const refused = resolutions.flatMap((resolution) =>
		resolution.ok ? [] : [resolution.reason],
	);

	// the same dependency twice is one edit when both agree, and a refusal when they do not: writing
	// either version would be silently ignoring the other
	const wanted = new Map<string, Extract<Resolution, { ok: true }>>();
	for (const resolution of resolutions) {
		if (!resolution.ok) continue;
		const { dependency } = resolution;
		const existing = wanted.get(`${dependency.ecosystem}:${dependency.id}`);
		if (existing && existing.to !== resolution.to)
			refused.push(
				`${dependency.name} was named twice, at ${existing.to} and ${resolution.to}`,
			);
		else wanted.set(`${dependency.ecosystem}:${dependency.id}`, resolution);
	}

	// planned for every dependency before any is written, and a refusal from any one of them fails the
	// run: a partial write would leave exactly the disagreement between files this command removes
	const planned = [...wanted.values()].map((resolution): Planned | null => {
		try {
			return {
				resolution,
				edits: plannedEdits(resolution.dependency.occurrences, resolution.to, {
					literalTag,
				}),
			};
		} catch (error) {
			refused.push(reasonOf(error));
			return null;
		}
	});
	if (refused.length) throw new Error(refused.join("\n"));

	const plans = planned.filter((plan): plan is Planned => !!plan);
	// before anything is written, so a failed upgrade leaves the tree alone, and so the lockfiles below
	// are regenerated by the tools they are being moved to
	const upgraded = await upgrade(
		repoDir,
		plans
			.map(({ resolution }) => resolution)
			.filter(({ dependency }) => dependency.ecosystem === "system"),
	);
	const files = await applyEdits(
		repoDir,
		plans.flatMap((plan) => plan.edits),
	);
	// one round for the batch: a dozen node pins regenerate bun.lock once, not a dozen times
	const actions = await lockActionsFor(repoDir, files);
	const written = new Set(files);

	return {
		changes: plans.map(({ resolution, edits }) => ({
			name: resolution.dependency.name,
			ecosystem: resolution.dependency.ecosystem,
			from: resolution.dependency.versions,
			to: resolution.to,
			files: [...new Set(edits.map((edit) => edit.file))]
				.filter((file) => written.has(file))
				.sort(),
			executable: resolution.dependency.executable,
			warnings: resolution.warnings,
		})),
		files,
		upgraded: upgraded.ran,
		locked: lock ? await regenerate(repoDir, actions) : actions.map(commandFor),
		warnings: [
			...upgraded.warnings,
			...(!lock && actions.length
				? [`did not run: ${actions.map(commandFor).join(", ")}`]
				: []),
		],
	};
};

/** alphabetical within each ecosystem, as `check` lists them, so a name is where you look for it */
const byName = (a: Change, b: Change) => a.name.localeCompare(b.name);

const rightOf = (change: Change) => {
	if (change.ecosystem === "system")
		return change.from.includes(change.to)
			? "already set"
			: (change.executable ?? "-");
	// a pin already at the version asked for is written nowhere, which is the whole row
	return change.files.length ? where(change.files) : "already set";
};

export const render = (
	result: Result,
	{ lock = true }: { lock?: boolean } = {},
) => {
	const lines = tabulate([
		{
			heading: "setting",
			groups: byEcosystem(
				[...result.changes].sort(byName).map(
					(change): Tagged => ({
						ecosystem: change.ecosystem,
						left: change.name,
						middle: `${change.from.join(", ") || "-"} -> ${change.to}`,
						right: rightOf(change),
					}),
				),
			),
		},
	]);

	return [
		...lines,
		"",
		`${result.changes.length} set, wrote ${count(result.files.length, "file")}`,
		...(result.upgraded.length ? [`ran ${result.upgraded.join(", ")}`] : []),
		// what --no-lock leaves behind is a command to run, not a thing that happened
		...(result.locked.length
			? [
					lock
						? `ran ${result.locked.join(", ")}`
						: `to lock, run: ${result.locked.join(", ")}`,
				]
			: []),
	].join("\n");
};

if (import.meta.main) {
	const command = new Command()
		.argument(
			"<specs...>",
			"the dependencies to set, each as name@version, or name alone for the latest; bun and uv are the installed tools",
		)
		.addOption(
			new Option(
				"--ecosystem <ecosystem>",
				"which one, when the same name is pinned in more than one",
			).choices([...ECOSYSTEMS]),
		)
		.option(
			"--tag",
			"write the version as the whole image tag, for a tag with no version to substitute into",
		)
		.option("--no-lock", "rewrite the pins but do not regenerate any lockfile")
		.option("--json", "emit the result as one line of JSON")
		.parse();
	const options = command.opts();

	const result = await set({
		repoDir: process.cwd(),
		specs: command.args,
		ecosystem: options.ecosystem,
		literalTag: options.tag,
		lock: options.lock,
	});

	if (options.json) console.log(JSON.stringify(result));
	else console.log(render(result, { lock: options.lock }));

	// named, because a warning about one of five dependencies has to say which
	for (const change of result.changes)
		for (const warning of change.warnings)
			console.error(`${change.name}: ${warning}`);
	for (const warning of result.warnings) console.error(warning);
}
