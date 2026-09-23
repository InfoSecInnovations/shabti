import { afterEach, describe, expect, mock, test } from "bun:test";
import { verdict } from "../catalogue";
import { client } from "../http";
import { npmCatalogue } from "../registries/npm";
import { ociCatalogue, target } from "../registries/oci";
import { pypiCatalogue } from "../registries/pypi";
import { type TagShape, shapeOf } from "../tag";
import type { Catalogue, Dependency } from "../types";
import { type Route, abbreviated, httpStub, simple } from "./stub";

// a canned failure is the answer under test, not something to wait out
const offline = { retries: 0 };

afterEach(() => {
	mock.restore();
});

/** every one of the three hosts answers an unauthenticated request with this same shape of challenge */
const CHALLENGE = {
	status: 401,
	headers: {
		"www-authenticate":
			'Bearer realm="https://auth.docker.io/token",service="registry.docker.io"',
	},
};

const hubRoutes = (tags: string[]): Route[] => [
	[
		/^https:\/\/registry-1\.docker\.io\/v2\/.*tags\/list/,
		(_url, init) =>
			new Headers(init?.headers ?? {}).get("authorization")
				? { body: { tags } }
				: CHALLENGE,
	],
	[/^https:\/\/auth\.docker\.io\/token/, { body: { token: "T" } }],
];

describe("pypi", () => {
	test("asks the simple API with the right header and a normalised name", async () => {
		const stub = httpStub([
			["https://pypi.org/simple/opensearch-py/", { body: simple(["3.0.0"]) }],
		]);
		await pypiCatalogue("OpenSearch.Py", client(offline));
		expect(stub.urls()).toEqual(["https://pypi.org/simple/opensearch-py/"]);
		expect(stub.headersOf(0).get("accept")).toBe(
			"application/vnd.pypi.simple.v1+json",
		);
	});

	test("finds the latest stable and the prerelease above it", async () => {
		httpStub([
			[
				/simple\/shiny/,
				{
					body: simple(["0.1", "1.6.3", "1.7.0", "1.8.0a1", "1.5.0"]),
				},
			],
		]);
		const catalogue = await pypiCatalogue("shiny", client(offline));
		expect(catalogue.latestStable?.version).toBe("1.7.0");
		expect(catalogue.latestPrerelease?.version).toBe("1.8.0a1");
		// ascending, so a report can count how far behind a pin is
		expect(catalogue.releases.map((release) => release.version)).toEqual([
			"0.1",
			"1.5.0",
			"1.6.3",
			"1.7.0",
			"1.8.0a1",
		]);
	});

	test("marks a version yanked only when every one of its files is", async () => {
		httpStub([
			[
				/simple\/shiny/,
				{
					body: simple(
						["0.1", "0.2.1", "1.7.0"],
						[
							// fully yanked, yet still listed in versions - verified live on shiny
							{ filename: "shiny-0.1.tar.gz", yanked: true },
							{ filename: "shiny-0.2.1-py3-none-any.whl", yanked: true },
							{ filename: "shiny-0.2.1.tar.gz", yanked: "broken metadata" },
							// one yanked wheel of an otherwise fine release
							{ filename: "shiny-1.7.0-py3-none-any.whl", yanked: true },
							{ filename: "shiny-1.7.0.tar.gz" },
						],
					),
				},
			],
		]);
		const catalogue = await pypiCatalogue("shiny", client(offline));
		const withdrawn = Object.fromEntries(
			catalogue.releases.map((release) => [release.version, release.withdrawn]),
		);
		expect(withdrawn["0.1"]).toBe("yanked");
		expect(withdrawn["0.2.1"]).toBe("yanked: broken metadata");
		expect(withdrawn["1.7.0"]).toBeUndefined();
		// a withdrawn release is kept in the list but never offered as somewhere to go
		expect(catalogue.latestStable?.version).toBe("1.7.0");
	});

	test("notes a project that is no longer active", async () => {
		httpStub([
			[
				/simple\//,
				{
					body: simple(["1.0"], [], {
						"project-status": { status: "archived" },
					}),
				},
			],
		]);
		const catalogue = await pypiCatalogue("x", client(offline));
		expect(catalogue.notes).toContain("the project is marked archived on PyPI");
	});

	test("counts unreadable versions once instead of dropping them silently", async () => {
		// 2020.05.01-beta would not do here: it looks legacy but parses cleanly as 2020.5.1b0
		httpStub([
			[
				/simple\//,
				{ body: simple(["1.0", "not-a-version", "1.0.0-SNAPSHOT"]) },
			],
		]);
		const catalogue = await pypiCatalogue("x", client(offline));
		expect(catalogue.releases).toHaveLength(1);
		expect(catalogue.notes).toContain(
			"2 versions from PyPI could not be read as a version",
		);
	});

	test("says a package is not published on a 404", async () => {
		httpStub([[/simple\//, { status: 404 }]]);
		await expect(pypiCatalogue("nope", client(offline))).rejects.toThrow(
			/not published on PyPI/,
		);
	});
});

describe("npm", () => {
	test("encodes a scoped name and asks for abbreviated metadata", async () => {
		const stub = httpStub([
			[
				"https://registry.npmjs.org/@biomejs%2Fbiome",
				{ body: abbreviated({ "1.9.4": {} }, { latest: "1.9.4" }) },
			],
		]);
		await npmCatalogue("@biomejs/biome", client(offline));
		expect(stub.headersOf(0).get("accept")).toBe(
			"application/vnd.npm.install-v1+json",
		);
	});

	test("takes the latest dist-tag as the answer", async () => {
		httpStub([
			[
				/commander/,
				{
					body: abbreviated(
						{ "14.0.2": {}, "15.0.0": {}, "15.0.0-0": {} },
						{ latest: "15.0.0", next: "15.0.0-0", "2_x": "2.20.3" },
					),
				},
			],
		]);
		const catalogue = await npmCatalogue("commander", client(offline));
		expect(catalogue.latestStable?.version).toBe("15.0.0");
		expect(catalogue.distTags).toEqual({
			latest: "15.0.0",
			next: "15.0.0-0",
			"2_x": "2.20.3",
		});
	});

	test("falls back when the registry tags a prerelease as latest", async () => {
		// verified live: @jsr/std__ini tags 1.0.0-rc.9 as latest
		httpStub([
			[
				/std__ini/,
				{
					body: abbreviated(
						{ "0.213.0": {}, "1.0.0-rc.9": {} },
						{ latest: "1.0.0-rc.9" },
					),
				},
			],
		]);
		const catalogue = await npmCatalogue("@jsr/std__ini", client(offline));
		expect(catalogue.latestStable?.version).toBe("0.213.0");
		expect(catalogue.latestPrerelease?.version).toBe("1.0.0-rc.9");
		expect(catalogue.notes[0]).toMatch(/tags 1\.0\.0-rc\.9 as latest/);
	});

	test("keeps the dist-tag but says when something higher exists", async () => {
		httpStub([
			[
				/x/,
				{
					body: abbreviated({ "1.0.0": {}, "2.0.0": {} }, { latest: "1.0.0" }),
				},
			],
		]);
		const catalogue = await npmCatalogue("x", client(offline));
		expect(catalogue.latestStable?.version).toBe("1.0.0");
		expect(catalogue.notes[0]).toMatch(/but 2\.0\.0 is higher/);
	});

	test("treats a deprecated version as withdrawn", async () => {
		httpStub([
			[
				/x/,
				{
					body: abbreviated(
						{ "1.0.0": {}, "2.0.0": { deprecated: "use y instead" } },
						{ latest: "2.0.0" },
					),
				},
			],
		]);
		const catalogue = await npmCatalogue("x", client(offline));
		expect(catalogue.latestStable?.version).toBe("1.0.0");
		expect(catalogue.releases[1]?.withdrawn).toBe("deprecated: use y instead");
	});

	test("says a package is not published on a 404", async () => {
		httpStub([[/registry\.npmjs/, { status: 404 }]]);
		await expect(npmCatalogue("nope", client(offline))).rejects.toThrow(
			/not published on npm/,
		);
	});
});

describe("oci", () => {
	test("applies Docker Hub's library namespace and its own API host", () => {
		expect(target({ registry: null, repository: "postgres" })).toEqual({
			host: "registry-1.docker.io",
			repository: "library/postgres",
		});
		expect(target({ registry: null, repository: "astral/uv" })).toEqual({
			host: "registry-1.docker.io",
			repository: "astral/uv",
		});
		expect(
			target({ registry: "quay.io", repository: "keycloak/keycloak" }),
		).toEqual({ host: "quay.io", repository: "keycloak/keycloak" });
		expect(target({ registry: "docker.io", repository: "postgres" })).toEqual({
			host: "registry-1.docker.io",
			repository: "library/postgres",
		});
	});

	test("takes a token from the realm the challenge names, then lists tags", async () => {
		const stub = httpStub([...hubRoutes(["18.3", "19.1", "latest"])]);
		const catalogue = await ociCatalogue(
			{ registry: null, repository: "postgres", tag: "18.3", digest: null },
			shapeOf("18.3") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		expect(catalogue.latestStable?.version).toBe("19.1");
		const urls = stub.urls();
		expect(urls[0]).toBe(
			"https://registry-1.docker.io/v2/library/postgres/tags/list?n=1000",
		);
		// the realm is a different host from the API, and always n=1000
		expect(urls[1]).toStartWith("https://auth.docker.io/token?");
		expect(urls[1]).toContain("service=registry.docker.io");
		expect(urls[1]).toContain("scope=repository%3Alibrary%2Fpostgres%3Apull");
		expect(stub.headersOf(2).get("authorization")).toBe("Bearer T");
	});

	test("follows the Link header across pages", async () => {
		let page = 0;
		httpStub([
			[
				/tags\/list/,
				(_url, init) => {
					if (!new Headers(init?.headers ?? {}).get("authorization"))
						return CHALLENGE;
					page++;
					return page === 1
						? {
								body: { tags: ["server-cuda-b9843"] },
								headers: {
									link: '</v2/ggml-org/llama.cpp/tags/list?n=1000&last=x>; rel="next"',
								},
							}
						: { body: { tags: ["server-cuda-b10412"] } };
				},
			],
			[/token/, { body: { token: "T" } }],
		]);
		const catalogue = await ociCatalogue(
			{
				registry: "ghcr.io",
				repository: "ggml-org/llama.cpp",
				tag: "server-cuda-b9843",
				digest: null,
			},
			shapeOf("server-cuda-b9843") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		expect(catalogue.latestStable?.version).toBe("b10412");
		expect(catalogue.releases).toHaveLength(2);
	});

	test("says a missing repository is unreadable rather than deleted", async () => {
		// verified: Docker Hub and Quay answer 401 and GHCR answers 403, never 404
		for (const status of [401, 403]) {
			httpStub([
				[
					/tags\/list/,
					(_url, init) =>
						new Headers(init?.headers ?? {}).get("authorization")
							? { status }
							: CHALLENGE,
				],
				[/token/, { body: { token: "T" } }],
			]);
			await expect(
				ociCatalogue(
					{ registry: null, repository: "gone", tag: "1.0", digest: null },
					shapeOf("1.0") as TagShape,
					client(offline),
					{ resolveLatest: false },
				),
			).rejects.toThrow(/not readable anonymously/);
		}
	});

	test("says what it held constant, and what it could not find", async () => {
		httpStub([
			...hubRoutes([
				"0.11.1-python3.14-trixie-slim",
				"0.12.3-python3.14-trixie-slim",
				"0.13.0",
			]),
		]);
		const catalogue = await ociCatalogue(
			{
				registry: null,
				repository: "astral/uv",
				tag: "0.11.1-python3.14-trixie-slim",
				digest: null,
			},
			shapeOf("0.11.1-python3.14-trixie-slim") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		expect(catalogue.label).toBe("-python3.14-trixie-slim");
		expect(catalogue.latestStable?.version).toBe("0.12.3");
		expect(catalogue.notes.join(" ")).toContain(
			'held "-python3.14-trixie-slim" constant',
		);
		expect(catalogue.notes.join(" ")).toContain(
			"the newest plain version is 0.13.0, which was never published for this variant",
		);
	});

	test("resolves what latest points at when asked", async () => {
		const digests: Record<string, string> = {
			latest: "sha256:aaa",
			"19.1": "sha256:aaa",
			"18.3": "sha256:bbb",
		};
		httpStub([
			[
				/manifests\//,
				(url, init) => {
					if (!new Headers(init?.headers ?? {}).get("authorization"))
						return CHALLENGE;
					const tag = url.split("/manifests/")[1] as string;
					const digest = digests[tag];
					return digest
						? { headers: { "docker-content-digest": digest } }
						: { status: 404 };
				},
			],
			...hubRoutes(["18.3", "19.1", "latest"]),
		]);
		const catalogue = await ociCatalogue(
			{ registry: null, repository: "postgres", tag: "18.3", digest: null },
			shapeOf("18.3") as TagShape,
			client(offline),
		);
		expect(catalogue.distTags).toEqual({ latest: "19.1" });
	});

	test("does not ask about latest when told not to", async () => {
		const stub = httpStub([...hubRoutes(["18.3", "19.1", "latest"])]);
		await ociCatalogue(
			{ registry: null, repository: "postgres", tag: "18.3", digest: null },
			shapeOf("18.3") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		expect(stub.urls().some((url) => url.includes("/manifests/"))).toBe(false);
	});
});

/**
 * Every catalogue is judged by verdict(), and nothing else here exercises that pairing: the fixtures in
 * catalogue.test.ts build their own catalogues, and they happen to build them the way assemble() does.
 * That is precisely what let the OCI catalogue hand back a latestStable that was equal to a release in
 * its own list without being the same object, so every identity based lookup found nothing and every
 * image reported as up to date. These tests go registry -> verdict for all three ecosystems.
 */
describe("verdict against a catalogue a registry built", () => {
	const dependency = (
		ecosystem: Dependency["ecosystem"],
		id: string,
		version: string,
	): Dependency => ({
		id,
		ecosystem,
		name: id,
		occurrences: [],
		versions: [version],
		agreement: "agreed",
		precision: "exact",
	});

	const consistent = (catalogue: Catalogue) => {
		// the property the bug violated: whatever is named latest must be one of the listed releases
		if (catalogue.latestStable)
			expect(catalogue.releases).toContain(catalogue.latestStable);
		if (catalogue.latestPrerelease)
			expect(catalogue.releases).toContain(catalogue.latestPrerelease);
	};

	test("sees a container image is behind", async () => {
		// the real astral/uv shape: a labelled pin, and a stream tag with no counterpart for the label
		httpStub([
			...hubRoutes([
				"latest",
				"0.11.1",
				"0.12.0",
				"0.12.5",
				"0.11.1-python3.14-trixie-slim",
				"0.12.0-python3.14-trixie-slim",
				"0.12.5-python3.14-trixie-slim",
			]),
		]);
		const catalogue = await ociCatalogue(
			{
				registry: null,
				repository: "astral/uv",
				tag: "0.11.1-python3.14-trixie-slim",
				digest: null,
			},
			shapeOf("0.11.1-python3.14-trixie-slim") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		consistent(catalogue);
		expect(catalogue.latestStable?.version).toBe("0.12.5");
		// the whole tag is what the report shows and what deps:set accepts
		expect(catalogue.latestStable?.raw).toBe("0.12.5-python3.14-trixie-slim");

		const result = verdict(
			dependency("docker", "astral/uv", "0.11.1"),
			catalogue,
		);
		expect(result.behind).toBe(true);
		expect(result.behindBy).toBe(2);
	});

	test("sees an image pinned by a build counter is behind", async () => {
		httpStub([
			...hubRoutes([
				"server-cuda-b9843",
				"server-cuda-b10412",
				"server-cuda-b10615",
			]),
		]);
		const catalogue = await ociCatalogue(
			{
				registry: null,
				repository: "ggml-org/llama.cpp",
				tag: "server-cuda-b9843",
				digest: null,
			},
			shapeOf("server-cuda-b9843") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		consistent(catalogue);
		expect(catalogue.latestStable?.raw).toBe("server-cuda-b10615");
		expect(
			verdict(dependency("docker", "ggml-org/llama.cpp", "b9843"), catalogue)
				.behind,
		).toBe(true);
	});

	test("sees an image that is already current", async () => {
		httpStub([...hubRoutes(["18.3", "18.2"])]);
		const catalogue = await ociCatalogue(
			{ registry: null, repository: "postgres", tag: "18.3", digest: null },
			shapeOf("18.3") as TagShape,
			client(offline),
			{ resolveLatest: false },
		);
		consistent(catalogue);
		expect(
			verdict(dependency("docker", "postgres", "18.3"), catalogue).behind,
		).toBe(false);
	});

	test("sees a python package is behind", async () => {
		httpStub([[/simple\//, { body: simple(["1.6.3", "1.7.0", "1.8.0a1"]) }]]);
		const catalogue = await pypiCatalogue("shiny", client(offline));
		consistent(catalogue);
		const result = verdict(dependency("python", "shiny", "1.6.3"), catalogue);
		expect(result.behind).toBe(true);
		expect(result.behindBy).toBe(1);
	});

	test("sees a node package is behind", async () => {
		httpStub([
			[
				/commander/,
				{
					body: abbreviated(
						{ "14.0.2": {}, "15.0.0": {}, "15.1.0": {} },
						{ latest: "15.1.0" },
					),
				},
			],
		]);
		const catalogue = await npmCatalogue("commander", client(offline));
		consistent(catalogue);
		const result = verdict(
			dependency("node", "commander", "14.0.2"),
			catalogue,
		);
		expect(result.behind).toBe(true);
		expect(result.behindBy).toBe(2);
	});
});
