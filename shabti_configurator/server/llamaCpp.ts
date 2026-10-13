export const LLAMA_CPP_SERVICE_FILE = "docker-compose-llama-cpp-service.yml";
const BLANK_SERVICE_FILE = "docker-compose-blank.yml";

/** llama.cpp serves every local model, so it can only be left out when there are none */
export const needsLlamaCpp = (
	chatModels: string[],
	remoteEmbeddings: boolean,
) => chatModels.length > 0 || !remoteEmbeddings;

/**
 * What the environment file needs for the compose file to include llama.cpp or leave it out. An
 * empty LLM_HOST is what tells the API there are no local models.
 */
export const llamaCppEnv = (needed: boolean) =>
	needed
		? { LLAMA_CPP_SERVICE_FILE, LLM_HOST: "llama_cpp" }
		: { LLAMA_CPP_SERVICE_FILE: BLANK_SERVICE_FILE, LLM_HOST: "" };

/** an environment written before llama.cpp could be left out has neither, and includes it */
export const usesLlamaCpp = (envs: Record<string, string | undefined>) =>
	envs.LLAMA_CPP_SERVICE_FILE != BLANK_SERVICE_FILE;
