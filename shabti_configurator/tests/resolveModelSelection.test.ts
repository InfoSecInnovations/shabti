import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import {
	ModelSelectionFallback,
	resolveModelSelection,
} from "../server/chatModelSelector";
import { resetConnectivity } from "../server/connectivity";
import { resetDownloadedModels } from "../server/listDownloadedModels";
import {
	HF,
	dockerState,
	mockDocker,
	mockFetch,
	reachableHosts,
	routerModels,
	useTempCwd,
} from "./mocks";

// a working directory without a my-models.ini, so the catalogue defaults are what's preselected
const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
	mockDocker(dockerState());
});

afterEach(async () => {
	mock.restore();
	resetConnectivity();
	resetDownloadedModels();
	await cwd.leave();
});

const resolve = () =>
	resolveModelSelection({ fallback: ModelSelectionFallback.DefaultModelOnly });

describe("resolveModelSelection", () => {
	test("offers the whole catalogue when online", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		const resolved = await resolve();
		expect(resolved.connectivity).toBe("online");
		expect(resolved.chatModels).toEqual(["mistral7b", "qwen2.5"]);
		expect(resolved.embeddingsModels).toEqual([
			"paraphrase-multilingual",
			"snowflake-arctic",
		]);
		expect(resolved.selectedChatModels).toEqual(["mistral7b"]);
	});

	test("offers only the downloaded models when offline", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11434/models")
				? routerModels(HF.qwen, HF.snowflake)
				: undefined,
		);
		const resolved = await resolve();
		expect(resolved.connectivity).toBe("offline");
		expect(resolved.chatModels).toEqual(["qwen2.5"]);
		expect(resolved.embeddingsModels).toEqual(["snowflake-arctic"]);
		// the catalogue default isn't downloaded, so the first one that is gets selected instead
		expect(resolved.selectedChatModels).toEqual(["qwen2.5"]);
	});

	test("offers nothing when offline and nothing is downloaded", async () => {
		mockFetch(() => undefined);
		const resolved = await resolve();
		expect(resolved.chatModels).toEqual([]);
		expect(resolved.embeddingsModels).toEqual([]);
		expect(resolved.selectedChatModels).toEqual([]);
	});
});
