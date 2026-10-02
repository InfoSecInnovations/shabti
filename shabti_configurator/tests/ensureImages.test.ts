import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import { resetConnectivity } from "../server/connectivity";
import ensureImages from "../server/ensureImages";
import { ImagesUnavailableError } from "../server/errors";
import type { OperationUpdate } from "../server/operationProtocol";
import { dockerState, mockDocker, mockFetch, reachableHosts } from "./mocks";

let docker: ReturnType<typeof dockerState>;

/** runs the pull to the end, keeping what it reported along the way */
const run = async (ignoreBuildable = false) => {
	const pull = ensureImages("compose.yml", {}, ignoreBuildable);
	const updates: OperationUpdate[] = [];
	while (true) {
		const next = await pull.next();
		if (next.done) return { updated: next.value, updates };
		updates.push(next.value);
	}
};

beforeEach(() => {
	resetConnectivity();
	docker = dockerState();
	docker.services = {
		tika: { image: "apache/tika:3.3.0.0-full" },
		"opensearch-node1": { image: "opensearchproject/opensearch:3.5.0" },
		// the dev compose file builds these rather than pulling them
		shabti: { build: { context: "." } },
	};
	mockDocker(docker);
	spyOn(Bun, "sleep").mockResolvedValue(undefined);
	// offline unless a test says otherwise, so a failed pull isn't retried
	mockFetch(() => undefined);
});

afterEach(() => {
	mock.restore();
});

describe("ensureImages", () => {
	test("reports an update when the pull goes through", async () => {
		expect((await run()).updated).toBe(true);
	});

	test("carries on with the local images when the pull fails", async () => {
		docker.pullSucceeds = false;
		docker.images = new Set([
			"apache/tika:3.3.0.0-full",
			"opensearchproject/opensearch:3.5.0",
		]);
		expect((await run()).updated).toBe(false);
		expect(docker.pulls).toBe(1);
	});

	test("names the images that are missing", async () => {
		docker.pullSucceeds = false;
		docker.images = new Set(["apache/tika:3.3.0.0-full"]);
		const error = await run().catch((e) => e);
		expect(error).toBeInstanceOf(ImagesUnavailableError);
		expect(error.message).toContain("opensearchproject/opensearch:3.5.0");
		expect(error.message).not.toContain("apache/tika");
	});

	test("doesn't expect images that get built to be there", async () => {
		docker.pullSucceeds = false;
		docker.images = new Set([
			"apache/tika:3.3.0.0-full",
			"opensearchproject/opensearch:3.5.0",
		]);
		expect((await run(true)).updated).toBe(false);
	});

	test("tries the pull again when it fails online", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		docker.pullResults = [false];
		const { updated, updates } = await run();
		expect(updated).toBe(true);
		expect(docker.pulls).toBe(2);
		expect(updates).toContain(
			"retrying the Docker image download (attempt 2 of 3)...",
		);
	});

	test("falls back to the local images once it runs out of attempts", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		docker.pullSucceeds = false;
		docker.images = new Set([
			"apache/tika:3.3.0.0-full",
			"opensearchproject/opensearch:3.5.0",
		]);
		expect((await run()).updated).toBe(false);
		expect(docker.pulls).toBe(3);
	});

	test("passes on the pull's progress", async () => {
		docker.pullEvents = [
			{ id: "Image apache/tika:3.3.0.0-full", status: "Done", text: "Pulled" },
		];
		const { updates } = await run();
		expect(updates).toContainEqual(
			expect.objectContaining({ key: "image/apache/tika:3.3.0.0-full" }),
		);
	});
});
