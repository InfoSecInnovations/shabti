import { createHash } from "node:crypto";
import { existsSync, statSync } from "node:fs";
import path from "node:path";

export const DEFAULT_CHUNK_SIZES = [128, 256, 512];

// keep these in step with docker_containers/shabti_api/tests/bench/datasets.py
export const NANO_LANGUAGES = [
	"ar",
	"de",
	"en",
	"es",
	"fr",
	"it",
	"ja",
	"ko",
	"no",
	"pt",
	"sv",
];
const TASK_LABELS: Record<string, string> = {
	scifact: "SciFact",
	nfcorpus: "NFCorpus",
};
export const BUILTIN_DATASETS = [
	"scifact",
	"nfcorpus",
	...["nano-scifact", "nano-nfcorpus"].flatMap((task) =>
		NANO_LANGUAGES.map((language) => `${task}-${language}`),
	),
];
// what a folder needs to be read as a dataset, the same as datasets.py's BEIR_FILES
const BEIR_FILES = ["corpus.jsonl", "queries.jsonl", "qrels/test.tsv"];

export const DEFAULT_DATASETS = [
	"nano-scifact-en",
	"nano-scifact-es",
	"nano-scifact-ja",
	"nano-nfcorpus-en",
	"nano-nfcorpus-es",
	"nano-nfcorpus-ja",
];

/** each embedder and chunk size gets its own folder, so its results are kept apart */
export const slugOf = (text: string) =>
	text.toLowerCase().replace(/[^a-z0-9]+/g, "-");

/** the embeddings models in the catalogue, in catalogue order */
export const shippedEmbeddingsModels = (
	catalogue: Record<string, { tags: string[] }>,
) =>
	Object.keys(catalogue).filter((name) =>
		catalogue[name].tags.includes("embeddings"),
	);

/** keys are sorted at every depth, so the same settings always hash the same */
const sortKeys = (value: unknown): unknown => {
	if (Array.isArray(value)) return value.map(sortKeys);
	if (value && typeof value === "object")
		return Object.fromEntries(
			Object.keys(value)
				.sort()
				.map((key) => [key, sortKeys((value as Record<string, unknown>)[key])]),
		);
	return value;
};

export const modelFingerprint = (section: Record<string, unknown>) =>
	createHash("sha256")
		.update(JSON.stringify(sortKeys(section)))
		.digest("hex")
		.slice(0, 8);

export interface Dataset {
	key: string;
	label: string;
	hostPath?: string;
}

const builtinLabel = (name: string) => {
	const nano = /^nano-(scifact|nfcorpus)-(\w+)$/.exec(name);
	if (nano) return `${TASK_LABELS[nano[1]]} ${nano[2]}`;
	return `${TASK_LABELS[name]} (full)`;
};

/** a built-in name, or a folder holding a BEIR dataset */
export const resolveDataset = (value: string): Dataset => {
	if (BUILTIN_DATASETS.includes(value))
		return { key: value, label: builtinLabel(value) };

	if (existsSync(value) && statSync(value).isDirectory()) {
		const missing = BEIR_FILES.find(
			(file) => !existsSync(path.join(value, file)),
		);
		if (missing)
			throw new Error(
				`${value} is missing ${missing}, a dataset folder needs ${BEIR_FILES.join(", ")}`,
			);
		const hostPath = path.resolve(value);
		const label = path.basename(hostPath);
		return { key: `custom-${slugOf(label)}`, label, hostPath };
	}

	throw new Error(
		`${value} is neither a built-in dataset nor a folder, the built-in datasets are ${BUILTIN_DATASETS.join(", ")}`,
	);
};

export interface PlannedRun {
	model: string;
	chunkSize: number;
	dataset: Dataset;
}

/** one group per model and chunk size, with a run on each dataset */
export const planRuns = (
	models: string[],
	chunkSizes: number[],
	datasets: Dataset[],
): { model: string; chunkSize: number; runs: PlannedRun[] }[] =>
	models.flatMap((model) =>
		chunkSizes.map((chunkSize) => ({
			model,
			chunkSize,
			runs: datasets.map((dataset) => ({ model, chunkSize, dataset })),
		})),
	);

export const runDir = (
	root: string,
	model: string,
	chunkSize: number,
	datasetKey: string,
) => path.join(root, "runs", `${slugOf(model)}-${chunkSize}`, datasetKey);

export interface RunMeta {
	model: string;
	chunkSize: number;
	dataset: string;
	fingerprint: string;
	extra: string[];
}

/** a run is cached when its results are there and were made with the same settings */
export const isCached = async (dir: string, meta: RunMeta) => {
	const results = Bun.file(path.join(dir, "results.json"));
	const metaFile = Bun.file(path.join(dir, "meta.json"));
	if (!(await results.exists()) || !(await metaFile.exists())) return false;
	return Bun.deepEquals(await metaFile.json(), meta, true);
};

export interface RunResult {
	ndcg: number;
	emptyAnswerable: number;
}

export const readRunResult = async (
	dir: string,
): Promise<RunResult | undefined> => {
	const results = Bun.file(path.join(dir, "results.json"));
	if (!(await results.exists())) return undefined;
	const { result } = await results.json();
	return {
		ndcg: result["ndcg@10"],
		emptyAnswerable: result.empty_answerable,
	};
};

export interface SummaryRow {
	model: string;
	chunkSize: number;
	runs: Record<string, RunResult | undefined>;
}

const mean = (values: number[]) =>
	values.reduce((sum, value) => sum + value, 0) / values.length;

/** a markdown table of nDCG@10 per dataset, with the mean and the share of empty answers */
export const summaryTable = (datasets: Dataset[], rows: SummaryRow[]) => {
	const header = [
		"Model",
		"Chunk",
		...datasets.map((dataset) => dataset.label),
		"Mean",
		"Empty",
	];
	const lines = [
		`| ${header.join(" | ")} |`,
		`| ${header.map(() => "---").join(" | ")} |`,
	];
	for (const row of rows) {
		const results = datasets.map((dataset) => row.runs[dataset.key]);
		const present = results.filter(
			(result): result is RunResult => result !== undefined,
		);
		const cells = results.map((result) =>
			result ? result.ndcg.toFixed(3) : "—",
		);
		const meanNdcg = present.length
			? mean(present.map((result) => result.ndcg)).toFixed(3)
			: "—";
		const meanEmpty = present.length
			? `${(mean(present.map((result) => result.emptyAnswerable)) * 100).toFixed(1)}%`
			: "—";
		lines.push(
			`| ${[row.model, row.chunkSize, ...cells, meanNdcg, meanEmpty].join(" | ")} |`,
		);
	}
	return `${lines.join("\n")}\n`;
};
