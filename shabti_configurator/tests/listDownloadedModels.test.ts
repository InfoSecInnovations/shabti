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
import {
	LlamaCppUnavailableError,
	ModelsUnavailableError,
} from "../server/errors";
import listDownloadedModels, {
	requireModels,
	resetDownloadedModels,
} from "../server/listDownloadedModels";
import {
	HF,
	dockerState,
	mockDocker,
	mockFetch,
	reachableHosts,
	routerModels,
} from "./mocks";

const LLAMA_IMAGE = "ghcr.io/ggml-org/llama.cpp:server";

let docker: ReturnType<typeof dockerState>;

beforeEach(() => {
	docker = dockerState();
	docker.services = { "llama-cpp": { image: LLAMA_IMAGE } };
	mockDocker(docker);
});

afterEach(() => {
	mock.restore();
	resetConnectivity();
	resetDownloadedModels();
});

describe("listDownloadedModels", () => {
	test("reads the cache of the running llama.cpp", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11434/models")
				? routerModels(HF.miniCpm, HF.miniLm)
				: undefined,
		);
		// the preset entry isn't a file in the cache, so it doesn't count
		expect(await listDownloadedModels()).toEqual(
			new Set([HF.miniCpm, HF.miniLm]),
		);
		expect(docker.started).toEqual([]);
	});

	test("finds nothing without a models volume", async () => {
		mockFetch(() => undefined);
		expect((await listDownloadedModels()).size).toBe(0);
		expect(docker.started).toEqual([]);
	});

	test("finds nothing without the llama.cpp image", async () => {
		mockFetch(() => undefined);
		docker.volumes.add("shabti_llama-cpp-models");
		expect((await listDownloadedModels()).size).toBe(0);
		expect(docker.started).toEqual([]);
	});

	test("starts a llama.cpp of its own when Shabti isn't running", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11435/models")
				? routerModels(HF.granite)
				: undefined,
		);
		docker.volumes.add("shabti_llama-cpp-models");
		docker.images.add(LLAMA_IMAGE);
		expect(await listDownloadedModels()).toEqual(new Set([HF.granite]));
		expect(docker.started).toEqual(["shabti-model-probe"]);
		// cleared beforehand in case an earlier probe was left behind, and afterwards
		expect(docker.removed).toEqual([
			"shabti-model-probe",
			"shabti-model-probe",
		]);
	});

	test("removes its llama.cpp even when it never answers", async () => {
		mockFetch(() => undefined);
		spyOn(Bun, "sleep").mockResolvedValue(undefined);
		docker.volumes.add("shabti_llama-cpp-models");
		docker.images.add(LLAMA_IMAGE);
		await expect(listDownloadedModels()).rejects.toBeInstanceOf(
			LlamaCppUnavailableError,
		);
		expect(docker.removed).toEqual([
			"shabti-model-probe",
			"shabti-model-probe",
		]);
	});
});

describe("requireModels", () => {
	test("doesn't check anything when online", async () => {
		const fetch = mockFetch(reachableHosts("https://huggingface.co"));
		expect(await requireModels(["Granite-4.2-3B", "all-MiniLM-L6-v2"])).toBe(
			true,
		);
		expect(
			fetch.mock.calls.some(([url]) => String(url).includes("localhost")),
		).toBe(false);
	});

	test("accepts downloaded models when offline", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11434/models")
				? routerModels(HF.miniCpm, HF.miniLm)
				: undefined,
		);
		expect(await requireModels(["MiniCPM5-2B", "all-MiniLM-L6-v2"])).toBe(
			false,
		);
	});

	test("names the models that aren't downloaded when offline", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11434/models")
				? routerModels(HF.miniCpm, HF.miniLm)
				: undefined,
		);
		const error = await requireModels([
			"Granite-4.2-3B",
			"all-MiniLM-L6-v2",
		]).catch((e) => e);
		expect(error).toBeInstanceOf(ModelsUnavailableError);
		expect(error.message).toContain("Granite-4.2-3B");
		expect(error.message).not.toContain("all-MiniLM-L6-v2");
	});
});
