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
