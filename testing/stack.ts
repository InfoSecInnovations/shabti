import { $ } from "bun";
import path from "node:path";
import type { ModelSelection } from "../shabti_configurator/getDefaultModelSelection";
import downloadModel from "../shabti_configurator/server/downloadModel";
import writeModelsIni from "../shabti_configurator/server/writeModelsIni";
import {
	type TestType,
	type Suite,
	command,
	isEndToEnd,
	typeLabel,
} from "./suites";

const REPO = path.join(import.meta.dir, "..");
/** every compose command runs from here, so the relative paths in the files resolve the same way */
export const COMPOSE_DIR = path.join(import.meta.dir, "compose");

/**
 * The compose project for a test type. Files are layered with -f, so a type brings in exactly
 * the services it needs: runners.yml alone has no stack in it at all, which is what lets the unit
 * tests run without an OpenSearch or llama.cpp container ever being created.
 */
export const composeFlags = (testType: TestType): string[] => {
	if (!isEndToEnd(testType))
		// its own project name, so a unit run can never disturb a stack someone has up
		return ["-p", "shabti-unit-tests", "-f", "runners.yml"];

	const flags = [
		"-p",
		"shabti",
		"-f",
		"stack.yml",
		"-f",
		"runners.yml",
		"-f",
		"e2e.yml",
	];
	if (testType === "enabled") flags.push("-f", "enabled.yml");
	flags.push("--env-file", `../env/${typeLabel[testType]}-env`);
	if (testType === "enabled") flags.push("--env-file", "../env/.generated.env");
	return flags;
};

export const compose = (testType: TestType, args: string[]) => [
	"docker",
	"compose",
	...composeFlags(testType),
	...args,
];

/** enough of the tail to explain a failure, without becoming the wall of text it is replacing */
const TAIL_LINES = 40;

/**
 * The output is piped rather than inherited so that a suite which dies before writing any JUnit
 * output still has a reason to show in the summary. Each chunk is written straight through as it
 * arrives, so the run still streams; only the last few lines are kept.
 */
const tee = async (stream: ReadableStream<Uint8Array>, tail: string[]) => {
	const decoder = new TextDecoder();
	let partial = "";
	try {
		for await (const chunk of stream) {
			process.stdout.write(chunk);
			const lines = (partial + decoder.decode(chunk, { stream: true })).split(
				"\n",
			);
			partial = lines.pop() ?? "";
			tail.push(...lines);
			if (tail.length > TAIL_LINES) tail.splice(0, tail.length - TAIL_LINES);
		}
	} catch {
		// killing the process on a timeout tears the stream down mid read; whatever was already
		// captured is exactly what we want to keep
	}
	if (partial) tail.push(partial);
};

const run = async (
	cmd: string[],
	{ timeoutMs, env }: { timeoutMs?: number; env?: Record<string, string> } = {},
) => {
	const controller = new AbortController();
	let timedOut = false;
	const timer = timeoutMs
		? setTimeout(() => {
				timedOut = true;
				controller.abort();
			}, timeoutMs)
		: undefined;
	const started = Date.now();
	const tail: string[] = [];
	const elapsed = () => (Date.now() - started) / 1000;
	try {
		const proc = Bun.spawn(cmd, {
			cwd: COMPOSE_DIR,
			env: { ...process.env, ...env },
			stdin: "ignore",
			stdout: "pipe",
			stderr: "pipe",
			signal: controller.signal,
		});
		// both streams share one tail, so the reason is there whichever the runner wrote it to
		const [code] = await Promise.all([
			proc.exited,
			tee(proc.stdout, tail),
			tee(proc.stderr, tail),
		]);
		return { code, timedOut, seconds: elapsed(), tail };
	} catch (error) {
		// a command that could not even start is a failure like any other, not a crashed run
		console.error(`${cmd[0]} could not be run: ${error}`);
		tail.push(String(error));
		return { code: 127, timedOut, seconds: elapsed(), tail };
	} finally {
		clearTimeout(timer);
	}
};

/** the lockfiles reference paths that only exist inside the containers, so uv has to run in one */
export const lockPythonDeps = () =>
	$`docker compose -f ${path.join(REPO, "docker_containers", "docker-compose-uv-lock.yml")} run --rm uv-lock`;

// the smallest models in the catalogue, to keep downloads and memory down. two chat models so
// test_model_routes can check that loading one swaps the other out
const TEST_MODELS: ModelSelection = {
	chatModels: ["MiniCPM5-2B", "Granite-4.2-3B"],
	embeddingsModel: "all-MiniLM-L6-v2",
	defaultModel: "MiniCPM5-2B",
};

/**
 * The compose files pull llama.cpp in from the configurator, which bind mounts my-models.ini. That
 * file is generated during a normal install, but the tests never run one, so we have to write it
 * ourselves or Docker will mount an empty directory in its place.
 */
export const writeTestModelsIni = () =>
	writeModelsIni(TEST_MODELS, path.join(REPO, "shabti_configurator"));

/**
 * llama.cpp only fetches a model the first time something loads it, which left the download to
 * the suites' model fixtures and their per test timeout. Like an install, we download them up
 * front instead; a model that is already there costs nothing.
 */
export const preloadModels = async (testType: TestType) => {
	const started = await run(
		compose(testType, ["up", "-d", "--wait", "llama-cpp"]),
	);
	if (started.code !== 0) return started;
	try {
		for (const modelName of [
			...TEST_MODELS.chatModels,
			TEST_MODELS.embeddingsModel,
		]) {
			// every 10% of each file, so a large download shows it is moving without flooding the output
			const reported = new Map<string, number>();
			for await (const { progress, total, file } of downloadModel(modelName)) {
				const step = total ? Math.floor((progress / total) * 10) : 0;
				if (reported.get(file) === step) continue;
				reported.set(file, step);
				console.log(`downloading ${modelName} (${file}): ${step * 10}%`);
			}
		}
		return { code: 0, tail: [] as string[] };
	} catch (error) {
		return {
			code: 1,
			tail: [String(error instanceof Error ? error.message : error)],
		};
	}
};

/**
 * Back to a blank slate before an end-to-end type, and only for the state that type owns. Note
 * the volumes are removed by name rather than with `compose down -v`, which would also take
 * llama-cpp-models with it and force every GGUF to be downloaded again.
 */
export const nuke = async (testType: TestType) => {
	if (!isEndToEnd(testType)) return;
	await $`docker container rm --force opensearch-node1`.quiet().nothrow();
	await $`docker volume rm --force shabti_opensearch-data1`.quiet().nothrow();
	// Compose reuses a container when the service definition is unchanged, so a llama_cpp
	// container created against an older my-models.ini would keep mounting the stale file
	await $`docker container rm --force llama_cpp`.quiet().nothrow();
	if (testType === "enabled") {
		await $`docker container rm --force keycloak postgres`.quiet().nothrow();
		await $`docker volume rm --force shabti_postgres_data`.quiet().nothrow();
	}
};

export const build = (testType: TestType, services: string[]) =>
	run(compose(testType, ["build", ...services]));

/**
 * Also collects the one-off `compose run` containers, including any a timeout left behind. No
 * --remove-orphans: project shabti may be someone's actual install, and orphans there are theirs.
 */
export const down = (testType: TestType) => run(compose(testType, ["down"]));

export const runSuite = (
	testType: TestType,
	suite: Suite,
	extra: string[],
	timeoutMs: number,
) =>
	run(
		compose(testType, [
			"run",
			"--rm",
			// no pseudo-tty, so the runner works just as well with its output piped somewhere
			"-T",
			...(suite.workdir ? ["--workdir", suite.workdir] : []),
			suite.service,
			...command(testType, suite, extra),
		]),
		{ timeoutMs, env: { FORCE_COLOR: "1" } },
	);

/** the enabled type's Keycloak users, added from a one-off container rather than the live API */
export const addKeycloakDemoUsers = () =>
	run(
		compose("enabled", [
			"run",
			"--rm",
			"-T",
			"--no-deps",
			"pytest-api",
			"uv",
			"run",
			"-m",
			"add_keycloak_demo_users",
		]),
	);

export { run as runCommand };
