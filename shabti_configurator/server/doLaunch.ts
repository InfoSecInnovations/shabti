import { runCompose } from "./composeProgress";
import * as envfile from "envfile";
import getEnvPath from "./getEnvPath";
import getEnvs from "./getEnvs";
import launchShabti from "./launchShabti";
import logMessage from "./logMessage";
import requireGpu from "./requireGpu";
import stopWatchProcess from "./stopWatchProcess";

export default async function* (
	options: FormData,
	state: { watchProcess?: Bun.Subprocess },
) {
	const envs = await getEnvs();
	const isLocal = envs.SHABTI_LOCAL_VERSION == "True";
	if (options.get("environment") == "stop") {
		if (!isLocal) {
			yield logMessage("Stopping Shabti Docker Compose configuration...");
			// this removes the containers and the network but leaves the volumes alone, so no
			// models or ingested documents are lost
			yield* runCompose("./docker_compose/docker-compose.yml", ["down"]);
			return;
		}
		yield logMessage("Stopping local Docker development configuration...");
		await stopWatchProcess(state);
		yield* runCompose("./docker_compose/docker-compose-dev.yml", ["stop"]);
		return;
	}
	envs.SHABTI_COMPUTE = options.has("use_gpu") ? "cuda" : "cpu";
	await requireGpu(envs);
	yield logMessage(
		`Launching Shabti ${envs.SHABTI_COMPUTE == "cuda" ? "with" : "without"} GPU acceleration.`,
	);
	await Bun.write(getEnvPath(), envfile.stringify(envs));
	yield* launchShabti(isLocal, state);
}
