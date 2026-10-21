import { Command } from "commander";
import { $ } from "bun";
import path from "node:path";
import {
	localModels,
	type ModelSelection,
} from "../shabti_configurator/getDefaultModelSelection";
import downloadModel from "../shabti_configurator/server/downloadModel";
import writeModelsIni from "../shabti_configurator/server/writeModelsIni";
import { composeFlags, runCommand, writeTestProviders } from "./stack";

const REPO = path.join(import.meta.dir, "..");

// the same embeddings model the test stack uses, so a bench with no flags is comparable to it
const DEFAULT_EMBEDDINGS = "all-MiniLM-L6-v2";
const DEFAULT_CHUNK_SIZE = 128;
// every reranker in the catalogue, so the benchmark compares them all by default
const DEFAULT_RERANKERS = [
	"jina-reranker-v1-tiny-en",
	"bge-reranker-v2-m3",
	"Qwen3-Reranker-0.6B",
];

interface BenchOptions {
	embeddingsModel: string;
	chunkSize: number;
	rerankers: string[];
	fresh?: boolean;
	keepUp?: boolean;
}

/** each embedder and chunk size gets its own project, so its OpenSearch data is kept apart */
const slugOf = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-");

/**
 * The security-disabled stack's flags, under the bench project's own name and with the bench layer
 * on top. Everything else is shared, so the bench stack runs the same services the tests do.
 */
const benchFlags = (project: string) => {
	const flags = composeFlags("disabled");
	flags[flags.indexOf("-p") + 1] = project;
	flags.splice(flags.indexOf("--env-file"), 0, "-f", "bench.yml");
	return flags;
};

const benchCompose = (project: string, args: string[]) => [
	"docker",
	"compose",
	...benchFlags(project),
	...args,
];

/** the message is the reason; the command's own output has already streamed past */
const failed = (message: string) => {
	console.error(`\n${message}`);
	return 1;
};

const benchmark = async (
	project: string,
	selection: ModelSelection,
	dataset: string,
	extra: string[],
	opts: BenchOptions,
): Promise<number> => {
	// the models llama.cpp serves and the providers the API reads are written before anything is
	// started, as the test harness does, because both are mounted in when the containers come up
	await writeModelsIni(selection, path.join(REPO, "shabti_configurator"));
	await writeTestProviders();

	// the test stack publishes the same container names and network, so it has to be gone first
	const testDown = await runCommand([
		"docker",
		"compose",
		...composeFlags("disabled"),
		"down",
	]);
	if (testDown.code !== 0)
		return failed("could not bring down the security-disabled test stack");

	// left behind by either stack, and llama.cpp would keep a stale my-models.ini if it survived
	for (const name of ["opensearch-node1", "llama_cpp", "tika"])
		await $`docker container rm --force ${name}`.quiet().nothrow();

	if (opts.fresh)
		await $`docker volume rm --force ${`${project}_opensearch-data1`}`
			.quiet()
			.nothrow();

	console.log("building pytest-api...");
	const built = await runCommand(
		benchCompose(project, ["build", "pytest-api"]),
	);
	if (built.code !== 0)
		return failed("the pytest-api image could not be built");

	console.log("starting llama.cpp...");
	const started = await runCommand(
		benchCompose(project, ["up", "-d", "--wait", "llama-cpp"]),
	);
	if (started.code !== 0) return failed("llama.cpp did not start");

	console.log("downloading the models...");
	try {
		for (const modelName of localModels(selection)) {
			// every 10% of each file, so a large download shows it is moving without flooding the output
			const reported = new Map<string, number>();
			for await (const { progress, total, file } of downloadModel(modelName)) {
				const step = total ? Math.floor((progress / total) * 10) : 0;
				if (reported.get(file) === step) continue;
				reported.set(file, step);
				console.log(`downloading ${modelName} (${file}): ${step * 10}%`);
			}
		}
	} catch (error) {
		return failed(
			`the models could not be downloaded: ${error instanceof Error ? error.message : error}`,
		);
	}

	console.log(`running the benchmark on ${dataset}...`);
	// no timeout: a real dataset can take hours, and the run's own exit code is what matters.
	// from /app as shabti.tests.bench, because the tests import src relatively from above tests
	const result = await runCommand(
		benchCompose(project, [
			"run",
			"--rm",
			"-T",
			"--workdir",
			"/app",
			"pytest-api",
			"/app/shabti/.venv/bin/python",
			"-m",
			"shabti.tests.bench",
			dataset,
			...extra,
		]),
	);
	return result.code;
};

const program = new Command()
	.name("bench")
	.description(
		"Benchmarks a dataset against one embeddings model, chunk size and set of rerankers, in a " +
			"stack of its own. Anything after -- goes to python -m shabti.tests.bench.\n\n" +
			"The test stack and a bench stack cannot be up at the same time, so a bench run takes the " +
			"test stack down first.",
	)
	.argument("<dataset>", "the dataset to benchmark against")
	.argument(
		"[extra...]",
		"passed through to python -m shabti.tests.bench, after --",
	)
	.option(
		"--embeddings-model <name>",
		"the local embeddings model to index with",
		DEFAULT_EMBEDDINGS,
	)
	.option(
		"--chunk-size <n>",
		"tokens per chunk",
		(value) => Number(value),
		DEFAULT_CHUNK_SIZE,
	)
	.option(
		"--rerankers <names...>",
		"the local rerankers to compare",
		DEFAULT_RERANKERS,
	)
	.option("--fresh", "drop this bench stack's OpenSearch data first")
	.option("--keep-up", "leave the bench stack running afterwards")
	.action(
		async (
			dataset: string,
			extra: string[] | undefined,
			opts: BenchOptions,
		) => {
			if (!Number.isInteger(opts.chunkSize) || opts.chunkSize < 1) {
				console.error(
					`--chunk-size has to be a whole number of tokens, not ${opts.chunkSize}`,
				);
				process.exit(2);
			}

			const selection: ModelSelection = {
				chatModels: [],
				embeddingsModel: opts.embeddingsModel,
				embeddingsChunkSize: opts.chunkSize,
				rerankModels: opts.rerankers,
			};
			const project = `shabti-bench-${slugOf(`${opts.embeddingsModel}-${opts.chunkSize}`)}`;

			let code = 1;
			try {
				code = await benchmark(project, selection, dataset, extra ?? [], opts);
			} catch (error) {
				code = failed(
					`the benchmark could not be run: ${error instanceof Error ? error.message : error}`,
				);
			} finally {
				// the stack is torn down even after a failure, so the next run starts clean, but
				// its volumes are kept: that is what lets the next run skip the indexing
				if (!opts.keepUp) {
					console.log("tearing down...");
					await runCommand(benchCompose(project, ["down"]));
				}
			}
			process.exitCode = code;
		},
	);

await program.parseAsync();
