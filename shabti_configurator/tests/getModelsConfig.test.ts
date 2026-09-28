import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import getDefaultModelSelection from "../getDefaultModelSelection";
import getModelsConfig, { getCustomModelsPath } from "../getModelsConfig";
import { useTempCwd } from "./mocks";

// an install directory, which only has a custom_models.ini once the user writes one
const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
});

afterEach(async () => {
	await cwd.leave();
});

const BUILT_IN = [
	"mistral7b",
	"paraphrase-multilingual",
	"snowflake-arctic",
	"qwen2.5",
];

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
		expect((await getModelsConfig()).mistral7b.tags).toEqual([
			"chat",
			"default",
		]);
	});

	test("a custom default takes over from the shipped one", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[my-model]\nhf = someone/my-model-GGUF:Q4_K_M\ntags = chat, default\n",
		);
		expect((await getModelsConfig()).mistral7b.tags).toEqual(["chat"]);
		expect((await getDefaultModelSelection()).defaultModel).toBe("my-model");
	});

	test("replaces a shipped model of the same name", async () => {
		await Bun.write(
			getCustomModelsPath(),
			"[snowflake-arctic]\nhf = Snowflake/snowflake-arctic-embed-m-v1.5:Q4_K_M\ntags = embeddings\n",
		);
		expect((await getModelsConfig())["snowflake-arctic"]).toEqual({
			hf: "Snowflake/snowflake-arctic-embed-m-v1.5:Q4_K_M",
			tags: ["embeddings"],
		});
	});
});
