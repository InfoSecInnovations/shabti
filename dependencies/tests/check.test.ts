import { describe, expect, test } from "bun:test";
import { type Report, render } from "../check";
import type { Dependency, Freshness, Pin, Precision, Release } from "../types";

const release = (version: string, raw?: string): Release => ({
	version,
	raw,
	prerelease: false,
});

const pin = (extra: Partial<Pin> = {}): Pin => ({
	name: "x",
	id: "x",
	ecosystem: "docker",
	file: "docker-compose.yml",
	line: 1,
	specifier: "",
	raw: "",
	version: null,
	precision: "exact",
	extras: [],
	marker: null,
	location: "services.x.image",
	...extra,
});

const dependency = (extra: Partial<Dependency> = {}): Dependency => ({
	id: "x",
	ecosystem: "docker",
	name: "x",
	occurrences: [pin()],
	versions: [],
	agreement: "agreed",
	precision: "exact",
	...extra,
});

const behind = (
	dep: Dependency,
	latest: Release,
	behindBy = 1,
): Extract<Freshness, { status: "ok" }> => ({
	status: "ok",
	dependency: dep,
	catalogue: { releases: [latest], latestStable: latest, notes: [] },
	verdict: { behind: true, behindBy },
});

/** an ordinary exactly pinned python dependency, the plainest row the report has */
const shiny = () =>
	dependency({
		id: "shiny",
		name: "shiny",
		ecosystem: "python",
		versions: ["1.6.3"],
		occurrences: [
			pin({
				id: "shiny",
				name: "shiny",
				ecosystem: "python",
				file: "pyproject.toml",
				specifier: "==1.6.3",
				version: "1.6.3",
				location: "project.dependencies",
			}),
		],
	});

const report = (found: Freshness[]): Report => ({
	found,
	exempt: [],
	summary: {
		total: found.length,
		behind: found.length,
		unpinned: 0,
		diverged: 0,
		unsupported: 0,
		failed: 0,
	},
});

describe("docker rows show whole tags", () => {
	/**
	 * A docker version is only the slot inside its tag, so the bare version is not something anyone
	 * would recognise from a registry. Both sides show the tag; deps:set accepts either spelling.
	 */
	const cases: [string, string, string, string, string][] = [
		[
			"astral/uv",
			"0.11.1-python3.14-trixie-slim",
			"0.11.1",
			"0.12.5",
			"0.12.5-python3.14-trixie-slim",
		],
		["apache/tika", "3.3.0.0-full", "3.3.0.0", "4.0.0-1", "4.0.0-1-full"],
		[
			"ggml-org/llama.cpp",
			"server-cuda-b9843",
			"b9843",
			"b10615",
			"server-cuda-b10615",
		],
	];

	for (const [name, specifier, slot, latestSlot, latestTag] of cases)
		test(name, () => {
			const text = render(
				report([
					behind(
						dependency({
							id: name,
							name,
							versions: [slot],
							occurrences: [pin({ id: name, name, specifier })],
						}),
						release(latestSlot, latestTag),
					),
				]),
			);
			expect(text).toContain(`${specifier} -> ${latestTag}`);
			// the bare slot on its own would be the old, unrecognisable output
			expect(text).not.toContain(`${slot} -> ${latestSlot}`);
		});

	test("an unlabelled tag needs no rewriting to be recognisable", () => {
		const text = render(
			report([
				behind(
					dependency({
						id: "postgres",
						name: "postgres",
						versions: ["18.3"],
						occurrences: [
							pin({ id: "postgres", name: "postgres", specifier: "18.3" }),
						],
					}),
					release("19.1"),
				),
			]),
		);
		expect(text).toContain("18.3 -> 19.1");
	});
});

describe("other ecosystems are unchanged", () => {
	test("a python row shows the version, which is the whole pin", () => {
		const text = render(report([behind(shiny(), release("1.7.0"))]));
		expect(text).toContain("1.6.3 -> 1.7.0");
		expect(text).toContain("python");
		expect(text).toContain("pyproject.toml");
	});
});

/** an exactly pinned node dependency, for the sections that need a second ecosystem in them */
const commander = (precision: Precision = "exact", specifier = "14.0.0") =>
	dependency({
		id: "commander",
		name: "commander",
		ecosystem: "node",
		precision,
		versions: ["14.0.0"],
		occurrences: [
			pin({
				id: "commander",
				name: "commander",
				ecosystem: "node",
				file: "package.json",
				specifier,
				version: "14.0.0",
				precision,
				location: "dependencies",
			}),
		],
	});

const lineIndex = (text: string) => {
	const lines = text.split("\n");
	return {
		lines,
		heading: (name: string) => lines.findIndex((line) => line.trim() === name),
		row: (name: string) =>
			lines.findIndex((line) => line.trimStart().startsWith(`${name} `)),
	};
};

describe("sections are the kind of mismatch, not the ecosystem", () => {
	test("a row is filed under the heading it is worst at", () => {
		const text = render(
			report([
				behind(shiny(), release("1.7.0")),
				behind(commander("compatible", "^14.0.0"), release("14.1.0")),
			]),
		);
		const { lines, heading, row } = lineIndex(text);

		// the range pin is behind too, but tightening it is the thing to do, so that is where it goes
		expect(row("commander")).toBeGreaterThan(heading("not pinned exactly"));
		expect(row("commander")).toBeLessThan(heading("behind"));
		expect(row("shiny")).toBeGreaterThan(heading("behind"));
		expect(lines.filter((line) => line.includes("commander"))).toHaveLength(1);
	});

	test("a section holding two ecosystems sub heads them both", () => {
		const text = render(
			report([
				behind(shiny(), release("1.7.0")),
				behind(commander(), release("14.1.0")),
			]),
		);
		const { heading, row } = lineIndex(text);

		// one heading, then an ecosystem apiece, in the order types.ts declares them
		expect(heading("behind")).toBeGreaterThanOrEqual(0);
		expect(row("shiny")).toBeGreaterThan(heading("python"));
		expect(row("commander")).toBeGreaterThan(heading("node"));
		expect(heading("python")).toBeLessThan(heading("node"));
	});

	test("a prerelease is listed apart rather than lost from a row", () => {
		const entry = behind(shiny(), release("1.7.0"));
		const text = render(
			report([
				{
					...entry,
					catalogue: {
						...entry.catalogue,
						latestPrerelease: { version: "1.8.0b1", prerelease: true },
					},
				},
			]),
		);
		expect(text).toContain("prerelease available");
		expect(text).toContain("1.6.3 -> 1.8.0b1");
		// and still where it can be acted on
		expect(text).toContain("1.6.3 -> 1.7.0");
	});
});

describe("system tools", () => {
	test("are listed under their own ecosystem with the executable", () => {
		const bun = dependency({
			id: "bun",
			name: "bun",
			ecosystem: "system",
			occurrences: [],
			versions: ["1.3.11"],
			executable: "/usr/local/bin/bun",
		});
		const text = render(report([behind(bun, release("1.3.12"))]));
		const { heading, row } = lineIndex(text);

		expect(row("bun")).toBeGreaterThan(heading("system"));
		expect(text).toContain("1.3.11 -> 1.3.12  /usr/local/bin/bun");
	});
});

describe("summary", () => {
	test("counts what it found", () => {
		expect(render(report([]))).toContain("0 dependencies, 0 behind");
	});
});
