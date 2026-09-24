/**
 * Reports every third party dependency this repo pins directly: what is pinned, whether the pin is
 * exact, whether every file naming it agrees, and what newer stable and prerelease versions exist.
 * The uv and Bun installed on this machine are reported alongside, under their own ecosystem.
 *
 * bun ./dependencies/check.ts [--all] [--json] [--no-latest-check] [--fail-if-behind]
 *
 * Being behind is information rather than a failure, so this exits 0 by default however far behind
 * anything is. A lookup that could not be made exits 1, because that is the report being wrong rather
 * than the repo being out of date.
 */

import { Command } from "commander";
import { freshness } from "./catalogue";
import { type Exception, exemption, readExceptions } from "./exceptions";
import type { Options } from "./http";
import { group, readPins } from "./read";
import { type Tagged, byEcosystem, tabulate, where } from "./render";
import { systemTools } from "./system";
import type { Dependency, Freshness, Precision, Release } from "./types";

export type Report = {
	found: Freshness[];
	/** deliberately floating, so they are listed apart from the warnings rather than among them */
	exempt: { dependency: Dependency; reason: string }[];
	summary: {
		total: number;
		behind: number;
		unpinned: number;
		diverged: number;
		unsupported: number;
		failed: number;
	};
};

/** exempt only when every occurrence is, so a dependency an app also pins is still checked */
const isExempt = (dependency: Dependency, exceptions: Exception[]) =>
	dependency.occurrences.every((pin) => exemption(exceptions, pin));

const reasonFor = (dependency: Dependency, exceptions: Exception[]) =>
	dependency.occurrences
		.map((pin) => exemption(exceptions, pin)?.reason)
		.find((reason): reason is string => !!reason) ?? "";

export const check = async ({
	repoDir,
	resolveLatest,
	...options
}: Options & {
	repoDir: string;
	resolveLatest?: boolean;
}): Promise<Report> => {
	const exceptions = await readExceptions(repoDir);
	const all = group(await readPins(repoDir));
	const exempt = all
		.filter((dependency) => isExempt(dependency, exceptions))
		.map((dependency) => ({
			dependency,
			reason: reasonFor(dependency, exceptions),
		}));
	// added after the exemptions, which a tool with no occurrences would otherwise vacuously satisfy
	const checked = [
		...all.filter((dependency) => !isExempt(dependency, exceptions)),
		...(await systemTools(repoDir)),
	];
	const found = await freshness(checked, {
		...options,
		resolveLatest,
	});

	return {
		found,
		exempt,
		summary: {
			total: checked.length,
			behind: found.filter(
				(entry) => entry.status === "ok" && entry.verdict.behind,
			).length,
			unpinned: checked.filter(
				(dependency) =>
					dependency.precision === "absent" || dependency.precision === "tag",
			).length,
			diverged: checked.filter(
				(dependency) => dependency.agreement !== "agreed",
			).length,
			unsupported: found.filter((entry) => entry.status === "unsupported")
				.length,
			failed: found.filter((entry) => entry.status === "failed").length,
		},
	};
};

/**
 * What the pin says, spelled the way a person would recognise it. A docker version is only the slot
 * inside its tag, so 0.11.1 is shown as 0.11.1-python3.14-trixie-slim: that is what is written in the
 * file, and what Docker Hub would call it.
 */
const pinned = (dependency: Dependency) => {
	const shown =
		dependency.ecosystem === "docker"
			? [
					...new Set(
						dependency.occurrences
							.map((pin) => pin.specifier)
							.filter((specifier) => !!specifier),
					),
				]
			: dependency.versions;
	return shown.length
		? shown.join(", ")
		: dependency.occurrences[0]?.specifier || "-";
};

/** the whole tag rather than the substitutable part of it; `deps:set` accepts either */
const available = (release?: Release) =>
	release && (release.raw ?? release.version);

/** a system tool lives in no file, so its row says which executable was asked */
const files = (dependency: Dependency) =>
	dependency.ecosystem === "system"
		? (dependency.executable ?? "-")
		: where(dependency.occurrences.map((pin) => pin.file));

/** the precisions that are a pin someone should tighten, as against `alias`, which nothing can pin */
const IMPRECISE: Precision[] = ["absent", "tag", "range", "compatible"];

const imprecise = (dependency: Dependency) =>
	IMPRECISE.includes(dependency.precision);

const interesting = (entry: Freshness) =>
	entry.status !== "ok" ||
	entry.verdict.behind ||
	entry.verdict.unknownPin ||
	entry.verdict.pinWithdrawn ||
	entry.dependency.agreement !== "agreed" ||
	imprecise(entry.dependency);

/**
 * What each row is doing in the report, most serious first, and each row is in exactly one of them: a
 * range pin that is also behind is listed under the range, because tightening it is the thing to do
 * and the row shows the newer version anyway. `up to date` is only reachable with --all.
 */
const CATEGORIES: { heading: string; holds: (entry: Freshness) => boolean }[] =
	[
		{ heading: "check failed", holds: (entry) => entry.status === "failed" },
		{
			heading: "pin not published upstream",
			holds: (entry) =>
				entry.status === "ok" &&
				!!(entry.verdict.unknownPin || entry.verdict.pinWithdrawn),
		},
		{
			heading: "files disagree",
			holds: (entry) => entry.dependency.agreement !== "agreed",
		},
		{
			heading: "not pinned exactly",
			holds: (entry) => imprecise(entry.dependency),
		},
		{
			heading: "behind",
			holds: (entry) => entry.status === "ok" && entry.verdict.behind,
		},
		{
			heading: "not supported",
			holds: (entry) => entry.status === "unsupported",
		},
		{ heading: "up to date", holds: () => true },
	];

const byName = (a: Freshness, b: Freshness) =>
	a.dependency.id.localeCompare(b.dependency.id);

/** the pin, and what it would become, in one column so the arrow reads as the pair it is */
const versions = (from: string, to?: string) =>
	to ? `${from} -> ${to}` : from;

export const render = (report: Report, showAll = false) => {
	const shown = report.found.filter((entry) => showAll || interesting(entry));

	const row = (entry: Freshness): Tagged => ({
		ecosystem: entry.dependency.ecosystem,
		left: entry.dependency.name,
		middle: versions(
			pinned(entry.dependency),
			entry.status === "ok" && entry.verdict.behind
				? available(entry.catalogue.latestStable)
				: undefined,
		),
		right: files(entry.dependency),
	});

	const claimed = new Set<Freshness>();
	const sections = CATEGORIES.map(({ heading, holds }) => {
		const entries = shown.filter(
			(entry) => !claimed.has(entry) && holds(entry),
		);
		for (const entry of entries) claimed.add(entry);
		return {
			heading,
			groups: byEcosystem([...entries].sort(byName).map(row)),
		};
	});

	/**
	 * Additive rather than a category of its own, so a dependency behind on stable is still listed
	 * where it can be acted on and its prerelease is not lost. It draws from the rows already shown,
	 * which is where a prerelease was ever mentioned.
	 */
	sections.push({
		heading: "prerelease available",
		groups: byEcosystem(
			[...shown].sort(byName).flatMap((entry) =>
				entry.status === "ok" && entry.catalogue.latestPrerelease
					? {
							ecosystem: entry.dependency.ecosystem,
							left: entry.dependency.name,
							middle: versions(
								pinned(entry.dependency),
								available(entry.catalogue.latestPrerelease),
							),
							right: files(entry.dependency),
						}
					: [],
			),
		),
	});

	/**
	 * Every reason here is a sentence, and eight sentences would be the longest lines in the report for
	 * the rows that need the least attention. They are in exceptions.json, which is where anyone
	 * changing one would look anyway, and in --json.
	 */
	sections.push({
		heading: "intentionally floating",
		groups: byEcosystem(
			[...report.exempt]
				.sort((a, b) => a.dependency.id.localeCompare(b.dependency.id))
				.map(({ dependency }) => ({
					ecosystem: dependency.ecosystem,
					left: dependency.name,
					middle: pinned(dependency),
					right: files(dependency),
				})),
		),
	});

	const { summary } = report;
	return [
		...tabulate(sections),
		"",
		`${summary.total} dependencies, ${summary.behind} behind, ${summary.diverged} disagreeing across files, ${summary.unpinned} not pinned, ${summary.unsupported} unsupported, ${summary.failed} failed`,
	].join("\n");
};

if (import.meta.main) {
	const command = new Command()
		.option("--all", "list every dependency, not only the ones worth acting on")
		.option("--json", "emit the report as one line of JSON")
		.option(
			"--no-latest-check",
			"skip resolving what each image's latest tag points at, which costs a request per image",
		)
		.option("--fail-if-behind", "exit non zero when anything is behind")
		.parse();
	const options = command.opts();

	const report = await check({
		repoDir: process.cwd(),
		resolveLatest: options.latestCheck,
	});

	if (options.json) console.log(JSON.stringify(report));
	else console.log(render(report, options.all));

	for (const entry of report.found)
		if (entry.status === "failed")
			console.error(
				`could not check ${entry.dependency.name}: ${entry.reason}`,
			);

	if (report.summary.failed) process.exitCode = 1;
	else if (options.failIfBehind && report.summary.behind) process.exitCode = 1;
}
