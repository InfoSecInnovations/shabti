import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import ensureImages from "../server/ensureImages";
import { ImagesUnavailableError } from "../server/errors";
import { dockerState, mockDocker } from "./mocks";

let docker: ReturnType<typeof dockerState>;

beforeEach(() => {
	docker = dockerState();
	docker.services = {
		tika: { image: "apache/tika:3.3.0.0-full" },
		"opensearch-node1": { image: "opensearchproject/opensearch:3.5.0" },
		// the dev compose file builds these rather than pulling them
		shabti: { build: { context: "." } },
	};
	mockDocker(docker);
});

afterEach(() => {
	mock.restore();
});

describe("ensureImages", () => {
	test("reports an update when the pull goes through", async () => {
		expect(await ensureImages("compose.yml", {})).toBe(true);
	});

	test("carries on with the local images when the pull fails", async () => {
		docker.pullSucceeds = false;
		docker.images = new Set([
			"apache/tika:3.3.0.0-full",
			"opensearchproject/opensearch:3.5.0",
		]);
		expect(await ensureImages("compose.yml", {})).toBe(false);
	});

	test("names the images that are missing", async () => {
		docker.pullSucceeds = false;
		docker.images = new Set(["apache/tika:3.3.0.0-full"]);
		const error = await ensureImages("compose.yml", {}).catch((e) => e);
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
		expect(await ensureImages("compose.yml", {}, true)).toBe(false);
	});
});
