import { describe, expect, test } from "bun:test";
import { useRepo } from "../../versioning/tests/fixture";
import { group, readPins } from "../read";
import { applyEdits, plannedEdits } from "../rewrite";
import type { Dependency } from "../types";

const ROOT_PACKAGE = `{
	"name": "shabti",
	"private": true,
	"devDependencies": {
		"@biomejs/biome": "1.9.4"
	},
	"dependencies": {
		"commander": "^15.0.0",
		"dotenv": "^17.2.2"
	},
	"version": "0.8.0"
}
`;

const CLI_PACKAGE = `{
	"name": "shabti_cli",
	"dependencies": {
		"commander": "^14.0.2",
		"dotenv": "^16.5.0"
	},
	"version": "0.1.0"
}
`;

/** no trailing newline, the shape python_packages/shabti_util uses */
const API_PYPROJECT = `[project]
name = "shabti-api"
version = "0.1.0"
dependencies = [
    "fastapi[standard]~=0.136.3",
    "requests>=2,<3",
    "zxcvbn",
    "tika~=3.1.0 ; sys_platform != 'win32'",
]

[dependency-groups]
dev = [
    "pytest~=8.4.2",
]`;

const DOCKERFILE = `FROM astral/uv:0.11.1-python3.14-trixie-slim AS base
WORKDIR /app

FROM base AS local
RUN uv sync --locked
`;

const BUN_DOCKERFILE = `FROM debian:13-slim

COPY --from=oven/bun:1.3.11-slim /usr/local/bin/bun /usr/local/bin/bun
`;

const UV_LOCK_COMPOSE = `services:
  uv-lock:
    # keep this in step with the FROM lines in shabti_api/Dockerfile and shabti_web/Dockerfile
    image: astral/uv:0.11.1-python3.14-trixie-slim
    command: ["sh", "-c", "uv lock"]
`;

const SERVICES_COMPOSE = `services:
  postgres:
    # Name the cluster
    image: postgres:18.3
  tika:
    image: "apache/tika:3.3.0.0-full"
  llama:
    image: ghcr.io/ggml-org/llama.cpp:server-cuda-b9843
  zip:
    image: javieraviles/zip
  pinned:
    image: opensearchproject/opensearch@sha256:abcdef
`;

const FILES = {
	"package.json": ROOT_PACKAGE,
	"shabti_cli/package.json": CLI_PACKAGE,
	"docker_containers/shabti_api/pyproject.toml": API_PYPROJECT,
	"docker_containers/shabti_api/Dockerfile": DOCKERFILE,
	"testing/images/Dockerfile.bun": BUN_DOCKERFILE,
	"docker_containers/docker-compose-uv-lock.yml": UV_LOCK_COMPOSE,
	"docker_compose/docker-compose-services.yml": SERVICES_COMPOSE,
};

describe("rewriting", () => {
	const repo = useRepo({ packages: [], files: FILES });

	const find = async (name: string) => {
		const dependency = group(await readPins(repo.dir)).find(
			(candidate) => candidate.name === name,
		);
		if (!dependency) throw new Error(`fixture has no ${name}`);
		return dependency;
	};

	const setTo = async (name: string, version: string, literalTag?: boolean) => {
		const dependency = await find(name);
		return applyEdits(
			repo.dir,
			plannedEdits(dependency.occurrences, version, { literalTag }),
		);
	};

	test("turns a compatible pin into an exact one and keeps the extras", async () => {
		expect(await setTo("fastapi", "0.137.0")).toEqual([
			"docker_containers/shabti_api/pyproject.toml",
		]);
		expect(
			await repo.read("docker_containers/shabti_api/pyproject.toml"),
		).toContain('"fastapi[standard]==0.137.0",');
	});

	test("replaces a whole multi clause specifier", async () => {
		await setTo("requests", "2.33.1");
		expect(
			await repo.read("docker_containers/shabti_api/pyproject.toml"),
		).toContain('"requests==2.33.1",');
	});

	test("adds a specifier to a requirement that had none", async () => {
		await setTo("zxcvbn", "4.5.0");
		expect(
			await repo.read("docker_containers/shabti_api/pyproject.toml"),
		).toContain('"zxcvbn==4.5.0",');
	});

	test("keeps an environment marker", async () => {
		await setTo("tika", "3.3.0");
		expect(
			await repo.read("docker_containers/shabti_api/pyproject.toml"),
		).toContain("\"tika==3.3.0; sys_platform != 'win32'\",");
	});

	test("leaves a file with no trailing newline without one", async () => {
		await setTo("pytest", "8.4.3");
		const text = await repo.read("docker_containers/shabti_api/pyproject.toml");
		expect(text).toContain('"pytest==8.4.3",');
		expect(text.endsWith("]")).toBe(true);
	});

	test("keeps node indentation, key order and the trailing newline", async () => {
		await setTo("@biomejs/biome", "2.0.0");
		expect(await repo.read("package.json")).toBe(
			ROOT_PACKAGE.replace('"1.9.4"', '"2.0.0"'),
		);
	});

	test("converges a dependency its files disagreed about", async () => {
		const commander = await find("commander");
		expect(commander.agreement).toBe("diverged");
		expect(commander.versions).toEqual([]);
		expect(await setTo("commander", "15.1.0")).toEqual([
			"package.json",
			"shabti_cli/package.json",
		]);
		expect(await repo.read("package.json")).toContain('"commander": "15.1.0"');
		expect(await repo.read("shabti_cli/package.json")).toContain(
			'"commander": "15.1.0"',
		);
	});

	test("changes every file an image is pinned in, keeping the label", async () => {
		expect(await setTo("astral/uv", "0.12.3")).toEqual([
			"docker_containers/docker-compose-uv-lock.yml",
			"docker_containers/shabti_api/Dockerfile",
		]);
		const compose = await repo.read(
			"docker_containers/docker-compose-uv-lock.yml",
		);
		expect(compose).toContain("image: astral/uv:0.12.3-python3.14-trixie-slim");
		// the invariant this pin used to rely on is the reason Dockerfiles are in scope at all
		expect(compose).toContain(
			"# keep this in step with the FROM lines in shabti_api/Dockerfile",
		);
		expect(
			await repo.read("docker_containers/shabti_api/Dockerfile"),
		).toStartWith("FROM astral/uv:0.12.3-python3.14-trixie-slim AS base");
	});

	test("rewrites the image a COPY --from names, and nothing else on the line", async () => {
		expect(await setTo("oven/bun", "1.3.12")).toEqual([
			"testing/images/Dockerfile.bun",
		]);
		expect(await repo.read("testing/images/Dockerfile.bun")).toBe(
			BUN_DOCKERFILE.replace("oven/bun:1.3.11-slim", "oven/bun:1.3.12-slim"),
		);
	});

	test("keeps a quoted image reference quoted, and its label", async () => {
		await setTo("apache/tika", "4.0.0");
		expect(
			await repo.read("docker_compose/docker-compose-services.yml"),
		).toContain('image: "apache/tika:4.0.0-full"');
	});

	test("adds a tag to an untagged image", async () => {
		await setTo("javieraviles/zip", "3.0");
		expect(
			await repo.read("docker_compose/docker-compose-services.yml"),
		).toContain("image: javieraviles/zip:3.0");
	});

	test("substitutes a build counter into its prefix", async () => {
		await setTo("ghcr.io/ggml-org/llama.cpp", "b10412");
		expect(
			await repo.read("docker_compose/docker-compose-services.yml"),
		).toContain("image: ghcr.io/ggml-org/llama.cpp:server-cuda-b10412");
	});

	test("does nothing when the pin is already the version asked for", async () => {
		expect(await setTo("postgres", "18.3")).toEqual([]);
		expect(await (await repo.git("status", "--porcelain")).stdout.trim()).toBe(
			"",
		);
	});
});

describe("refusals", () => {
	const repo = useRepo({ packages: [], files: FILES });

	const occurrences = async (name: string) => {
		const dependency = group(await readPins(repo.dir)).find(
			(candidate) => candidate.name === name,
		) as Dependency;
		return dependency.occurrences;
	};

	test("refuses a digest rather than dropping it", async () => {
		// awaited out here rather than inside the assertion: plannedEdits is synchronous, and an async
		// arrow would turn the refusal into a rejected promise that toThrow cannot see
		const pins = await occurrences("opensearchproject/opensearch");
		expect(() => plannedEdits(pins, "3.5.0")).toThrow(/pins .* by digest/);
	});

	test("refuses a tag with no version, and names the way out", async () => {
		const pin = (await occurrences("ghcr.io/ggml-org/llama.cpp"))[0];
		if (!pin?.image) throw new Error("fixture has no llama.cpp image");
		// the real tag has a build counter, so an opaque one has to be constructed
		const opaque = [
			{
				...pin,
				raw: "ghcr.io/ggml-org/llama.cpp:nightly",
				image: { ...pin.image, tag: "nightly" },
			},
		];
		expect(() => plannedEdits(opaque, "b1")).toThrow(/pass --tag/);
	});

	test("accepts an opaque tag when told to write it literally", async () => {
		const pins = await occurrences("ghcr.io/ggml-org/llama.cpp");
		const edits = plannedEdits(pins, "server-cuda-b10412", {
			literalTag: true,
		});
		await applyEdits(repo.dir, edits);
		expect(
			await repo.read("docker_compose/docker-compose-services.yml"),
		).toContain("image: ghcr.io/ggml-org/llama.cpp:server-cuda-b10412");
	});

	test("writes nothing at all when one file does not match", async () => {
		const pins = await occurrences("astral/uv");
		const edits = plannedEdits(pins, "0.12.3");
		const before = await Promise.all([
			repo.read("docker_containers/docker-compose-uv-lock.yml"),
			repo.read("docker_containers/shabti_api/Dockerfile"),
		]);
		await expect(
			applyEdits(repo.dir, [
				...edits,
				{
					file: "package.json",
					find: /never-appears-anywhere/g,
					replace: "x",
					count: 3,
					from: "never-appears-anywhere",
					to: "x",
				},
			]),
		).rejects.toThrow(/expected 3 occurrences/);
		expect(
			await Promise.all([
				repo.read("docker_containers/docker-compose-uv-lock.yml"),
				repo.read("docker_containers/shabti_api/Dockerfile"),
			]),
		).toEqual(before);
		expect((await repo.git("status", "--porcelain")).stdout.trim()).toBe("");
	});
});
