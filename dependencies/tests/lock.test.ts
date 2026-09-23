import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import * as git from "../../versioning/git";
import { useRepo } from "../../versioning/tests/fixture";
import { commandFor, lockActionsFor, regenerate } from "../lock";

const ROOT_PACKAGE = `{
	"name": "shabti",
	"workspaces": ["shabti_cli/", "shabti_configurator/", "shabti_configurator/client/"],
	"version": "0.8.0"
}
`;

const ROOT_PYPROJECT = `[project]
name = "shabti"
version = "0.1.0"
dependencies = ["ruff~=0.12.11"]

[tool.uv.workspace]
members = ["shiny_demo_projects"]
`;

/** the container shape: sources that only exist inside the image that builds it */
const API_PYPROJECT = `[project]
name = "shabti-api"
version = "0.1.0"
dependencies = ["fastapi~=0.136.3", "shabti-util"]

[tool.uv.sources]
shabti-util = { path = "/app/python_packages/shabti_util", editable = true }
`;

/** a published library: relative sources, and no lockfile of its own */
const LIBRARY_PYPROJECT = `[project]
name = "shabti-api-client"
version = "0.1.0"
dependencies = ["httpx", "shabti-types"]

[tool.uv.sources]
shabti-types = { path = "../shabti_types", editable = true }
`;

const repo = useRepo({
	packages: [],
	files: {
		"package.json": ROOT_PACKAGE,
		"pyproject.toml": ROOT_PYPROJECT,
		"shabti_cli/package.json": '{ "name": "shabti_cli", "version": "0.1.0" }\n',
		"shabti_configurator/client/package.json": '{ "name": "client" }\n',
		"docker_containers/shabti_api/pyproject.toml": API_PYPROJECT,
		"python_packages/shabti_api_client/pyproject.toml": LIBRARY_PYPROJECT,
		"shiny_demo_projects/pyproject.toml":
			'[project]\nname = "shiny-demo-projects"\nversion = "0.1.0"\ndependencies = ["shiny>=1.5.1"]\n',
		"elsewhere/package.json": '{ "name": "elsewhere" }\n',
		"elsewhere/bun.lock": '{ "lockfileVersion": 1 }\n',
	},
});

describe("lockActionsFor", () => {
	test("one bun run covers every workspace member", async () => {
		expect(
			await lockActionsFor(repo.dir, [
				"package.json",
				"shabti_cli/package.json",
				"shabti_configurator/client/package.json",
			]),
		).toEqual(["bun"]);
	});

	test("a container project can only be locked inside Docker", async () => {
		// decided by the absolute path in [tool.uv.sources], which is the documented condition
		expect(
			await lockActionsFor(repo.dir, [
				"docker_containers/shabti_api/pyproject.toml",
			]),
		).toEqual(["uv-docker"]);
	});

	test("the root and its uv workspace members lock on the host", async () => {
		expect(await lockActionsFor(repo.dir, ["pyproject.toml"])).toEqual([
			"uv-host",
		]);
		expect(
			await lockActionsFor(repo.dir, ["shiny_demo_projects/pyproject.toml"]),
		).toEqual(["uv-host"]);
	});

	test("a published library has no lockfile to regenerate", async () => {
		expect(
			await lockActionsFor(repo.dir, [
				"python_packages/shabti_api_client/pyproject.toml",
			]),
		).toEqual([]);
	});

	test("image tags are not locked", async () => {
		expect(
			await lockActionsFor(repo.dir, [
				"docker-compose.yml",
				"docker_containers/shabti_api/Dockerfile",
			]),
		).toEqual([]);
	});

	test("never touches a nested bun.lock", async () => {
		// one bun.lock at the root covers every workspace member; running bun install beside a
		// stray nested lockfile would only revive it
		expect(await lockActionsFor(repo.dir, ["elsewhere/bun.lock"])).toEqual([]);
	});

	test("deduplicates and orders the actions", async () => {
		expect(
			await lockActionsFor(repo.dir, [
				"shabti_cli/package.json",
				"docker_containers/shabti_api/pyproject.toml",
				"package.json",
				"pyproject.toml",
			]),
		).toEqual(["uv-host", "uv-docker", "bun"]);
	});

	test("refuses a package.json outside the workspace rather than guessing", async () => {
		await expect(
			lockActionsFor(repo.dir, ["elsewhere/package.json"]),
		).rejects.toThrow(/not in the root bun workspace/);
	});
});

describe("regenerate", () => {
	// the commands are recorded rather than run, so no test starts Docker
	afterEach(() => {
		mock.restore();
	});

	test("runs each action once, in order, in the repo root", async () => {
		const ran: { command: string[]; cwd: string }[] = [];
		spyOn(git, "run").mockImplementation(async (command, options) => {
			ran.push({ command, cwd: options.cwd });
			return { exitCode: 0, stdout: "", stderr: "" };
		});
		expect(await regenerate(repo.dir, ["uv-host", "uv-docker", "bun"])).toEqual(
			["uv lock", "bun run lock", "bun install --lockfile-only"],
		);
		expect(ran.map(({ command }) => command)).toEqual([
			["uv", "lock"],
			["bun", "run", "lock"],
			["bun", "install", "--lockfile-only"],
		]);
		expect(ran.every(({ cwd }) => cwd === repo.dir)).toBe(true);
	});

	test("mentions UV_LOCK_USER when the Docker lock fails", async () => {
		spyOn(git, "run").mockRejectedValue(new Error("permission denied"));
		await expect(regenerate(repo.dir, ["uv-docker"])).rejects.toThrow(
			/UV_LOCK_USER/,
		);
	});

	test("says plainly which command failed", async () => {
		spyOn(git, "run").mockRejectedValue(new Error("no uv on PATH"));
		await expect(regenerate(repo.dir, ["uv-host"])).rejects.toThrow(
			/uv lock failed/,
		);
	});
});

describe("commandFor", () => {
	test("names what --no-lock skipped", () => {
		expect(commandFor("bun")).toBe("bun install --lockfile-only");
		expect(commandFor("uv-docker")).toBe("bun run lock");
	});
});
