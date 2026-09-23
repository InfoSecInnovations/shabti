import { $ } from "bun";
import buildImages from "./buildImages";
import { getConnectivity } from "./connectivity";
import getEnvs from "./getEnvs";
import logMessage from "./logMessage";
import lockPythonDeps from "./lockPythonDeps";
import removeAllContainers from "./removeAllContainers";
import updateKeycloakRedirects from "./updateKeycloakRedirects";

// brings the containers up from whatever is currently in the environment file. The containers
// are removed rather than restarted because port mappings and bind mounts are fixed when a
// container is created, so that's the only way a configuration change takes effect
export default async function* (
	isLocal: boolean,
	state: { watchProcess?: Bun.Subprocess },
) {
	await removeAllContainers();
	if (isLocal) {
		if (state.watchProcess) {
			yield logMessage(
				"Local Docker development configuration already running!",
			);
			return;
		}
		if ((await getConnectivity()) == "offline") {
			yield logMessage(
				"Couldn't update the Python lockfiles offline, using the existing ones.",
			);
		} else {
			yield logMessage("Updating Python lockfiles...");
			await lockPythonDeps();
		}
		yield logMessage(
			"Building Docker image to run local code. This can take a while depending on your internet connection...",
		);
		if (!(await buildImages("./docker_compose/docker-compose-dev.yml", {})))
			yield logMessage(
				"Couldn't rebuild the Docker images, using the ones already built.",
			);
		yield logMessage(
			"Launching Docker Compose configuration with local code...",
		);
		state.watchProcess = Bun.spawn([
			"docker",
			"compose",
			"-f",
			"./docker_compose/docker-compose-dev.yml",
			"up",
		]);
	} else {
		yield logMessage("Launching Shabti Docker Compose configuration...");
		await $`docker compose -f ./docker_compose/docker-compose.yml up -d`;
	}
	const envs = await getEnvs();
	if (envs.SHABTI_SECURITY_ENABLED != "True") return;
	yield logMessage(
		"Updating the Keycloak configuration to match the current host and port...",
	);
	// the settings are already applied and the containers are already up at this point, so a
	// failure here isn't worth failing the whole operation over
	try {
		await updateKeycloakRedirects(
			envs.WEB_HOST || "localhost",
			envs.WEB_PORT || "15130",
		);
	} catch (error) {
		yield logMessage(
			`Couldn't update the Keycloak configuration: ${error instanceof Error ? error.message : error}. Users may not be able to log in until Shabti is relaunched.`,
		);
	}
}
