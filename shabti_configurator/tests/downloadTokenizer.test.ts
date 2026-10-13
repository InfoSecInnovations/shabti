import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import path from "node:path";
import {
	fetchTokenizer,
	getTokenizerPath,
	writeTokenizer,
} from "../server/downloadTokenizer";
import { mockFetch, useTempCwd } from "./mocks";

const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
});

afterEach(async () => {
	mock.restore();
	await cwd.leave();
});

describe("the embeddings tokenizer", () => {
	test("comes from the repository's tokenizer.json", async () => {
		const asked: string[] = [];
		mockFetch((url) => {
			asked.push(url);
			return new Response('{"model": {}}');
		});
		expect(await fetchTokenizer("BAAI/bge-m3")).toBe('{"model": {}}');
		expect(asked).toEqual([
			"https://huggingface.co/BAAI/bge-m3/resolve/main/tokenizer.json",
		]);
	});

	test("says when the repository has none to give", async () => {
		mockFetch(() => new Response("not found", { status: 404 }));
		await expect(fetchTokenizer("BAAI/bge-m3")).rejects.toThrow("status 404");
	});

	test("says when Hugging Face can't be reached", async () => {
		mockFetch(() => undefined);
		await expect(fetchTokenizer("BAAI/bge-m3")).rejects.toThrow(
			"Couldn't download the tokenizer",
		);
	});

	test("goes where the API looks for it", async () => {
		const written = await writeTokenizer("BAAI/bge-m3", "{}");
		// next to the model settings, with the repository's slash as a double dash
		expect(written).toBe(getTokenizerPath("BAAI/bge-m3"));
		expect(written).toEndWith(
			path.join("llama_models", "tokenizers", "BAAI--bge-m3.json"),
		);
		expect(await Bun.file(written).text()).toBe("{}");
	});
});
