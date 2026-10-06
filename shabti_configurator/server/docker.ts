import { $ } from "bun";
import { ComposeCommandError } from "./errors";

// the Docker commands the offline handling and the progress reporting rely on, kept together so
// that the tests can spy on them rather than needing a Docker daemon

export const MODELS_VOLUME = "shabti_llama-cpp-models";

export interface ComposeService {
	image?: string;
	build?: unknown;
}

// the values we're about to write to the environment file take precedence over whatever the
// current one says, compose prefers the shell environment over .env
const withEnv = (env: Record<string, string>) =>
	({ ...process.env, ...env }) as Record<string, string>;

export const imageExists = async (image: string) =>
	(await $`docker image inspect ${image}`.quiet().nothrow()).exitCode == 0;

/**
 * Makes the same GPU request the CUDA service does, which fails the same way it would when Docker
 * has no access to the GPU (anything other than the WSL2 backend on Windows, or no NVIDIA container
 * toolkit). Returns Docker's error, or nothing when the container ran.
 */
export const gpuError = async (image: string) => {
	const { exitCode, stderr } =
		await $`docker run --rm --pull never --gpus 1 ${image} --version`
			.quiet()
			.nothrow();
	if (exitCode == 0) return;
	return stderr.toString().trim() || `exit code ${exitCode}`;
};

export const volumeExists = async (volume: string) =>
	(await $`docker volume inspect ${volume}`.quiet().nothrow()).exitCode == 0;

/** what `docker compose --progress json` reports on a resource, or on a layer of an image (parent_id) */
export interface ComposeEvent {
	id: string;
	parent_id?: string;
	status?: "Working" | "Done" | "Error" | "Warning";
	text?: string;
	details?: string;
	current?: number;
	total?: number;
}

/** what BuildKit reports on a build, the steps are vertexes */
export interface BuildStatus {
	vertexes?: {
		digest: string;
		name?: string;
		started?: string;
		completed?: string;
		cached?: boolean;
		error?: string;
	}[];
	statuses?: {
		id: string;
		vertex?: string;
		current: number;
		total?: number;
		completed?: string;
	}[];
	/** the step's output, base64 encoded */
	logs?: { vertex?: string; data: string }[];
}

export type ComposeOutput =
	| { kind: "event"; event: ComposeEvent }
	| { kind: "build"; status: BuildStatus };

/** the lines of a stream as they arrive */
async function* lines(stream: ReadableStream<Uint8Array>, from: string) {
	let buffer = "";
	for await (const chunk of stream.pipeThrough(new TextDecoderStream())) {
		const parts = (buffer + chunk).split("\n");
		buffer = parts.pop()!;
		for (const line of parts) yield { from, line: line.trim() };
	}
	yield { from, line: buffer.trim() };
}

/** the items of several iterators in whichever order they come */
async function* merge<T>(...sources: AsyncIterator<T>[]) {
	const next = (index: number) =>
		sources[index]!.next().then((result) => ({ index, result }));
	const pending = new Map(sources.map((_, index) => [index, next(index)]));
	while (pending.size) {
		const { index, result } = await Promise.race(pending.values());
		if (result.done) pending.delete(index);
		else {
			pending.set(index, next(index));
			yield result.value;
		}
	}
}

const parse = (line: string) => {
	try {
		if (line.startsWith("{")) return JSON.parse(line);
	} catch {}
};

/**
 * Runs a compose command with its progress as JSON, streaming what it reports and printing
 * anything else to the terminal. Returns the exit code, a failure throws with compose's own
 * error text unless `nothrow` is set.
 */
export async function* compose(
	composeFile: string,
	args: string[],
	{
		env = {},
		nothrow = false,
	}: { env?: Record<string, string>; nothrow?: boolean } = {},
): AsyncGenerator<ComposeOutput, number> {
	const proc = Bun.spawn(
		["docker", "compose", "--progress", "json", "-f", composeFile, ...args],
		{ env: withEnv(env), stdout: "pipe", stderr: "pipe" },
	);
	const errors: string[] = [];
	try {
		// compose reports on stderr, mixed in with its errors. stdout has the build's progress, or
		// the output of a container it runs
		for await (const { from, line } of merge(
			lines(proc.stdout, "stdout"),
			lines(proc.stderr, "stderr"),
		)) {
			if (!line) continue;
			const json = parse(line);
			if (from == "stdout") {
				if (json?.vertexes || json?.statuses || json?.logs)
					yield { kind: "build", status: json };
				else console.log(line);
			} else if (json?.id) yield { kind: "event", event: json };
			// compose's own log messages come through as JSON too, they just don't have an id
			else if (json?.msg) {
				console.error(`${json.level}: ${json.msg}`);
				if (json.level == "error" || json.level == "fatal")
					errors.push(json.msg);
			} else {
				console.error(line);
				errors.push(line);
			}
		}
		const exitCode = await proc.exited;
		if (exitCode != 0 && !nothrow)
			throw new ComposeCommandError(args, exitCode, errors.join("\n"));
		return exitCode;
	} finally {
		// the operation gave up on the command partway through
		if (proc.exitCode === null) proc.kill();
	}
}

/**
 * Streams the pull's progress, then returns whether it went through. A failure is left to the
 * caller to make sense of.
 */
export async function* composePull(
	composeFile: string,
	env: Record<string, string>,
	ignoreBuildable: boolean,
): AsyncGenerator<ComposeOutput, boolean> {
	const pull = compose(
		composeFile,
		[
			"pull",
			// otherwise one image failing aborts the downloads of all the others
			"--ignore-pull-failures",
			...(ignoreBuildable ? ["--ignore-buildable"] : []),
		],
		{ env, nothrow: true },
	);
	// with failures ignored the exit code doesn't tell us about them, and a download which fails
	// partway through isn't even reported: the image just never finishes
	let failed = false;
	const unfinished = new Set<string>();
	while (true) {
		const next = await pull.next();
		if (next.done) return next.value == 0 && !failed && !unfinished.size;
		const output = next.value;
		if (output.kind == "event" && !output.event.parent_id) {
			const { id, status, text } = output.event;
			if (text == "Pulling") unfinished.add(id);
			else if (status == "Done") unfinished.delete(id);
			else if (status == "Error" || status == "Warning") failed = true;
		}
		yield output;
	}
}

export const composeServices = async (
	composeFile: string,
	env: Record<string, string> = {},
): Promise<Record<string, ComposeService>> => {
	const config: { name: string; services?: Record<string, ComposeService> } =
		await $`docker compose -f ${composeFile} config --format json`
			.env(withEnv(env))
			.quiet()
			.json();
	// a build without an image is tagged with compose's default name, which config leaves out
	return Object.fromEntries(
		Object.entries(config.services || {}).map(([name, service]) => [
			name,
			service.build && !service.image
				? { ...service, image: `${config.name}-${name}` }
				: service,
		]),
	);
};

/** throws the build's own error, which is what explains a failure down to the code */
export const composeBuild = (
	composeFile: string,
	env: Record<string, string>,
) => compose(composeFile, ["build"], { env });

/** a llama.cpp router with nothing to serve, only there to report what's in the models volume */
export const startModelProbe = (image: string, name: string, port: number) =>
	$`docker run -d --rm --name ${name} -v ${MODELS_VOLUME}:/models -e LLAMA_CACHE=/models/hub -e LLAMA_ARG_OFFLINE=1 -p 127.0.0.1:${port}:${port} ${image} --host 0.0.0.0 --port ${port}`.quiet();

export const removeContainer = (name: string) =>
	$`docker container rm --force ${name}`.quiet().nothrow();
