import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import buildImages from "../server/buildImages";
import { resetConnectivity } from "../server/connectivity";
import { ImageBuildError } from "../server/errors";
import { dockerState, mockDocker, mockFetch, reachableHosts } from "./mocks";

let docker: ReturnType<typeof dockerState>;

beforeEach(() => {
	resetConnectivity();
	docker = dockerState();
	docker.services = {
		tika: { image: "apache/tika:3.3.0.0-full" },
		shabti: { build: { context: "." }, image: "shabti-shabti" },
		"shabti-web": { build: { context: "." }, image: "shabti-shabti-web" },
	};
	mockDocker(docker);
});

afterEach(() => {
	mock.restore();
});

describe("buildImages", () => {
	test("reports a rebuild when the build goes through", async () => {
		expect(await buildImages("compose.yml", {})).toBe(true);
	});

	test("leaves the build's own error to explain a failure online", async () => {
		docker.buildSucceeds = false;
		mockFetch(reachableHosts("https://huggingface.co"));
		const error = await buildImages("compose.yml", {}).catch((e) => e);
		expect(error).not.toBeInstanceOf(ImageBuildError);
		expect(error.exitCode).toBe(1);
	});

	test("carries on with the last build when offline", async () => {
		docker.buildSucceeds = false;
		docker.images = new Set(["shabti-shabti", "shabti-shabti-web"]);
		mockFetch(() => undefined);
		expect(await buildImages("compose.yml", {})).toBe(false);
	});

	test("names the images that were never built", async () => {
		docker.buildSucceeds = false;
		docker.images = new Set(["shabti-shabti"]);
		mockFetch(() => undefined);
		const error = await buildImages("compose.yml", {}).catch((e) => e);
		expect(error).toBeInstanceOf(ImageBuildError);
		expect(error.message).toContain("shabti-shabti-web");
		expect(error.message).not.toContain("apache/tika");
	});
});
