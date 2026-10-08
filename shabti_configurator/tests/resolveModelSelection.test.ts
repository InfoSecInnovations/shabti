import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import getDefaultModelSelection from "../getDefaultModelSelection";
import getModelsConfig from "../getModelsConfig";
import { resolveModelSelection } from "../server/chatModelSelector";
import writeModelsIni from "../server/writeModelsIni";
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

const resolve = () => resolveModelSelection();

describe("resolveModelSelection", () => {
	test("offers the whole catalogue when online", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		const resolved = await resolve();
		const catalogue = await getModelsConfig();
		// the order is checked on its own below
		const tagged = (tag: string) =>
			Object.keys(catalogue)
				.filter((k) => catalogue[k].tags.includes(tag))
				.sort();
		expect(resolved.connectivity).toBe("online");
		expect([...resolved.chatModels].sort()).toEqual(tagged("chat"));
		expect([...resolved.embeddingsModels].sort()).toEqual(tagged("embeddings"));
		// there's no my-models.ini yet, so the catalogue's default is preselected
		expect(resolved.selectedChatModels).toEqual([
			(await getDefaultModelSelection()).defaultModel!,
		]);
	});

	test("lists the models alphabetically", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		const resolved = await resolve();
		const collator = new Intl.Collator("en");
		expect(resolved.chatModels).toEqual(
			[...resolved.chatModels].sort(collator.compare),
		);
		expect(resolved.embeddingsModels).toEqual(
			[...resolved.embeddingsModels].sort(collator.compare),
		);
	});

	test("offers only the downloaded models when offline", async () => {
		mockFetch((url) =>
			url.startsWith("http://localhost:11434/models")
				? routerModels(HF.miniCpm, HF.miniLm)
				: undefined,
		);
		const resolved = await resolve();
		expect(resolved.connectivity).toBe("offline");
		expect(resolved.chatModels).toEqual(["MiniCPM5-2B"]);
		expect(resolved.embeddingsModels).toEqual(["all-MiniLM-L6-v2"]);
		// the catalogue default isn't downloaded, so the first one that is gets selected instead
		expect(resolved.selectedChatModels).toEqual(["MiniCPM5-2B"]);
	});

	test("keeps an install without a chat model that way", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		const { embeddingsModel } = await getDefaultModelSelection();
		await writeModelsIni({ chatModels: [], embeddingsModel });
		const resolved = await resolve();
		expect(resolved.selection.chatModels).toEqual([]);
		// rather than standing a chat model in for one which isn't downloaded
		expect(resolved.selectedChatModels).toEqual([]);
	});

	test("offers nothing when offline and nothing is downloaded", async () => {
		mockFetch(() => undefined);
		const resolved = await resolve();
		expect(resolved.chatModels).toEqual([]);
		expect(resolved.embeddingsModels).toEqual([]);
		expect(resolved.selectedChatModels).toEqual([]);
	});
});
