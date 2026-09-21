import { afterEach, describe, expect, test } from "bun:test";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import getDefaultModelSelection from "../getDefaultModelSelection";
import readModelsIni from "../server/readModelsIni";
import writeModelsIni, {
	buildModelsConfig,
	getShabtiModelsPath,
} from "../server/writeModelsIni";

/**
 * The translation is tested against a catalogue made up here rather than the one that ships,
 * because what's being checked is how an entry is handled: llama.cpp refuses to start over a key
 * it doesn't recognise, so Shabti's own keys have to be stripped out and a typo has to be caught
 * before the file is written, and the real catalogue has nothing to exercise either path with.
 */
const catalogue = (models: Record<string, any>) =>
	Object.entries(models).reduce(
		(acc, [k, v]) => ({
			...acc,
			[k]: { ...v, tags: v.tags.split(",").map((t: string) => t.trim()) },
		}),
		{} as Record<string, any>,
	);

const CHAT = { hf: "someone/chat-gguf:Q4_K_M", tags: "chat, default" };
const EMBED = {
	hf: "someone/embed-gguf:Q4_K_M",
	tags: "embeddings",
	shabti_chunk_size: "128",
};
const SELECTION = {
	chatModels: ["chat"],
	embeddingsModel: "embed",
	defaultModel: "chat",
};

const build = (models: Record<string, any>, selection = SELECTION) =>
	buildModelsConfig(selection, catalogue(models));

describe("the preset sections llama.cpp reads", () => {
	test("carry nothing but llama.cpp's own options", () => {
		const { sections } = build({ chat: CHAT, embed: EMBED });
		// an unrecognised key is fatal to llama.cpp's preset loader rather than ignored, so one
		// leaking through wouldn't misconfigure the model, it would stop the service starting
		expect(Object.keys(sections.embed)).toEqual(["hf", "tags"]);
		expect(sections.embed.tags).toBe("embeddings");
		expect(sections.chat.tags).toBe("chat, default");
	});

	test("reject an option llama.cpp doesn't have", () => {
		// caught here, by the installer, because the alternative is a container that won't come up
		// with an error message nothing connects back to the catalogue
		expect(() =>
			build({
				chat: CHAT,
				embed: { ...EMBED, tokenizer: "someone/tokenizer" },
			}),
		).toThrow("tokenizer");
	});

	test("reject a mistyped Shabti setting rather than absorbing it", () => {
		// the prefix is what keeps a key out of the preset file, so a typo on this side doesn't
		// stop anything starting: the setting would just silently not be there
		expect(() =>
			build({ chat: CHAT, embed: { ...EMBED, shabti_chnk_size: "128" } }),
		).toThrow("shabti_chnk_size");
	});

	test("set the batch sizes globally so every model gets today's", () => {
		const { sections } = build({ chat: CHAT, embed: EMBED });
		// they live here rather than on the container's command line because the router overlays
		// its own arguments on top of every section, which would undo the override below
		expect(sections["*"]).toEqual({ "ubatch-size": 256, "batch-size": 1024 });
		expect(sections.embed["ubatch-size"]).toBeUndefined();
	});

	test("raise the batch size for a model whose chunks wouldn't fit", () => {
		const { sections } = build({
			chat: CHAT,
			embed: { ...EMBED, shabti_chunk_size: "512" },
		});
		// a whole embeddings input has to fit in one physical batch, and llama.cpp adds the
		// model's special tokens to it before measuring
		expect(sections.embed["ubatch-size"]).toBe(576);
		// only for the model that needs it: the global stays where it is to keep the chat models
		// off the batch size that froze large ingests
		expect(sections["*"]["ubatch-size"]).toBe(256);
	});

	test("leave the batch sizes alone when the catalogue sets them itself", () => {
		const { sections } = build({
			chat: CHAT,
			embed: { ...EMBED, shabti_chunk_size: "512", "ubatch-size": "1024" },
		});
		expect(sections.embed["ubatch-size"]).toBe("1024");
	});
});

describe("the settings the API reads", () => {
	test("carry the chunk size as a number", () => {
		// the ini parser hands every value back as a string, and this one is read as a number
		expect(build({ chat: CHAT, embed: EMBED }).settings).toEqual({
			embed: { chunk_size: 128 },
		});
	});

	test("fall back to a default chunk size the catalogue didn't set", () => {
		// adding an embeddings model shouldn't require knowing a number: the GGUF carries no token
		// config to read one off, and the model's trained context is an architectural ceiling
		// rather than a sensible chunk size, so a retrieval granularity is chosen here instead
		const { settings, sections } = build({
			chat: CHAT,
			embed: { hf: EMBED.hf, tags: "embeddings" },
		});
		expect(settings).toEqual({ embed: { chunk_size: 128 } });
		// and the default is small enough that the batch sizes don't have to move for it
		expect(sections.embed["ubatch-size"]).toBeUndefined();
	});

	test("prefer the catalogue's chunk size to the default", () => {
		expect(build({ chat: CHAT, embed: EMBED }).settings).toEqual({
			embed: { chunk_size: 128 },
		});
		const { settings } = build({
			chat: CHAT,
			embed: { ...EMBED, shabti_chunk_size: "256" },
		});
		expect(settings).toEqual({ embed: { chunk_size: 256 } });
	});
});

/**
 * An asymmetric model was trained with an instruction in front of a search query, and underperforms
 * measurably without it. Nothing declares what that instruction is - the GGUF repositories carry no
 * sentence-transformers config, and upstream it is inconsistently keyed and often missing for models
 * that require one anyway - so it is curated in the catalogue, where the ini parser trims every
 * value and cannot carry a newline. Quoting is what survives both.
 */
describe("the prefixes a model wants on its input", () => {
	test("keep the trailing space the ini parser would have eaten", () => {
		const { settings } = build({
			chat: CHAT,
			embed: {
				...EMBED,
				shabti_query_prefix:
					'"Represent this sentence for searching relevant passages: "',
			},
		});
		// the space is the point: without it the instruction runs into the question
		expect(settings.embed.query_prefix).toBe(
			"Represent this sentence for searching relevant passages: ",
		);
	});

	test("carry a newline, which an ini line cannot hold literally", () => {
		// how the instruct-style models write theirs
		const { settings } = build({
			chat: CHAT,
			embed: {
				...EMBED,
				// what a catalogue author writes by hand; shown built here so the encoding is the
				// point of the test rather than something to squint at
				shabti_query_prefix: JSON.stringify("Instruct: Retrieve.\nQuery: "),
			},
		});
		expect(settings.embed.query_prefix).toBe("Instruct: Retrieve.\nQuery: ");
	});

	test("can be set on the document side too", () => {
		// e5 and nomic want one on both sides. it is part of what a stored vector means, so it has
		// to be expressible before such a model is chosen rather than added to one already in use
		const { settings } = build({
			chat: CHAT,
			embed: {
				...EMBED,
				shabti_query_prefix: '"search_query: "',
				shabti_document_prefix: '"search_document: "',
			},
		});
		expect(settings.embed).toEqual({
			chunk_size: 128,
			query_prefix: "search_query: ",
			document_prefix: "search_document: ",
		});
	});

	test("stay empty rather than becoming zero", () => {
		// `Number("")` is 0, so reading every setting as a number if it looked like one would have
		// handed the API a prefix of 0 to put in front of every query
		const { settings } = build({
			chat: CHAT,
			embed: { ...EMBED, shabti_query_prefix: '""' },
		});
		expect(settings.embed.query_prefix).toBe("");
	});

	test("reject an unquoted prefix rather than trimming it", () => {
		// the failure this replaces is silent: the prefix would arrive without its trailing space
		// and retrieval would just be a bit worse
		expect(() =>
			build({
				chat: CHAT,
				embed: { ...EMBED, shabti_query_prefix: "Represent this sentence: " },
			}),
		).toThrow("shabti_query_prefix");
	});

	test("reject a chunk size that isn't a number", () => {
		expect(() =>
			build({ chat: CHAT, embed: { ...EMBED, shabti_chunk_size: "a lot" } }),
		).toThrow("shabti_chunk_size");
	});
});

describe("the files", () => {
	const dirs: string[] = [];

	afterEach(async () => {
		await Promise.all(
			dirs.splice(0).map((d) => rm(d, { recursive: true, force: true })),
		);
	});

	const writeToTemp = async () => {
		const baseDir = await mkdtemp(path.join(tmpdir(), "shabti-models-"));
		dirs.push(baseDir);
		return {
			baseDir,
			paths: await writeModelsIni(await getDefaultModelSelection(), baseDir),
		};
	};

	test("read back as the selection they were written from", async () => {
		const { baseDir } = await writeToTemp();
		// the global section isn't a model, and readModelsIni skips anything that isn't in the
		// catalogue, so it is ignored rather than mistaken for one
		expect(await readModelsIni(baseDir)).toEqual(
			await getDefaultModelSelection(),
		);
	});

	test("sit next to each other", async () => {
		const { baseDir, paths } = await writeToTemp();
		// so that mounting the one directory into the API container covers both, and the test
		// harness gets the settings from the same writeModelsIni call an install makes
		expect(paths.shabtiModelsPath).toBe(getShabtiModelsPath(baseDir));
		expect(await Bun.file(paths.shabtiModelsPath).exists()).toBe(true);
	});
});
