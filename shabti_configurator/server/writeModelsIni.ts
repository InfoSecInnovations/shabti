import path from "node:path";
import { rm, stat } from "node:fs/promises";
import * as ini from "@std/ini";
import { HTTPException } from "hono/http-exception";
import _ from "lodash";
import getModelsConfig from "../getModelsConfig";
import type { ModelSelection } from "../getDefaultModelSelection";

// keys the catalogue carries for Shabti's own use rather than for llama.cpp. llama.cpp treats an
// unrecognised preset key as a fatal error rather than ignoring it, so leaving one of these in the
// generated file doesn't misconfigure a model, it stops the LLM service from starting at all
const SHABTI_PREFIX = "shabti_";

// the ini parser hands back every value as a string, so each setting says how to read itself. a
// blanket "number if it looks like one" would turn an empty prefix into the number 0, and a prefix
// that happened to be all digits into a number the API would concatenate as one
const asNumber = (raw: string, where: string) => {
	const value = Number(raw);
	if (raw.trim() === "" || Number.isNaN(value))
		throw new HTTPException(400, {
			message: `${where} is ${JSON.stringify(raw)}, which is not a number`,
		});
	return value;
};

// prefixes are written as JSON string literals in the catalogue because the ini parser trims every
// value and cannot carry a newline, and the trailing space on something like "Represent this
// sentence for searching relevant passages: " is part of the prefix. quoting makes the whitespace
// explicit and a malformed one an error the installer reports rather than a wrong prefix nobody sees
const asText = (raw: string, where: string) => {
	let value: unknown;
	try {
		value = JSON.parse(raw);
	} catch {
		value = undefined;
	}
	if (typeof value !== "string")
		throw new HTTPException(400, {
			message: `${where} is ${raw}, which is not a quoted string. Prefixes are written as JSON string literals, so that their spacing is visible: "a prefix: "`,
		});
	return value;
};

// what a catalogue entry is allowed to tell Shabti about a model, and how each one is read. checked
// for the same reason as the options below, except that a typo in one of these wouldn't even stop
// the LLM service: the setting would just quietly not be there when the API came to read it
const SHABTI_KEYS: Record<string, (raw: string, where: string) => unknown> = {
	chunk_size: asNumber,
	// what the model wants in front of a search query and in front of a stored chunk. most modern
	// embedding models are asymmetric and underperform measurably without the query one, and some
	// (e5, nomic) want one on both sides. nothing declares them: the GGUF repositories carry no
	// sentence-transformers config, and where one exists upstream it is inconsistently keyed and
	// often absent for models that require a prefix anyway, so they are curated here
	query_prefix: asText,
	document_prefix: asText,
};

// which llama.cpp options a catalogue entry is allowed to set, checked so that a typo is reported
// here, by the installer, rather than as a container that won't come up
const PRESET_KEYS = [
	"hf",
	"tags",
	"ctx-size",
	"ubatch-size",
	"batch-size",
	"n-gpu-layers",
];

// the batch sizes everything runs with unless a model asks for more. deliberately small: they were
// lowered to fix a freeze on large ingests, so raising them for the embeddings model must not raise
// them for the 7B chat model too. they're written into the file's global section rather than passed
// on the container's command line because the router overlays its own CLI arguments on top of every
// preset section, which would silently undo a per model override.
// the logical batch is the less interesting of the two: the server runs every model with
// --embeddings, and that makes it lower n_batch to n_ubatch on startup, so this is really a ceiling
// the physical batch is never allowed to cross rather than a size anything runs at
const GLOBAL_SECTION = "*";
const DEFAULT_UBATCH = 256;
const DEFAULT_BATCH = 1024;

// a whole embeddings input has to fit in one physical batch: a non-causal model can't be split
// across micro batches, so llama.cpp asserts n_ubatch >= the input's token count. the chunks are
// already measured with their special tokens included, which makes a batch the size of a chunk
// exactly enough; the headroom is margin in case /tokenize and /v1/embeddings ever count the same
// text differently
const UBATCH_HEADROOM = 64;

// how many of the model's tokens one chunk may take up when the catalogue doesn't say. this is a
// retrieval granularity rather than anything the model dictates - a chunk is a reference someone
// gets shown, so it wants to be a passage rather than a whole document - and it is small enough to
// be within any embeddings model's trained context. a model whose useful sequence length differs
// sets shabti_chunk_size, and the API refuses one that exceeds what the model was trained on
const DEFAULT_CHUNK_SIZE = 128;

// the compiled installer unzips docker_compose next to the working directory,
// so this stays relative to the cwd by default like getEnvPath does
const getLlamaModelsDir = (baseDir = path.resolve()) =>
	path.join(
		baseDir,
		"docker_compose",
		"docker_compose_dependencies",
		"llama_models",
	);

export const getModelsIniPath = (baseDir = path.resolve()) =>
	path.join(getLlamaModelsDir(baseDir), "my-models.ini");

/**
 * The settings the API needs about the model it will be embedding with, which llama.cpp has no
 * use for and would refuse to start over. Written next to my-models.ini and mounted into the API
 * container, rather than passed through the environment file, so that everything which configures
 * models goes through this one function: the test harness and the post-install model management
 * page both already call it, and neither touches the environment file.
 */
export const getShabtiModelsPath = (baseDir = path.resolve()) =>
	path.join(getLlamaModelsDir(baseDir), "shabti-models.json");

const shabtiSettings = (modelName: string, modelData: Record<string, any>) =>
	Object.entries(modelData).reduce(
		(acc, [key, value]) => {
			if (!key.startsWith(SHABTI_PREFIX)) return acc;
			const setting = key.slice(SHABTI_PREFIX.length);
			const read = SHABTI_KEYS[setting];
			if (!read)
				throw new HTTPException(400, {
					message: `model ${modelName} sets ${key}, which is not a setting Shabti knows about`,
				});
			return {
				...acc,
				[setting]: read(String(value), `${key} for model ${modelName}`),
			};
		},
		{} as Record<string, any>,
	);

const presetSection = (modelName: string, modelData: Record<string, any>) =>
	Object.entries(modelData).reduce(
		(acc, [key, value]) => {
			if (key.startsWith(SHABTI_PREFIX)) return acc;
			if (!PRESET_KEYS.includes(key))
				throw new HTTPException(400, {
					message: `model ${modelName} sets ${key}, which is not a supported llama.cpp option. Shabti's own settings have to start with ${SHABTI_PREFIX}`,
				});
			return { ...acc, [key]: value };
		},
		{} as Record<string, any>,
	);

// Docker creates a directory in place of a bind mounted file that doesn't exist yet, so a previous
// run which launched the containers first would make the write fail
const clearDirectory = async (filePath: string) => {
	if (
		await stat(filePath).then(
			(s) => s.isDirectory(),
			() => false,
		)
	)
		await rm(filePath, { recursive: true, force: true });
};

/**
 * Translates a selection and the catalogue into the two files that describe it: the preset
 * sections llama.cpp reads and the settings the API reads. Separate from the writing so that what
 * ends up in each file can be checked without a catalogue on disk to stand in for the real one.
 */
export const buildModelsConfig = (
	selection: ModelSelection,
	shabtiModels: Record<string, any>,
) => {
	const { chatModels, embeddingsModel, defaultModel } = selection;
	const settings: Record<string, any> = {};
	const sections = [...chatModels, embeddingsModel].reduce(
		(acc, key) => {
			const modelData = _.cloneDeep(shabtiModels[key]);
			if (!modelData)
				throw new HTTPException(404, { message: `model ${key} not found` });
			const tags = modelData.tags.filter((tag: string) => tag != "default"); // remove the default tag set in the config file
			if (key == defaultModel) {
				// set the default tag if this model is the selected default
				tags.push("default");
			}
			modelData.tags = tags.join(", "); // llama.cpp expects the tags in CSV
			const shabti = shabtiSettings(key, modelData);
			// only the models we know something about, so the file says what it carries
			if (Object.keys(shabti).length) settings[key] = shabti;
			return { ...acc, [key]: presetSection(key, modelData) };
		},
		{} as Record<string, any>,
	);

	// the splitter has no tokenizer to read a chunk size off: the GGUF repositories llama.cpp loads
	// from don't carry one, and the model's own trained context is an architectural ceiling rather
	// than a sensible chunk size, so this is decided here and recorded rather than inferred
	const chunkSize = settings[embeddingsModel]?.chunk_size || DEFAULT_CHUNK_SIZE;
	settings[embeddingsModel] = {
		...settings[embeddingsModel],
		chunk_size: chunkSize,
	};

	const globals: Record<string, any> = {
		"ubatch-size": DEFAULT_UBATCH,
		"batch-size": DEFAULT_BATCH,
	};
	const needed = chunkSize + UBATCH_HEADROOM;
	// left to the catalogue entry if it sets the batch sizes itself. the logical batch is raised
	// with the physical one so the two can never cross, which llama.cpp would otherwise resolve by
	// quietly lowering one of them
	if (needed > DEFAULT_UBATCH && !sections[embeddingsModel]["ubatch-size"]) {
		sections[embeddingsModel]["ubatch-size"] = needed;
		if (needed > DEFAULT_BATCH && !sections[embeddingsModel]["batch-size"])
			sections[embeddingsModel]["batch-size"] = needed;
	}

	return { sections: { [GLOBAL_SECTION]: globals, ...sections }, settings };
};

// writes the ini file used by llama.cpp's --models-preset, plus the settings file the API reads
export default async (selection: ModelSelection, baseDir = path.resolve()) => {
	const { sections, settings } = buildModelsConfig(
		selection,
		await getModelsConfig(),
	);
	const modelsIniPath = getModelsIniPath(baseDir);
	const shabtiModelsPath = getShabtiModelsPath(baseDir);
	await Promise.all([
		clearDirectory(modelsIniPath),
		clearDirectory(shabtiModelsPath),
	]);
	await Promise.all([
		Bun.write(modelsIniPath, ini.stringify(sections, { pretty: true })),
		Bun.write(shabtiModelsPath, `${JSON.stringify(settings, null, "\t")}\n`),
	]);
	return { modelsIniPath, shabtiModelsPath };
};
