import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import * as catalogueModule from "../catalogue";
import { freshness, unsupported, verdict } from "../catalogue";
import type { Catalogue, Dependency, Pin, Release } from "../types";

const release = (version: string, extra: Partial<Release> = {}): Release => ({
	version,
	prerelease: false,
	...extra,
});

const catalogue = (
	versions: (string | Release)[],
	latest?: string,
): Catalogue => {
	const releases = versions.map((entry) =>
		typeof entry === "string" ? release(entry) : entry,
	);
	const stable = releases.filter(
		({ prerelease, withdrawn }) => !prerelease && !withdrawn,
	);
	return {
		releases,
		latestStable: latest
			? releases.find(({ version }) => version === latest)
			: stable.at(-1),
		notes: [],
	};
};

const pin = (extra: Partial<Pin> = {}): Pin => ({
	name: "x",
	id: "x",
	ecosystem: "python",
	file: "pyproject.toml",
	line: 1,
	specifier: "==1.0.0",
	raw: "x==1.0.0",
	version: "1.0.0",
	precision: "exact",
	extras: [],
	marker: null,
	location: "project.dependencies",
	...extra,
});

const dependency = (
	pins: Pin[],
	extra: Partial<Dependency> = {},
): Dependency => ({
	id: pins[0]?.id ?? "x",
	ecosystem: pins[0]?.ecosystem ?? "python",
	name: pins[0]?.name ?? "x",
	occurrences: pins,
	versions: [
		...new Set(pins.flatMap((entry) => (entry.version ? [entry.version] : []))),
	],
	agreement: "agreed",
	precision: "exact",
	...extra,
});

describe("verdict", () => {
	test("counts how many stable releases the pin is behind", () => {
		const result = verdict(
			dependency([pin({ version: "1.0.0" })]),
			catalogue(["1.0.0", "1.1.0", "1.2.0", "1.3.0"]),
		);
		expect(result.behind).toBe(true);
		expect(result.behindBy).toBe(3);
	});

	test("is not behind when the pin is the latest", () => {
		expect(
			verdict(
				dependency([pin({ version: "1.3.0" })]),
				catalogue(["1.0.0", "1.3.0"]),
			),
		).toEqual({
			behind: false,
			behindBy: undefined,
			unknownPin: undefined,
			pinWithdrawn: undefined,
		});
	});

	test("does not count prereleases or withdrawn releases as ground to make up", () => {
		const result = verdict(
			dependency([pin({ version: "1.0.0" })]),
			catalogue([
				"1.0.0",
				release("1.1.0", { prerelease: true }),
				release("1.2.0", { withdrawn: "yanked" }),
				"1.3.0",
			]),
		);
		expect(result.behindBy).toBe(1);
	});

	test("judges a diverged dependency by whichever file is furthest behind", () => {
		const result = verdict(
			dependency([
				pin({ version: "1.0.0", file: "a/pyproject.toml" }),
				pin({ version: "1.2.0", file: "b/pyproject.toml" }),
			]),
			catalogue(["1.0.0", "1.1.0", "1.2.0", "1.3.0"]),
		);
		expect(result.behind).toBe(true);
		expect(result.behindBy).toBe(3);
	});

	test("reports a pin that is not published at all", () => {
		const result = verdict(
			dependency([pin({ version: "9.9.9" })]),
			catalogue(["1.0.0"]),
		);
		expect(result.unknownPin).toBe(true);
		// nothing to compare against, so no claim about being behind
		expect(result.behind).toBe(false);
	});

	test("reports a pin that has been withdrawn", () => {
		const result = verdict(
			dependency([pin({ version: "1.0.0" })]),
			catalogue([release("1.0.0", { withdrawn: "yanked" }), "1.1.0"]),
		);
		expect(result.pinWithdrawn).toBe("yanked");
		expect(result.behind).toBe(true);
	});

	test("says nothing when the pin names no version", () => {
		expect(
			verdict(
				dependency([pin({ version: null, precision: "absent" })]),
				catalogue(["1.0.0"]),
			).behind,
		).toBe(false);
	});
});

describe("unsupported", () => {
	test("refuses an alias, whose version comes from elsewhere", () => {
		expect(
			unsupported(
				dependency([pin({ ecosystem: "node", precision: "alias" })], {
					ecosystem: "node",
					precision: "alias",
				}),
			),
		).toMatch(/aliased/);
	});

	test("refuses a digest", () => {
		expect(
			unsupported(
				dependency(
					[
						pin({
							ecosystem: "docker",
							image: {
								registry: null,
								repository: "postgres",
								tag: null,
								digest: "sha256:abc",
							},
						}),
					],
					{ ecosystem: "docker" },
				),
			),
		).toMatch(/digest/);
	});

	test("refuses a tag with no version in it", () => {
		expect(
			unsupported(
				dependency(
					[
						pin({
							ecosystem: "docker",
							image: {
								registry: null,
								repository: "x",
								tag: "nightly",
								digest: null,
							},
						}),
					],
					{ ecosystem: "docker" },
				),
			),
		).toMatch(/names no version/);
	});

	test("allows an untagged image, which compares against plain version tags", () => {
		expect(
			unsupported(
				dependency(
					[
						pin({
							ecosystem: "docker",
							image: {
								registry: null,
								repository: "javieraviles/zip",
								tag: null,
								digest: null,
							},
						}),
					],
					{ ecosystem: "docker" },
				),
			),
		).toBeNull();
	});

	test("allows an ordinary pin", () => {
		expect(unsupported(dependency([pin()]))).toBeNull();
	});
});

describe("freshness", () => {
	afterEach(() => {
		mock.restore();
	});

	test("turns a failed lookup into a failed row rather than a failed run", async () => {
		spyOn(catalogueModule, "registry").mockReturnValue(async (target) => {
			if (target.id === "bad") throw new Error("ghcr.io hiccuped");
			return catalogue(["1.0.0", "2.0.0"]);
		});
		const results = await freshness([
			dependency([pin({ id: "good", name: "good" })], {
				id: "good",
				name: "good",
			}),
			dependency([pin({ id: "bad", name: "bad" })], {
				id: "bad",
				name: "bad",
			}),
		]);
		expect(results.map((result) => result.status)).toEqual(["ok", "failed"]);
		const failed = results[1];
		if (failed?.status !== "failed") throw new Error("expected a failure");
		expect(failed.reason).toBe("ghcr.io hiccuped");
		// the row that worked still carries its verdict
		const ok = results[0];
		if (ok?.status !== "ok") throw new Error("expected a result");
		expect(ok.verdict.behind).toBe(true);
	});

	test("never asks the registry about something it cannot look up", async () => {
		let asked = 0;
		spyOn(catalogueModule, "registry").mockReturnValue(async () => {
			asked++;
			return catalogue(["1.0.0"]);
		});
		const results = await freshness([
			dependency([pin({ ecosystem: "node", precision: "alias" })], {
				ecosystem: "node",
				precision: "alias",
			}),
		]);
		expect(results[0]?.status).toBe("unsupported");
		expect(asked).toBe(0);
	});
});
