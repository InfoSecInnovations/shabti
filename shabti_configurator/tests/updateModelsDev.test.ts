import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import updateModelsDev, {
	getModelsDevPath,
	trim,
} from "../server/updateModelsDev";
import { json, mockFetch, useTempCwd } from "./mocks";

const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
});

afterEach(async () => {
	mock.restore();
	await cwd.leave();
});

const CATALOGUE = {
	zeta: {
		api: "https://api.zeta.test/v1",
		npm: "@ai-sdk/openai-compatible",
		models: {
			chat: {
				id: "chat",
				name: "Chat",
				limit: { context: 8000, output: 1000 },
				cost: { input: 1 },
			},
		},
	},
	alpha: { models: { solo: { name: "Solo" } } },
	empty: {},
};

const TRIMMED = {
	providers: {
		zeta: {
			api: "https://api.zeta.test/v1",
			models: { chat: { limit: { context: 8000, output: 1000 } } },
		},
		alpha: { models: { solo: {} } },
		empty: { models: {} },
	},
};

const written = async () =>
	JSON.parse(await readFile(getModelsDevPath(), "utf8"));

describe("updateModelsDev", () => {
	test("keeps only what the API reads", () => {
		expect(trim(CATALOGUE)).toEqual(TRIMMED);
	});

	test("writes the catalogue where the API mounts it", async () => {
		mockFetch((url) =>
			url == "https://models.dev/api.json" ? json(CATALOGUE) : undefined,
		);
		await updateModelsDev();
		expect(await written()).toEqual(TRIMMED);
		// nothing left over from writing it
		expect(await readdir(path.dirname(getModelsDevPath()))).toEqual([
			"models-dev.json",
		]);
	});

	test("keeps the last copy when offline", async () => {
		mockFetch(() => json(CATALOGUE));
		await updateModelsDev();
		mockFetch(() => undefined);
		await expect(updateModelsDev()).rejects.toThrow();
		expect(await written()).toEqual(TRIMMED);
	});

	test("keeps the last copy when models.dev fails", async () => {
		mockFetch(() => json(CATALOGUE));
		await updateModelsDev();
		mockFetch(() => new Response("down", { status: 503 }));
		await expect(updateModelsDev()).rejects.toThrow("503");
		expect(await written()).toEqual(TRIMMED);
	});
});
