import { Command } from "commander";
import { $ } from "bun";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import getModelsConfig, {
	getShippedModelsConfig,
} from "../shabti_configurator/getModelsConfig";
import {
	localModels,
	type ModelSelection,
} from "../shabti_configurator/getDefaultModelSelection";
import downloadModel from "../shabti_configurator/server/downloadModel";
import writeModelsIni from "../shabti_configurator/server/writeModelsIni";
import { composeFlags, runCommand, writeTestProviders } from "./stack";
import {
	DEFAULT_CHUNK_SIZES,
	DEFAULT_DATASETS,
	type Dataset,
	type RunMeta,
	type RunResult,
	type SummaryRow,
	isCached,
	modelFingerprint,
	planRuns,
	readRunResult,
	resolveDataset,
	runDir,
	shippedEmbeddingsModels,
	slugOf,
	summaryTable,
} from "./benchMatrix";

const REPO = path.join(import.meta.dir, "..");
const ROOT = path.join(REPO, "testing", "bench_results");

interface BenchOptions {
	models?: string[];
	chunkSizes?: string[];
	datasets?: string[];
	compute: string;
	rerun?: boolean;
	fresh?: boolean;
	keepUp?: boolean;
}

/** one run in the plan: its place in the count, where its results go, and whether they are cached */
interface Slot {
	k: number;
	dataset: Dataset;
	dir: string;
	meta: RunMeta;
	cached: boolean;
}

/** one stack: a model and chunk size, with a run on each dataset */
interface Group {
	model: string;
	chunkSize: number;
	fingerprint: string;
	slots: Slot[];
}

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

/** bad input stops here, before anything is written or started */
const usage = (message: string): never => {
	console.error(message);
	process.exit(2);
};

/**
 * Brings the stack up for one model and chunk size. Returns why it could not, so the group's runs
 * are recorded as failed and the bench carries on with the next group.
 */
const startStack = async (
	project: string,
	selection: ModelSelection,
	fingerprint: string,
	fresh: boolean,
): Promise<string | undefined> => {
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
		return "could not bring down the security-disabled test stack";

	// left behind by either stack, and llama.cpp would keep a stale my-models.ini if it survived
	for (const name of ["opensearch-node1", "llama_cpp", "tika"])
		await $`docker container rm --force ${name}`.quiet().nothrow();

	// indexed data is only reused while the model's settings are the ones it was indexed with
	const stackFile = path.join(ROOT, "stacks", `${project}.json`);
	const previous = Bun.file(stackFile);
	const changed =
		(await previous.exists()) &&
		(await previous.json()).fingerprint !== fingerprint;
	if (fresh || changed) {
		console.log(
			fresh
				? "dropping the OpenSearch data: --fresh"
				: "dropping the OpenSearch data: the model's settings changed",
		);
		await $`docker volume rm --force ${`${project}_opensearch-data1`}`
			.quiet()
			.nothrow();
	}
	await mkdir(path.dirname(stackFile), { recursive: true });
	await Bun.write(stackFile, `${JSON.stringify({ fingerprint }, null, 2)}\n`);

	console.log("building pytest-api...");
	const built = await runCommand(
		benchCompose(project, ["build", "pytest-api"]),
	);
	if (built.code !== 0) return "the pytest-api image could not be built";

	console.log("starting llama.cpp...");
	const started = await runCommand(
		benchCompose(project, ["up", "-d", "--wait", "llama-cpp"]),
	);
	if (started.code !== 0) return "llama.cpp did not start";

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
		return `the models could not be downloaded: ${error instanceof Error ? error.message : error}`;
	}

	return undefined;
};

/** one run inside a started stack. Returns why it failed, if it did */
const benchRun = async (
	project: string,
	group: Group,
	slot: Slot,
	extra: string[],
): Promise<string | undefined> => {
	const { dataset } = slot;
	const out = path.posix.join(
		"/bench_results",
		"runs",
		`${slugOf(group.model)}-${group.chunkSize}`,
		dataset.key,
	);
	// a folder of the user's is mounted read only under its own name, and read from there
	const mount = dataset.hostPath
		? [
				"-v",
				`${dataset.hostPath}:/custom/${path.basename(dataset.hostPath)}:ro`,
			]
		: [];
	const target = dataset.hostPath
		? `/custom/${path.basename(dataset.hostPath)}`
		: dataset.key;

	// no timeout: a real dataset can take hours, and the run's own exit code is what matters.
	// from /app as shabti.tests.bench, because the tests import src relatively from above tests
	const result = await runCommand(
		benchCompose(project, [
			"run",
			"--rm",
			"-T",
			...mount,
			"--workdir",
			"/app",
			"pytest-api",
			"/app/shabti/.venv/bin/python",
			"-m",
			"shabti.tests.bench",
			target,
			"--out",
			out,
			...extra,
		]),
	);
	if (result.code !== 0) return `the benchmark exited with code ${result.code}`;

	// the meta is what makes the run count as cached: it is only written once the run has finished
	try {
		await mkdir(slot.dir, { recursive: true });
		await Bun.write(
			path.join(slot.dir, "meta.json"),
			`${JSON.stringify(slot.meta, null, 2)}\n`,
		);
	} catch (error) {
		return `the results could not be recorded: ${error instanceof Error ? error.message : error}`;
	}
	return undefined;
};

/** runs one group's uncached runs under a single stack, and returns the failures */
const benchGroup = async (
	group: Group,
	options: { total: number; extra: string[]; fresh: boolean; leaveUp: boolean },
): Promise<string[]> => {
	const progress = (slot: Slot) =>
		`[${slot.k}/${options.total}] ${group.model} ${group.chunkSize} ${slot.dataset.label}`;

	// a group that is all cached never starts its stack
	if (group.slots.every((slot) => slot.cached)) {
		for (const slot of group.slots) console.log(`${progress(slot)}: cached`);
		return [];
	}

	const project = `shabti-bench-${slugOf(`${group.model}-${group.chunkSize}`)}`;
	const selection: ModelSelection = {
		chatModels: [],
		embeddingsModel: group.model,
		embeddingsChunkSize: group.chunkSize,
	};
	const failures: string[] = [];

	let stackReason: string | undefined;
	try {
		stackReason = await startStack(
			project,
			selection,
			group.fingerprint,
			options.fresh,
		);
	} catch (error) {
		stackReason = `the stack could not be started: ${error instanceof Error ? error.message : error}`;
	}
	if (stackReason) failed(stackReason);

	try {
		for (const slot of group.slots) {
			if (slot.cached) {
				console.log(`${progress(slot)}: cached`);
				continue;
			}
			console.log(progress(slot));
			const reason =
				stackReason ?? (await benchRun(project, group, slot, options.extra));
			if (reason)
				failures.push(
					`${group.model} ${group.chunkSize} ${slot.dataset.label}: ${reason}`,
				);
		}
	} finally {
		// the stack is torn down even after a failure, so the next group starts clean, but its
		// volume is kept: that is what lets the next run skip the indexing
		if (!options.leaveUp) {
			console.log("tearing down...");
			await runCommand(benchCompose(project, ["down"]));
		}
	}
	return failures;
};

const program = new Command()
	.name("bench")
	.description(
		"Benchmarks retrieval over every shipped embeddings model × chunk sizes × datasets by default, " +
			"reusing cached results and printing one summary table. Anything after -- goes to " +
			"python -m shabti.tests.bench.\n\n" +
			"The test stack and a bench stack cannot be up at the same time, so the test stack is taken down first.",
	)
	.argument(
		"[extra...]",
		"passed through to python -m shabti.tests.bench, after --",
	)
	.option(
		"--models <names...>",
		"embeddings models to bench, from the catalogue or shabti_configurator/custom_models.ini (default: every shipped one)",
	)
	.option(
		"--chunk-sizes <n...>",
		`tokens per chunk (default: ${DEFAULT_CHUNK_SIZES.join(" ")})`,
	)
	.option(
		"--datasets <names-or-paths...>",
		"built-in dataset names or BEIR folders (default: the six multilingual nano datasets)",
	)
	.option("--compute <cpu|cuda>", "where llama.cpp runs", "cuda")
	.option("--rerun", "ignore cached results")
	.option(
		"--fresh",
		"drop each stack's OpenSearch data before indexing (implies --rerun)",
	)
	.option("--keep-up", "leave the last stack running afterwards")
	.action(async (extra: string[], opts: BenchOptions) => {
		// folder paths are relative to where the command was run, so they are resolved before the cwd moves
		const datasets = (opts.datasets ?? DEFAULT_DATASETS).map((value) => {
			try {
				return resolveDataset(value);
			} catch (error) {
				return usage(error instanceof Error ? error.message : String(error));
			}
		});

		// the configurator's dev mode runs from here, so custom_models.ini is read from the same place
		process.chdir(path.join(REPO, "shabti_configurator"));

		// everything is checked before anything is written or started
		const catalogue = await getModelsConfig();
		const models =
			opts.models ?? shippedEmbeddingsModels(await getShippedModelsConfig());
		for (const model of models) {
			if (!catalogue[model])
				usage(
					`${model} is not a model in the catalogue, add it to shabti_configurator/custom_models.ini to bench it`,
				);
			if (!catalogue[model].tags.includes("embeddings"))
				usage(
					`${model} is not an embeddings model, so it cannot be benched; check its tags in shabti_configurator/custom_models.ini`,
				);
		}

		const chunkSizes = opts.chunkSizes?.map(Number) ?? DEFAULT_CHUNK_SIZES;
		const badSize = chunkSizes.find(
			(size) => !Number.isInteger(size) || size < 1,
		);
		if (badSize !== undefined)
			usage(
				`--chunk-sizes has to be whole numbers of tokens, 1 or more, not ${badSize}`,
			);

		if (opts.compute !== "cpu" && opts.compute !== "cuda")
			usage(`--compute takes cpu or cuda, not ${opts.compute}`);
		// the compose files pick the llama.cpp service from this, and the shell env wins over the env file
		process.env.SHABTI_COMPUTE = opts.compute;

		const fresh = opts.fresh ?? false;
		const rerun = (opts.rerun ?? false) || fresh;
		const keepUp = opts.keepUp ?? false;

		let k = 0;
		const groups: Group[] = [];
		for (const { model, chunkSize, runs } of planRuns(
			models,
			chunkSizes,
			datasets,
		)) {
			const fingerprint = modelFingerprint(catalogue[model]);
			const slots: Slot[] = [];
			for (const { dataset } of runs) {
				k += 1;
				const dir = runDir(ROOT, model, chunkSize, dataset.key);
				const meta: RunMeta = {
					model,
					chunkSize,
					dataset: dataset.key,
					fingerprint,
					extra,
				};
				slots.push({
					k,
					dataset,
					dir,
					meta,
					cached: !rerun && (await isCached(dir, meta)),
				});
			}
			groups.push({ model, chunkSize, fingerprint, slots });
		}

		const failures: string[] = [];
		for (const [index, group] of groups.entries()) {
			const last = index === groups.length - 1;
			failures.push(
				...(await benchGroup(group, {
					total: k,
					extra,
					fresh,
					leaveUp: keepUp && last,
				})),
			);
		}

		const rows: SummaryRow[] = [];
		for (const group of groups) {
			const runs: Record<string, RunResult | undefined> = {};
			for (const slot of group.slots)
				runs[slot.dataset.key] = (await isCached(slot.dir, slot.meta))
					? await readRunResult(slot.dir)
					: undefined;
			rows.push({ model: group.model, chunkSize: group.chunkSize, runs });
		}

		const report = `# Retrieval benchmark: nDCG@10\n\n${summaryTable(datasets, rows)}`;
		console.log(`\n${report}`);
		await mkdir(ROOT, { recursive: true });
		await Bun.write(path.join(ROOT, "summary.md"), report);
		console.log(`per-run reports are in ${path.join(ROOT, "runs")}`);

		if (failures.length) {
			console.error("\nfailed runs:");
			for (const line of failures) console.error(line);
		}
		process.exitCode = failures.length ? 1 : 0;
	});

await program.parseAsync();
