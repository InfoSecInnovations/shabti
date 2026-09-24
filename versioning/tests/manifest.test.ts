import { describe, expect, test } from "bun:test";
import {
	dependencyNamesIn,
	manifestIn,
	nameIn,
	typeIn,
	versionIn,
} from "../manifest";
import { nodePackage, pythonPackage, useRepo } from "./fixture";

describe("manifestIn", () => {
	const repo = useRepo({
		packages: [
			nodePackage("node-both", "0.1.0"),
			pythonPackage("python-only", "0.1.0"),
			pythonPackage("python-nested", "0.1.0", { dir: "containers/nested" }),
		],
	});

	test("finds a package.json", async () => {
		expect(await manifestIn(repo.dir, "node-both")).toBe(
			"node-both/package.json",
		);
	});

	test("finds a pyproject.toml", async () => {
		expect(await manifestIn(repo.dir, "python_only")).toBe(
			"python_only/pyproject.toml",
		);
	});

	test("prefers package.json where a directory has both", async () => {
		await repo.write(
			"node-both/pyproject.toml",
			'[project]\nversion = "9.9.9"\n',
		);
		expect(await manifestIn(repo.dir, "node-both")).toBe(
			"node-both/package.json",
		);
	});

	test("keeps nested paths posix, for git", async () => {
		expect(await manifestIn(repo.dir, "containers/nested")).toBe(
			"containers/nested/pyproject.toml",
		);
	});

	test("throws where a directory has neither", async () => {
		await expect(manifestIn(repo.dir, "nothing-here")).rejects.toThrow(
			/no package.json or pyproject.toml/,
		);
	});
});

describe("versionIn", () => {
	test("reads a package.json", () => {
		expect(versionIn("a/package.json", '{ "version": "1.2.3" }')).toBe("1.2.3");
	});

	test("reads a pyproject.toml", () => {
		expect(
			versionIn(
				"a/pyproject.toml",
				'[project]\nname = "a"\nversion = "1.2.3"\n',
			),
		).toBe("1.2.3");
	});

	test("takes the first top level version of a pyproject.toml", () => {
		expect(
			versionIn(
				"a/pyproject.toml",
				'[project]\nversion = "1.2.3"\n\n[tool.other]\nversion = "9.9.9"\n',
			),
		).toBe("1.2.3");
	});

	test("returns null when there is no version", () => {
		expect(versionIn("a/package.json", '{ "name": "a" }')).toBeNull();
		expect(versionIn("a/pyproject.toml", '[project]\nname = "a"\n')).toBeNull();
	});

	test("returns null for text that does not parse", () => {
		expect(versionIn("a/package.json", "not a manifest")).toBeNull();
	});

	test("throws for a file it does not know how to read", () => {
		expect(() => versionIn("a/setup.cfg", "")).toThrow(/not a package.json/);
	});
});

describe("typeIn", () => {
	test("names the ecosystem a manifest belongs to", () => {
		expect(typeIn("a/package.json")).toBe("node");
		expect(typeIn("a/b/pyproject.toml")).toBe("python");
		expect(() => typeIn("a/setup.cfg")).toThrow(/not a package.json/);
	});
});

describe("nameIn", () => {
	test("reads a distribution name", () => {
		expect(nameIn("a/package.json", '{ "name": "@scope/a" }')).toBe("@scope/a");
		expect(nameIn("a/pyproject.toml", '[project]\nname = "a-b"\n')).toBe("a-b");
	});

	test("returns null when there is no name", () => {
		expect(nameIn("a/package.json", '{ "version": "1.2.3" }')).toBeNull();
		expect(
			nameIn("a/pyproject.toml", '[project]\nversion = "1.2.3"\n'),
		).toBeNull();
	});

	test("throws for text that does not parse, naming the file", () => {
		expect(() => nameIn("a/pyproject.toml", 'dependencies = ["a",')).toThrow(
			/could not parse a\/pyproject.toml/,
		);
		expect(() => nameIn("a/pyproject.toml", "not a manifest")).toThrow(
			/could not parse a\/pyproject.toml/,
		);
	});
});

describe("dependencyNamesIn", () => {
	test("reads every kind of node dependency", () => {
		const manifest = JSON.stringify({
			dependencies: { runtime: "^1.0.0" },
			devDependencies: { dev: "^1.0.0" },
			peerDependencies: { peer: "^1.0.0" },
			optionalDependencies: { optional: "^1.0.0" },
		});
		expect(dependencyNamesIn("a/package.json", manifest).sort()).toEqual([
			"dev",
			"optional",
			"peer",
			"runtime",
		]);
	});

	test("reads every kind of python dependency", () => {
		const manifest = `[project]
dependencies = ["runtime"]

[project.optional-dependencies]
extra = ["optional"]

[dependency-groups]
dev = ["dev", { include-group = "test" }]
test = ["testing"]
`;
		expect(dependencyNamesIn("a/pyproject.toml", manifest).sort()).toEqual([
			"dev",
			"optional",
			"runtime",
			"testing",
		]);
	});

	test("strips extras, specifiers and markers from a requirement", () => {
		const manifest = `[project]
dependencies = [
    "fastapi[standard]~=0.136.3",
    "shabti-types==0.1.0",
    "tqdm>=4 ; python_version > '3.12'",
]
`;
		expect(dependencyNamesIn("a/pyproject.toml", manifest)).toEqual([
			"fastapi",
			"shabti-types",
			"tqdm",
		]);
	});

	test("returns nothing for a manifest that declares no dependencies", () => {
		expect(dependencyNamesIn("a/package.json", '{ "name": "a" }')).toEqual([]);
		expect(dependencyNamesIn("a/pyproject.toml", "[project]\n")).toEqual([]);
	});
});
