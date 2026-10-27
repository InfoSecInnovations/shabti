import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import {
	type Dataset,
	isCached,
	modelFingerprint,
	planRuns,
	readRunResult,
	resolveDataset,
	runDir,
	shippedEmbeddingsModels,
	slugOf,
	summaryTable,
} from "../benchMatrix";

let root: string;

beforeEach(async () => {
	root = await mkdtemp(path.join(tmpdir(), "bench-matrix-"));
});

afterEach(async () => {
	await rm(root, { recursive: true, force: true });
});

/** a folder with the three files a BEIR dataset needs */
const writeBeir = async (dir: string) => {
	await mkdir(path.join(dir, "qrels"), { recursive: true });
	await writeFile(path.join(dir, "corpus.jsonl"), "");
	await writeFile(path.join(dir, "queries.jsonl"), "");
	await writeFile(path.join(dir, "qrels", "test.tsv"), "");
};

const DATASET_EN: Dataset = { key: "nano-scifact-en", label: "SciFact en" };
const DATASET_JA: Dataset = { key: "nano-nfcorpus-ja", label: "NFCorpus ja" };
const DATASET_FULL: Dataset = { key: "scifact", label: "SciFact (full)" };

describe("slugOf", () => {
	test("lower-cases and joins runs of other characters with dashes", () => {
		expect(slugOf("All-MiniLM L6_v2")).toBe("all-minilm-l6-v2");
	});
});

describe("shippedEmbeddingsModels", () => {
	test("keeps the embeddings models in catalogue order", () => {
		const catalogue = {
			chat: { tags: ["chat", "default"] },
			"first-embedder": { tags: ["embeddings"] },
			"other-chat": { tags: ["chat"] },
			"second-embedder": { tags: ["embeddings"] },
		};
		expect(shippedEmbeddingsModels(catalogue)).toEqual([
			"first-embedder",
			"second-embedder",
		]);
	});
});

describe("modelFingerprint", () => {
	test("is eight hex characters", () => {
		expect(modelFingerprint({ hf: "a/b", tags: ["embeddings"] })).toMatch(
			/^[0-9a-f]{8}$/,
		);
	});

	test("ignores key order, at every depth", () => {
		const one = { hf: "a/b", extra: { x: 1, y: { p: 1, q: 2 } } };
		const two = { extra: { y: { q: 2, p: 1 }, x: 1 }, hf: "a/b" };
		expect(modelFingerprint(one)).toBe(modelFingerprint(two));
	});

	test("changes with a value", () => {
		expect(modelFingerprint({ hf: "a/b" })).not.toBe(
			modelFingerprint({ hf: "a/c" }),
		);
	});
});

describe("resolveDataset", () => {
	test("labels a built-in nano dataset", () => {
		expect(resolveDataset("nano-scifact-en")).toEqual({
			key: "nano-scifact-en",
			label: "SciFact en",
		});
	});

	test("labels a built-in nano dataset in another language", () => {
		expect(resolveDataset("nano-nfcorpus-ja")).toEqual({
			key: "nano-nfcorpus-ja",
			label: "NFCorpus ja",
		});
	});

	test("labels the full built-in datasets", () => {
		expect(resolveDataset("scifact")).toEqual({
			key: "scifact",
			label: "SciFact (full)",
		});
		expect(resolveDataset("nfcorpus")).toEqual({
			key: "nfcorpus",
			label: "NFCorpus (full)",
		});
	});

	test("reads a folder holding a BEIR dataset as a custom one", async () => {
		const dir = path.join(root, "My Set");
		await writeBeir(dir);
		expect(resolveDataset(dir)).toEqual({
			key: "custom-my-set",
			label: "My Set",
			hostPath: path.resolve(dir),
		});
	});

	test("names the missing file in a folder that is not a whole dataset", async () => {
		const dir = path.join(root, "partial");
		await mkdir(dir);
		await writeFile(path.join(dir, "corpus.jsonl"), "");
		await writeFile(path.join(dir, "queries.jsonl"), "");
		expect(() => resolveDataset(dir)).toThrow(/qrels\/test\.tsv/);
	});

	test("rejects an unknown name and lists the built-in ones", () => {
		expect(() => resolveDataset("not-a-dataset")).toThrow(/nano-scifact-en/);
	});
});

describe("planRuns", () => {
	test("groups by model and chunk size, with models outermost and datasets in order", () => {
		const datasets = [DATASET_EN, DATASET_JA];
		const plan = planRuns(["m1", "m2"], [128, 256], datasets);

		expect(plan.map((group) => [group.model, group.chunkSize])).toEqual([
			["m1", 128],
			["m1", 256],
			["m2", 128],
			["m2", 256],
		]);
		expect(plan[1].runs).toEqual([
			{ model: "m1", chunkSize: 256, dataset: DATASET_EN },
			{ model: "m1", chunkSize: 256, dataset: DATASET_JA },
		]);
	});
});

describe("runDir", () => {
	test("puts each model and chunk size under its own folder, then the dataset", () => {
		expect(runDir("/bench", "all-MiniLM-L6-v2", 128, "nano-scifact-en")).toBe(
			path.join("/bench", "runs", "all-minilm-l6-v2-128", "nano-scifact-en"),
		);
	});
});

describe("isCached", () => {
	const meta = {
		model: "m1",
		chunkSize: 128,
		dataset: "nano-scifact-en",
		fingerprint: "abcd1234",
		extra: ["--top-k", "5"],
	};

	test("is false when nothing is there", async () => {
		expect(await isCached(path.join(root, "run"), meta)).toBe(false);
	});

	test("is true when the results and the matching meta are there", async () => {
		const dir = path.join(root, "run");
		await mkdir(dir);
		await writeFile(path.join(dir, "results.json"), "{}");
		await writeFile(path.join(dir, "meta.json"), JSON.stringify(meta));
		expect(await isCached(dir, meta)).toBe(true);
	});

	test("is false when the meta differs", async () => {
		const dir = path.join(root, "run");
		await mkdir(dir);
		await writeFile(path.join(dir, "results.json"), "{}");
		await writeFile(
			path.join(dir, "meta.json"),
			JSON.stringify({ ...meta, chunkSize: 256 }),
		);
		expect(await isCached(dir, meta)).toBe(false);
	});

	test("is false when the meta is there without results", async () => {
		const dir = path.join(root, "run");
		await mkdir(dir);
		await writeFile(path.join(dir, "meta.json"), JSON.stringify(meta));
		expect(await isCached(dir, meta)).toBe(false);
	});
});

describe("readRunResult", () => {
	test("reads nDCG@10 and the empty-answerable share", async () => {
		const dir = path.join(root, "run");
		await mkdir(dir);
		await writeFile(
			path.join(dir, "results.json"),
			JSON.stringify({
				dataset: "nano-scifact-en",
				args: {},
				splits: {},
				result: {
					"hit@5": 0.5,
					"recall@5": 0.4,
					"ndcg@10": 0.512,
					"mrr@10": 0.4,
					per_query: [],
					empty_unanswerable: 0.9,
					empty_answerable: 0.02,
					p50_ms: 1,
					p95_ms: 2,
				},
			}),
		);
		expect(await readRunResult(dir)).toEqual({
			ndcg: 0.512,
			emptyAnswerable: 0.02,
		});
	});

	test("is undefined when the run has no results", async () => {
		expect(await readRunResult(path.join(root, "missing"))).toBeUndefined();
	});
});

describe("summaryTable", () => {
	test("prints the header, a dash for the missing run, and the means", () => {
		const datasets = [DATASET_EN, DATASET_JA, DATASET_FULL];
		const table = summaryTable(datasets, [
			{
				model: "all-MiniLM-L6-v2",
				chunkSize: 128,
				runs: {
					"nano-scifact-en": { ndcg: 0.5, emptyAnswerable: 0.02 },
					"nano-nfcorpus-ja": { ndcg: 0.25, emptyAnswerable: 0.04 },
				},
			},
			{ model: "bge-m3", chunkSize: 256, runs: {} },
		]);

		expect(table).toBe(
			[
				"| Model | Chunk | SciFact en | NFCorpus ja | SciFact (full) | Mean | Empty |",
				"| --- | --- | --- | --- | --- | --- | --- |",
				"| all-MiniLM-L6-v2 | 128 | 0.500 | 0.250 | — | 0.375 | 3.0% |",
				"| bge-m3 | 256 | — | — | — | — | — |",
				"",
			].join("\n"),
		);
	});
});
