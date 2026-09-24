import { describe, expect, test } from "bun:test";
import { composePins, dockerfilePins, idOf, parseImage } from "../docker";
import type { ImageRef } from "../types";

const ref = (
	registry: string | null,
	repository: string,
	tag: string | null,
	digest: string | null = null,
): ImageRef => ({ registry, repository, tag, digest });

describe("parseImage", () => {
	const cases: [string, ImageRef | null][] = [
		// every image the repo names today
		["postgres:18.3", ref(null, "postgres", "18.3")],
		[
			"quay.io/keycloak/keycloak:26.5.6",
			ref("quay.io", "keycloak/keycloak", "26.5.6"),
		],
		[
			// a dot in the repository half, which is why only the first segment may be a registry
			"ghcr.io/ggml-org/llama.cpp:server-cuda-b9843",
			ref("ghcr.io", "ggml-org/llama.cpp", "server-cuda-b9843"),
		],
		[
			"astral/uv:0.11.1-python3.14-trixie-slim",
			ref(null, "astral/uv", "0.11.1-python3.14-trixie-slim"),
		],
		[
			"opensearchproject/opensearch:3.5.0",
			ref(null, "opensearchproject/opensearch", "3.5.0"),
		],
		["apache/tika:3.3.0.0-full", ref(null, "apache/tika", "3.3.0.0-full")],
		// no tag at all, so docker implies latest and there is nothing pinned
		["javieraviles/zip", ref(null, "javieraviles/zip", null)],
		// a port in the registry: the tag colon is the one after the last slash
		["localhost:5000/x:1", ref("localhost:5000", "x", "1")],
		["quay.io:443/x/y:1", ref("quay.io:443", "x/y", "1")],
		["localhost:5000/x", ref("localhost:5000", "x", null)],
		["postgres@sha256:abc", ref(null, "postgres", null, "sha256:abc")],
		["postgres:18.3@sha256:abc", ref(null, "postgres", "18.3", "sha256:abc")],
		["postgres:latest", ref(null, "postgres", "latest")],
		["  postgres:18.3  ", ref(null, "postgres", "18.3")],
		// decided by the environment at run time, not by this repo
		["infosecinnovations/shabti:${SHABTI_API_VERSION:-latest}", null],
		["${IMAGE}", null],
		["", null],
		["   ", null],
	];

	for (const [reference, expected] of cases)
		test(JSON.stringify(reference), () => {
			expect(parseImage(reference)).toEqual(expected);
		});
});

describe("idOf", () => {
	test("names a Docker Hub image without a host and every other with one", () => {
		expect(idOf(ref(null, "postgres", "18.3"))).toBe("postgres");
		expect(idOf(ref(null, "astral/uv", "1"))).toBe("astral/uv");
		expect(idOf(ref("quay.io", "keycloak/keycloak", "1"))).toBe(
			"quay.io/keycloak/keycloak",
		);
	});
});

/** the real hazards together: an invariant in a comment, an unset image, an interpolation, a command */
const COMPOSE = `services:
  postgres:
    # Name the cluster
    image: postgres:18.3
    environment:
      POSTGRES_DB: keycloak
  keycloak:
    image: quay.io/keycloak/keycloak:26.5.6
  uv-lock:
    # keep this in step with the FROM lines in shabti_api/Dockerfile and shabti_web/Dockerfile
    image: astral/uv:0.11.1-python3.14-trixie-slim
    command: ["sh", "-c", "cd /app/shabti && uv lock && echo built the image"]
  tika:
    image: "apache/tika:3.3.0.0-full"
  shabti:
    image: infosecinnovations/shabti:\${SHABTI_API_VERSION:-latest}
  shabti-web:
    image: !reset null
  shabti-client:
    image: null
  zip:
    image: javieraviles/zip`;

describe("composePins", () => {
	const pins = composePins("docker-compose.yml", COMPOSE);
	const byId = (id: string) => pins.find((pin) => pin.id === id);

	test("finds every service image and nothing that is not one", () => {
		expect(pins.map((pin) => pin.id)).toEqual([
			"postgres",
			"quay.io/keycloak/keycloak",
			"astral/uv",
			"apache/tika",
			"javieraviles/zip",
		]);
	});

	test("skips an image deliberately unset to force a local build", () => {
		// docker-compose-dev.yml does this twice with !reset, which Bun's parser reads as the string
		// "null"; a text scan would find a dependency named "null" either way
		expect(pins.some((pin) => pin.id === "null")).toBe(false);
	});

	test("skips an interpolated tag", () => {
		expect(byId("infosecinnovations/shabti")).toBeUndefined();
	});

	test("is not fooled by the word image inside a command", () => {
		expect(pins).toHaveLength(5);
	});

	test("names the service each image belongs to", () => {
		expect(byId("postgres")?.location).toBe("services.postgres.image");
		expect(byId("apache/tika")?.location).toBe("services.tika.image");
	});

	test("reads a quoted reference the same as a bare one", () => {
		expect(byId("apache/tika")?.specifier).toBe("3.3.0.0-full");
		expect(byId("apache/tika")?.version).toBe("3.3.0.0");
		expect(byId("apache/tika")?.precision).toBe("exact");
	});

	test("splits the version from the label it must hold constant", () => {
		expect(byId("astral/uv")?.version).toBe("0.11.1");
		expect(byId("astral/uv")?.specifier).toBe("0.11.1-python3.14-trixie-slim");
	});

	test("reports an untagged image as unpinned", () => {
		expect(byId("javieraviles/zip")?.precision).toBe("absent");
		expect(byId("javieraviles/zip")?.version).toBeNull();
	});

	test("finds the line each image is on", () => {
		expect(byId("postgres")?.line).toBe(4);
		expect(byId("astral/uv")?.line).toBe(11);
	});

	test("refuses a compose file it cannot parse", () => {
		expect(() =>
			composePins("docker-compose.yml", "services:\n  - [oops\n"),
		).toThrow(/could not parse docker-compose\.yml/);
	});
});

/** the real shabti_api Dockerfile shape: one image, then three stages built on it */
const DOCKERFILE = `FROM astral/uv:0.11.1-python3.14-trixie-slim AS base
WORKDIR /app
COPY pyproject.toml uv.lock ./

FROM base AS local
RUN uv sync --locked

FROM base AS online
RUN uv sync --locked --no-dev --no-editable
`;

describe("dockerfilePins", () => {
	test("finds the image and skips the stages built on it", () => {
		const pins = dockerfilePins(
			"docker_containers/shabti_api/Dockerfile",
			DOCKERFILE,
		);
		expect(pins).toHaveLength(1);
		expect(pins[0]?.id).toBe("astral/uv");
		expect(pins[0]?.version).toBe("0.11.1");
		expect(pins[0]?.location).toBe("FROM");
		expect(pins[0]?.line).toBe(1);
	});

	test("skips scratch and an interpolated reference", () => {
		expect(
			dockerfilePins("Dockerfile", "FROM scratch\nFROM ${BASE}\n"),
		).toEqual([]);
	});

	test("reads a platform flag without mistaking it for the image", () => {
		const pins = dockerfilePins(
			"Dockerfile",
			"FROM --platform=linux/amd64 postgres:18.3 AS db\n",
		);
		expect(pins[0]?.id).toBe("postgres");
		expect(pins[0]?.specifier).toBe("18.3");
	});

	test("finds every image in a multi stage build", () => {
		const pins = dockerfilePins(
			"Dockerfile",
			"FROM astral/uv:0.11.1 AS build\nFROM postgres:18.3 AS db\nFROM build AS final\n",
		);
		expect(pins.map((pin) => pin.id)).toEqual(["astral/uv", "postgres"]);
	});

	test("reads an image a COPY --from takes a file out of", () => {
		const pins = dockerfilePins(
			"testing/images/Dockerfile.bun",
			"FROM astral/uv:0.11.1\n\nCOPY --from=oven/bun:1.3.11-slim /usr/local/bin/bun /usr/local/bin/bun\n",
		);
		expect(pins.map((pin) => pin.id)).toEqual(["astral/uv", "oven/bun"]);
		expect(pins[1]?.version).toBe("1.3.11");
		expect(pins[1]?.specifier).toBe("1.3.11-slim");
		expect(pins[1]?.location).toBe("COPY --from");
		expect(pins[1]?.line).toBe(3);
	});

	test("skips a COPY --from that names a stage, a build context or an index", () => {
		const pins = dockerfilePins(
			"Dockerfile",
			[
				"FROM astral/uv:0.11.1 AS build",
				"COPY --from=build /app /app",
				"COPY --from=python_packages ./shabti_util /app/shabti_util/",
				"COPY --chown=1000 --from=0 /app /app",
			].join("\n"),
		);
		expect(pins.map((pin) => pin.location)).toEqual(["FROM"]);
	});
});
