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

/** whether the pull went through, a failure is left to the caller to make sense of */
export const composePull = async (
	composeFile: string,
	env: Record<string, string>,
	ignoreBuildable: boolean,
) =>
	(
		await $`docker compose -f ${composeFile} pull ${ignoreBuildable ? ["--ignore-buildable"] : []}`
			.env(withEnv(env))
			.nothrow()
	).exitCode == 0;

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
