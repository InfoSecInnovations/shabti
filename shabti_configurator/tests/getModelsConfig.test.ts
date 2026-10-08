import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { file } from "bun";
import * as ini from "@std/ini";
import getDefaultModelSelection from "../getDefaultModelSelection";
import getModelsConfig, { getCustomModelsPath } from "../getModelsConfig";
import shabtiModelsFile from "../shabti_models.ini" with { type: "file" };
import { useTempCwd } from "./mocks";

// an install directory, which only has a custom_models.ini once the user writes one
const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
});

afterEach(async () => {
	await cwd.leave();
});

// read off the shipped file, so this doesn't have to change whenever the catalogue does
const SHIPPED = ini.parse(await file(shabtiModelsFile).text()) as Record<
	string,
	any
>;
const BUILT_IN = Object.keys(SHIPPED);
const SHIPPED_DEFAULT = BUILT_IN.find((k) =>
	String(SHIPPED[k].tags)
		.split(",")
		.some((tag) => tag.trim() == "default"),
)!;

describe("getModelsConfig", () => {
	test("is the shipped catalogue without a custom file", async () => {
		expect(Object.keys(await getModelsConfig())).toEqual(BUILT_IN);
	});

	test("adds the custom models to the shipped ones", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[my-model]\nhf = someone/my-model-GGUF:Q4_K_M\ntags = chat, default\n",
		);
		const models = await getModelsConfig();
		expect(Object.keys(models)).toEqual([...BUILT_IN, "my-model"]);
		expect(models["my-model"]).toEqual({
			hf: "someone/my-model-GGUF:Q4_K_M",
			tags: ["chat", "default"],
		});
	});

	test("keeps the shipped default without a custom one", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[my-model]\nhf = someone/my-model-GGUF:Q4_K_M\ntags = chat\n",
		);
		expect((await getModelsConfig())[SHIPPED_DEFAULT].tags).toEqual([
			"chat",
			"default",
		]);
	});

	test("a custom default takes over from the shipped one", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[my-model]\nhf = someone/my-model-GGUF:Q4_K_M\ntags = chat, default\n",
		);
		expect((await getModelsConfig())[SHIPPED_DEFAULT].tags).toEqual(["chat"]);
		expect((await getDefaultModelSelection()).defaultModel).toBe("my-model");
	});

	test("replaces a shipped model of the same name", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[all-MiniLM-L6-v2]\nhf = infosecinnovations/all-MiniLM-L6-v2-GGUF:Q4_K_M\ntags = embeddings\n",
		);
		expect((await getModelsConfig())["all-MiniLM-L6-v2"]).toEqual({
			hf: "infosecinnovations/all-MiniLM-L6-v2-GGUF:Q4_K_M",
			tags: ["embeddings"],
		});
	});
});
