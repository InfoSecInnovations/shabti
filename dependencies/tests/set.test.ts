import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import * as git from "../../versioning/git";
import { useRepo } from "../../versioning/tests/fixture";
import * as catalogueModule from "../catalogue";
import { parseSpec, render, set } from "../set";
import type { Catalogue, Release } from "../types";

const ROOT_PACKAGE = `{
	"name": "fixture-root",
	"private": true,
	"workspaces": [],
	"dependencies": {
		"commander": "15.0.0"
	},
	"devDependencies": {
		"@types/semver": "7.8.0"
	},
	"version": "0.1.0"
}
`;

const ROOT_PYPROJECT = `[project]
name = "fixture"
version = "0.1.0"
dependencies = ["fastapi==0.136.0"]
`;

const COMPOSE = `services:
  uv-lock:
    image: astral/uv:0.9.6-python3.14-trixie-slim
`;

const repo = useRepo({
	packages: [],
	files: {
		"package.json": ROOT_PACKAGE,
		"pyproject.toml": ROOT_PYPROJECT,
		"docker-compose.yml": COMPOSE,
	},
});

const stable = (versions: string[]): Release[] =>
	versions.map((version) => ({ version, prerelease: false }));

const catalogue = (
	versions: string[],
	extra: Partial<Catalogue> = {},
): Catalogue => {
	const releases = stable(versions);
	return { releases, latestStable: releases.at(-1), notes: [], ...extra };
};

const CATALOGUES: Record<string, Catalogue> = {
	fastapi: catalogue(["0.136.0", "0.137.0"]),
	commander: catalogue(["14.0.0", "15.0.0", "15.1.0"]),
	"@types/semver": catalogue(["7.7.0", "7.8.0", "7.9.0"]),
	"astral/uv": catalogue(["0.9.6", "0.9.7"]),
};

/** what the registry answers with this test, so nothing here needs a network */
let catalogues: Record<string, Catalogue>;
const stub = (overrides: Record<string, Catalogue> = {}) => {
	catalogues = { ...CATALOGUES, ...overrides };
};

beforeEach(() => {
	stub();
	spyOn(catalogueModule, "registry").mockReturnValue(async (dependency) => {
		const found = catalogues[dependency.id];
		if (!found) throw new Error(`nothing stubbed for ${dependency.id}`);
		return found;
	});
});

afterEach(() => {
	mock.restore();
});

// taken before anything spies on it, so git itself still runs for real
const realRun = git.run;

/** the lockfile commands, recorded rather than run, so no test starts Docker */
const recorder = () => {
	const ran: string[][] = [];
	spyOn(git, "run").mockImplementation(async (command, options) => {
		if (command[0] === "git") return realRun(command, options);
		ran.push(command);
		return { exitCode: 0, stdout: "", stderr: "" };
	});
	return { ran };
};

const setting = (
	specs: string[],
	extra: { literalTag?: boolean; lock?: boolean } = {},
) => set({ repoDir: repo.dir, specs, ...extra });

describe("parseSpec", () => {
	test("splits name@version", () => {
		expect(parseSpec("fastapi@0.137.0")).toEqual({
			name: "fastapi",
			version: "0.137.0",
		});
	});

	test("splits at the last @, so a scoped package keeps its own", () => {
		expect(parseSpec("@types/semver@7.8.0")).toEqual({
			name: "@types/semver",
			version: "7.8.0",
		});
	});

	test("a bare name asks for the latest, scoped or not", () => {
		expect(parseSpec("fastapi")).toEqual({ name: "fastapi" });
		expect(parseSpec("@types/bun")).toEqual({ name: "@types/bun" });
	});

	test("an @ with nothing after it is a typo rather than either", () => {
		expect(() => parseSpec("fastapi@")).toThrow(/names no version/);
		expect(() => parseSpec("")).toThrow(/empty string/);
	});
});

describe("a list of dependencies is one run", () => {
	test("sets every one named, and locks once for the batch", async () => {
		const { ran } = recorder();
		const result = await setting(["fastapi@0.137.0", "commander@15.1.0"]);

		expect(result.changes.map((change) => [change.name, change.to])).toEqual([
			["fastapi", "0.137.0"],
			["commander", "15.1.0"],
		]);
		expect(await repo.read("pyproject.toml")).toContain("fastapi==0.137.0");
		expect(await repo.read("package.json")).toContain('"commander": "15.1.0"');
		expect(result.files).toEqual(["package.json", "pyproject.toml"]);
		expect(ran).toEqual([
			["uv", "lock"],
			["bun", "install", "--lockfile-only"],
		]);
	});

	test("two node pins regenerate bun.lock once between them", async () => {
		const { ran } = recorder();
		await setting(["commander@15.1.0", "@types/semver@7.9.0"]);

		expect(await repo.read("package.json")).toContain('"commander": "15.1.0"');
		expect(await repo.read("package.json")).toContain(
			'"@types/semver": "7.9.0"',
		);
		expect(ran).toEqual([["bun", "install", "--lockfile-only"]]);
	});

	test("--no-lock names what is left to run, once", async () => {
		const { ran } = recorder();
		const result = await setting(["fastapi@0.137.0", "commander@15.1.0"], {
			lock: false,
		});

		expect(ran).toEqual([]);
		expect(result.locked).toEqual(["uv lock", "bun install --lockfile-only"]);
		expect(result.warnings).toEqual([
			"did not run: uv lock, bun install --lockfile-only",
		]);
	});
});

describe("a name with no version takes the latest", () => {
	test("the version the report calls the latest stable", async () => {
		const result = await setting(["fastapi"], { lock: false });

		expect(result.changes[0]?.to).toBe("0.137.0");
		expect(await repo.read("pyproject.toml")).toContain("fastapi==0.137.0");
	});

	test("never a prerelease and never a withdrawn release", async () => {
		// latestStable is what the report is judged by, so it is what a bare name has to follow
		stub({
			fastapi: {
				releases: [
					...stable(["0.136.0", "0.137.0"]),
					{ version: "0.137.1", prerelease: false, withdrawn: "yanked" },
					{ version: "0.138.0b1", prerelease: true },
				],
				latestStable: { version: "0.137.0", prerelease: false },
				notes: [],
			},
		});
		const result = await setting(["fastapi"], { lock: false });

		expect(result.changes[0]?.to).toBe("0.137.0");
	});

	test("a docker tag keeps every literal the pin held constant", async () => {
		const result = await setting(["astral/uv"], { lock: false });

		expect(result.changes[0]?.to).toBe("0.9.7");
		expect(await repo.read("docker-compose.yml")).toContain(
			"astral/uv:0.9.7-python3.14-trixie-slim",
		);
	});

	test("refuses when nothing stable is published", async () => {
		// moving to a prerelease is a decision, so it has to be spelled out
		stub({
			fastapi: catalogue(["0.138.0b1"], { latestStable: undefined }),
		});
		await expect(setting(["fastapi"], { lock: false })).rejects.toThrow(
			/no stable release to move to/,
		);
	});

	test("--tag has no tag to write without one", async () => {
		await expect(
			setting(["astral/uv"], { lock: false, literalTag: true }),
		).rejects.toThrow(/--tag/);
	});
});

describe("a pin already at the version asked for", () => {
	test("writes nothing, locks nothing, and says so", async () => {
		const { ran } = recorder();
		const result = await setting(["fastapi@0.136.0"]);

		expect(result.files).toEqual([]);
		expect(result.locked).toEqual([]);
		expect(ran).toEqual([]);
		expect(result.changes[0]?.files).toEqual([]);
		expect(render(result)).toContain("already set");
	});
});

describe("all or nothing across the whole list", () => {
	test("every refusal is reported together, and nothing is written", async () => {
		const pyproject = await repo.read("pyproject.toml");
		const error = (await setting([
			"nope@1.0.0",
			"fastapi@9.9.9",
			"commander@15.1.0",
		]).catch((thrown) => thrown)) as Error;

		expect(error.message).toContain("nothing in this repo pins nope");
		expect(error.message).toContain("fastapi 9.9.9 is not published");
		expect(await repo.read("pyproject.toml")).toBe(pyproject);
		// the one good spec in the list is not written either
		expect(await repo.read("package.json")).toContain('"commander": "15.0.0"');
	});

	test("the same dependency twice at two versions is refused", async () => {
		await expect(
			setting(["commander@15.1.0", "commander@14.0.0"]),
		).rejects.toThrow(/named twice/);
	});

	test("the same dependency twice at one version is one change", async () => {
		const result = await setting(["commander@15.1.0", "commander@15.1.0"], {
			lock: false,
		});

		expect(result.changes).toHaveLength(1);
		expect(result.files).toEqual(["package.json"]);
	});

	test("an empty list names nothing to set", async () => {
		await expect(setting([])).rejects.toThrow(/at least one/);
	});
});

describe("warnings", () => {
	test("a withdrawn version is written, against its own name", async () => {
		stub({
			commander: {
				releases: [
					...stable(["14.0.0", "15.0.0"]),
					{ version: "15.1.0", prerelease: false, withdrawn: "deprecated" },
				],
				latestStable: { version: "15.0.0", prerelease: false },
				notes: [],
			},
		});
		const result = await setting(["commander@15.1.0"], {
			lock: false,
		});

		expect(result.changes[0]?.warnings).toEqual(["15.1.0 is deprecated"]);
		expect(await repo.read("package.json")).toContain('"commander": "15.1.0"');
	});

	test("an unchecked tag says it was never checked", async () => {
		const result = await setting(["astral/uv@edge"], {
			lock: false,
			literalTag: true,
		});

		expect(result.changes[0]?.warnings).toEqual([
			"wrote the tag edge without checking it exists",
		]);
		expect(await repo.read("docker-compose.yml")).toContain("astral/uv:edge");
	});
});

describe("the table", () => {
	test("shows the pin, what it becomes, and where it went", async () => {
		const result = await setting(["fastapi@0.137.0", "commander@15.1.0"], {
			lock: false,
		});
		const text = render(result, { lock: false });

		expect(text).toContain("setting");
		expect(text).toContain("python");
		expect(text).toContain("0.136.0 -> 0.137.0");
		expect(text).toContain("15.0.0 -> 15.1.0");
		expect(text).toContain("2 set, wrote 2 files");
		expect(text).toContain(
			"to lock, run: uv lock, bun install --lockfile-only",
		);
	});

	test("says what it ran when it locked", async () => {
		recorder();
		const result = await setting(["fastapi@0.137.0"]);

		expect(render(result)).toContain("wrote 1 file\nran uv lock");
	});
});
