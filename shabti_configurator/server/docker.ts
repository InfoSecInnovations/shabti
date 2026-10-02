import { $ } from "bun";

// the Docker commands the offline handling relies on, kept together so that the tests can
// spy on them rather than needing a Docker daemon

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

export const volumeExists = async (volume: string) =>
	(await $`docker volume inspect ${volume}`.quiet().nothrow()).exitCode == 0;

/** what `docker compose --progress json` reports on an image, or on one of its layers (parent_id) */
export interface PullEvent {
	id: string;
	parent_id?: string;
	status?: "Working" | "Done" | "Error" | "Warning";
	text?: string;
	details?: string;
	current?: number;
	total?: number;
}

/**
 * Streams the pull's progress, then returns whether it went through. A failure is left to the
 * caller to make sense of.
 */
export async function* composePull(
	composeFile: string,
	env: Record<string, string>,
	ignoreBuildable: boolean,
): AsyncGenerator<PullEvent, boolean> {
	const proc = Bun.spawn(
		[
			"docker",
			"compose",
			"--progress",
			"json",
			"-f",
			composeFile,
			"pull",
			// otherwise one image failing aborts the downloads of all the others
			"--ignore-pull-failures",
			...(ignoreBuildable ? ["--ignore-buildable"] : []),
		],
		{ env: withEnv(env), stdout: "ignore", stderr: "pipe" },
	);
	// with failures ignored the exit code doesn't tell us about them, the events do
	let failed = false;
	let buffer = "";
	try {
		// compose writes its progress to stderr, mixed in with any errors of its own
		for await (const chunk of proc.stderr.pipeThrough(
			new TextDecoderStream(),
		)) {
			const lines = (buffer + chunk).split("\n");
			buffer = lines.pop()!;
			for (const line of lines.map((line) => line.trim()).filter(Boolean)) {
				let event: (PullEvent & { level?: string; msg?: string }) | undefined;
				try {
					if (line.startsWith("{")) event = JSON.parse(line);
				} catch {}
				// compose's own log messages come through as JSON too, they just don't have an id
				if (!event?.id) {
					console.error(event?.msg ? `${event.level}: ${event.msg}` : line);
					continue;
				}
				if (event.status == "Error") failed = true;
				yield event;
			}
		}
		if (buffer.trim()) console.error(buffer.trim());
		return (await proc.exited) == 0 && !failed;
	} finally {
		// the install gave up on the pull partway through
		if (proc.exitCode === null) proc.kill();
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
export const composeBuild = async (
	composeFile: string,
	env: Record<string, string>,
) => {
	await $`docker compose -f ${composeFile} build`.env(withEnv(env));
};

/** a llama.cpp router with nothing to serve, only there to report what's in the models volume */
export const startModelProbe = (image: string, name: string, port: number) =>
	$`docker run -d --rm --name ${name} -v ${MODELS_VOLUME}:/models -e LLAMA_CACHE=/models/hub -e LLAMA_ARG_OFFLINE=1 -p 127.0.0.1:${port}:${port} ${image} --host 0.0.0.0 --port ${port}`.quiet();

export const removeContainer = (name: string) =>
	$`docker container rm --force ${name}`.quiet().nothrow();
