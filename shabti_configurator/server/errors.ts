/** a model the language model service reported as failed, or stopped reporting on */
export class ModelDownloadError extends Error {
	constructor(modelName: string, detail?: string) {
		super(
			`Couldn't download the language model ${modelName}${detail ? `: ${detail}` : "."}`,
		);
		this.name = "ModelDownloadError";
	}
}

/** the llama.cpp container never answered its health endpoint */
export class LlamaCppUnavailableError extends Error {
	constructor(seconds: number) {
		super(
			`The language model service didn't come online within ${seconds} seconds, check that Docker is running and that the llama_cpp container started.`,
		);
		this.name = "LlamaCppUnavailableError";
	}
}

/** Keycloak never accepted the credentials within the retry budget */
export class KeycloakUnavailableError extends Error {
	constructor(attempts: number, options?: ErrorOptions) {
		super(
			`Couldn't connect to Keycloak after ${attempts} attempts, check the Keycloak container's logs.`,
			options,
		);
		this.name = "KeycloakUnavailableError";
	}
}

/** GPU acceleration was selected but Docker can't give containers the GPU */
export class GpuUnavailableError extends Error {
	constructor(detail: string) {
		super(
			`Docker couldn't start a container with access to the GPU: ${detail}. GPU acceleration needs the NVIDIA drivers and, on Windows, Docker Desktop's WSL2 backend.`,
		);
		this.name = "GpuUnavailableError";
	}
}

/** the Docker images couldn't be pulled and some of them aren't already downloaded */
export class ImagesUnavailableError extends Error {
	constructor(images: string[]) {
		super(
			`Couldn't download the Docker images ${images.join(", ")}. Connect to the internet and try again.`,
		);
		this.name = "ImagesUnavailableError";
	}
}

/** the local code's images couldn't be built offline and there's no earlier build to fall back on */
export class ImageBuildError extends Error {
	constructor(images: string[]) {
		super(
			`Couldn't build the Docker images ${images.join(", ")} offline, and they haven't been built before. Connect to the internet and try again.`,
		);
		this.name = "ImageBuildError";
	}
}

/** the selected language models aren't downloaded and Hugging Face can't be reached */
export class ModelsUnavailableError extends Error {
	constructor(models: string[]) {
		super(
			`The language models ${models.join(", ")} aren't downloaded, and Hugging Face can't be reached to download them.`,
		);
		this.name = "ModelsUnavailableError";
	}
}

/** a compose command exited with an error, describeError shows the stderr as it does a ShellError's */
export class ComposeCommandError extends Error {
	constructor(
		args: string[],
		public exitCode: number,
		public stderr: string,
	) {
		super(`docker compose ${args.join(" ")} failed with exit code ${exitCode}`);
		this.name = "ComposeCommandError";
	}
}
