/**
 * Rewrites every range pin as the exact version it currently resolves to, so the repo can move to exact
 * pins without changing anything that is installed today.
 *
 * bun ./dependencies/migrate.ts [--dry-run] [--no-lock] [--json]
 *
 * This is the one place in the tool that reads a lockfile, and only ever to answer "what is installed
 * right now". Everywhere else, reading one would mean reporting transitive dependencies, which is the
 * thing this must not do.
 *
 * A dependency whose occurrences resolve to different versions is reported and left alone. Picking one
 * for it would be choosing an upgrade for some file and a downgrade for another, which is a decision for
 * a person and for `set`.
 *
 * exceptions.json is honoured pin by pin rather than dependency by dependency, because pydantic floats
 * in the library that publishes it and is pinned in the app that installs it. An exempt occurrence is
 * dropped before anything is planned, so it cannot be written: an exact pin forced on a published
 * library is the one thing that file exists to prevent.
 */

import path from "node:path";
import { Command } from "commander";
import { normalise } from "../versioning/manifest";
import { type Exception, exemption, readExceptions } from "./exceptions";
import { commandFor, lockActionsFor, regenerate } from "./lock";
import { group, readPins } from "./read";
import { type Tagged, byEcosystem, count, tabulate, where } from "./render";
import { applyEdits, plannedEdits } from "./rewrite";
import type { Dependency, Ecosystem, Pin } from "./types";

export type Migration = {
	name: string;
	ecosystem: Ecosystem;
	from: string[];
	to: string;
	files: string[];
};

/** why a dependency was left alone: nothing is installed to read, or the places disagree */
export type Skip = {
	name: string;
	ecosystem: Ecosystem;
	kind: "uninstalled" | "diverged";
	from: string[];
	reason: string;
};

/** exempt, and so listed without a version: what it would be bumped to is what must not happen */
export type Floating = {
	name: string;
	ecosystem: Ecosystem;
	from: string[];
	files: string[];
};

export type Plan = {
	migrations: Migration[];
	skipped: Skip[];
	floating: Floating[];
};

const table = (value: unknown) =>
	value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/** every directory from the pin's own up to the repo root, which is node's own resolution order */
const upwards = (file: string) => {
	const directories: string[] = [];
	let dir = path.posix.dirname(file);
	while (dir && dir !== ".") {
		directories.push(dir);
		dir = path.posix.dirname(dir);
	}
	return [...directories, ""];
};

/**
 * What is installed for a node pin, read from the nearest node_modules rather than from bun.lock. It is
 * the same answer and it is unambiguous: commander is pinned at two different majors in this repo, and
 * only walking up from each manifest tells them apart.
 */
const installedNode = async (repoDir: string, pin: Pin) => {
	for (const dir of upwards(pin.file)) {
		const manifest = path.join(
			repoDir,
			dir,
			"node_modules",
			pin.name,
			"package.json",
		);
		const handle = Bun.file(manifest);
		if (!(await handle.exists())) continue;
		const version = table(JSON.parse(await handle.text())).version;
		if (typeof version === "string") return version;
	}
	return undefined;
};

/** what is locked for a python pin, from the nearest uv.lock, which libraries simply do not have */
const installedPython = async (repoDir: string, pin: Pin) => {
	const wanted = normalise(pin.name);
	for (const dir of upwards(pin.file)) {
		const handle = Bun.file(path.join(repoDir, dir, "uv.lock"));
		if (!(await handle.exists())) continue;
		const locked = table(Bun.TOML.parse(await handle.text())).package;
		if (!Array.isArray(locked)) continue;
		for (const entry of locked) {
			const { name, version } = table(entry);
			if (
				typeof name === "string" &&
				typeof version === "string" &&
				normalise(name) === wanted
			)
				return version;
		}
	}
	return undefined;
};

const installed = (repoDir: string, pin: Pin) =>
	pin.ecosystem === "node"
		? installedNode(repoDir, pin)
		: installedPython(repoDir, pin);

/** the dependencies worth migrating: not already exact, and not resolved by something else entirely */
const migratable = (dependency: Dependency) =>
	dependency.ecosystem !== "docker" &&
	dependency.precision !== "exact" &&
	dependency.precision !== "alias";

/** exempt pins, dropped before grouping so a partly exempt dependency migrates only where it must */
export const migratablePins = (pins: Pin[], exceptions: Exception[]) =>
	pins.filter((pin) => !exemption(exceptions, pin));

export const planMigration = async (
	repoDir: string,
	dependencies: Dependency[],
	exempt: Dependency[] = [],
): Promise<Plan> => {
	const migrations: Migration[] = [];
	const skipped: Skip[] = [];
	// only the exempt ones migrate would otherwise have bumped: the rest it was never going to touch
	const floating = exempt.filter(migratable).map((dependency) => ({
		name: dependency.name,
		ecosystem: dependency.ecosystem,
		from: [...new Set(dependency.occurrences.map((pin) => pin.specifier))],
		files: dependency.occurrences.map((pin) => pin.file),
	}));

	for (const dependency of dependencies.filter(migratable)) {
		const from = dependency.occurrences.map((pin) => pin.specifier);
		const resolved = await Promise.all(
			dependency.occurrences.map(async (pin) => ({
				pin,
				version: await installed(repoDir, pin),
			})),
		);
		const missing = resolved.filter(({ version }) => !version);
		if (missing.length) {
			skipped.push({
				name: dependency.name,
				ecosystem: dependency.ecosystem,
				kind: "uninstalled",
				from,
				reason: `no locked version found for ${missing
					.map(({ pin }) => pin.file)
					.join(", ")} - install first, or it is a library with no lockfile`,
			});
			continue;
		}
		const versions = [...new Set(resolved.map(({ version }) => version))];
		if (versions.length > 1) {
			skipped.push({
				name: dependency.name,
				ecosystem: dependency.ecosystem,
				kind: "diverged",
				from,
				reason: `resolves to ${versions.join(" and ")} in different places, so pick one with set`,
			});
			continue;
		}
		migrations.push({
			name: dependency.name,
			ecosystem: dependency.ecosystem,
			from,
			to: versions[0] as string,
			files: dependency.occurrences.map((pin) => pin.file),
		});
	}
	return { migrations, skipped, floating };
};

export type Result = Plan & { files: string[]; locked: string[] };

export const migrate = async ({
	repoDir,
	dryRun,
	lock = true,
}: {
	repoDir: string;
	dryRun?: boolean;
	lock?: boolean;
}): Promise<Result> => {
	const exceptions = await readExceptions(repoDir);
	const pins = await readPins(repoDir);
	// grouped from the pins that remain, so a partly exempt dependency's precision is only about those
	const dependencies = group(migratablePins(pins, exceptions));
	const plan = await planMigration(
		repoDir,
		dependencies,
		group(pins.filter((pin) => !!exemption(exceptions, pin))),
	);
	if (dryRun) return { ...plan, files: [], locked: [] };

	// every edit is planned before any is written, as everywhere else in this tool
	const edits = dependencies
		.filter((dependency) =>
			plan.migrations.some((migration) => migration.name === dependency.name),
		)
		.flatMap((dependency) => {
			const migration = plan.migrations.find(
				(candidate) => candidate.name === dependency.name,
			);
			return migration
				? plannedEdits(dependency.occurrences, migration.to)
				: [];
		});

	const files = await applyEdits(repoDir, edits);
	const actions = await lockActionsFor(repoDir, files);
	return {
		...plan,
		files,
		locked: lock ? await regenerate(repoDir, actions) : actions.map(commandFor),
	};
};

const SKIPPED: Record<Skip["kind"], string> = {
	uninstalled: "not installed",
	diverged: "resolves differently",
};

/** the pins as written, deduped: four files pinning ^1.2.3 is one thing to say, not four */
const specifiers = (from: string[]) => [...new Set(from)].join(", ");

/** alphabetical within each ecosystem, as `check` lists them, so a name is where you look for it */
const byName = <T extends { name: string }>(a: T, b: T) =>
	a.name.localeCompare(b.name);

/**
 * Why a dependency was skipped is two words here and the whole sentence on stderr, for the same reason
 * `check` keeps its notes out of the table: a row wide enough to explain itself is a row nobody reads.
 */
export const render = (
	result: Result,
	{ dryRun, lock = true }: { dryRun?: boolean; lock?: boolean } = {},
) => {
	const lines = tabulate([
		{
			heading: "migrating",
			groups: byEcosystem(
				[...result.migrations].sort(byName).map(
					(migration): Tagged => ({
						ecosystem: migration.ecosystem,
						left: migration.name,
						middle: `${specifiers(migration.from)} -> ${migration.to}`,
						right: where(migration.files),
					}),
				),
			),
		},
		{
			heading: "skipped",
			groups: byEcosystem(
				[...result.skipped].sort(byName).map(
					(skip): Tagged => ({
						ecosystem: skip.ecosystem,
						left: skip.name,
						middle: specifiers(skip.from),
						right: SKIPPED[skip.kind],
					}),
				),
			),
		},
		{
			// no target version on these rows: what they would be bumped to is precisely what must not
			// happen to them, so printing it would be an invitation
			heading: "intentionally floating, not migrated",
			groups: byEcosystem(
				[...result.floating].sort(byName).map(
					(floating): Tagged => ({
						ecosystem: floating.ecosystem,
						left: floating.name,
						middle: specifiers(floating.from),
						right: where(floating.files),
					}),
				),
			),
		},
	]);

	const written = dryRun
		? ["dry run, nothing written"]
		: [
				`wrote ${count(result.files.length, "file")}`,
				// what --no-lock leaves behind is a command to run, not a thing that happened
				...(result.locked.length
					? [
							lock
								? `ran ${result.locked.join(", ")}`
								: `to lock, run: ${result.locked.join(", ")}`,
						]
					: []),
			];
	return [
		...lines,
		"",
		`${result.migrations.length} to migrate, ${result.skipped.length} skipped, ${result.floating.length} left floating`,
		...written,
	].join("\n");
};

if (import.meta.main) {
	const command = new Command()
		.option("--dry-run", "list what would change without writing anything")
		.option("--no-lock", "rewrite the pins but do not regenerate any lockfile")
		.option("--json", "emit the result as one line of JSON")
		.parse();
	const options = command.opts();

	const result = await migrate({
		repoDir: process.cwd(),
		dryRun: options.dryRun,
		lock: options.lock,
	});

	if (options.json) console.log(JSON.stringify(result));
	else
		console.log(render(result, { dryRun: options.dryRun, lock: options.lock }));

	for (const { name, reason } of result.skipped)
		console.error(`skipped ${name}: ${reason}`);
}
