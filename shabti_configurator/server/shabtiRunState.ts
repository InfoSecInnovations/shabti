import * as envfile from "envfile";
import { containerIsRunning } from "./dockerItemsExist";
import getEnvPath from "./getEnvPath";
import { usesLlamaCpp } from "./llamaCpp";

export type ShabtiRunState = "stopped" | "partial" | "running";

// the containers docker-compose.yml is expected to bring up, every service pins its
// container_name so these are the same whichever security or logging variant is configured.
// Keycloak and llama.cpp are conditional on the same include hack the compose file uses.
const getExpectedContainers = async () => {
	const envs = envfile.parse(await Bun.file(getEnvPath()).text());
	return [
		"shabti",
		"shabti-web",
		...(usesLlamaCpp(envs) ? ["llama_cpp"] : []),
		"opensearch-node1",
		"tika",
		...(envs.KEYCLOAK_SERVICE_FILE == "docker-compose-keycloak.yml"
			? ["keycloak", "postgres"]
			: []),
	];
};

// only meaningful for a non local installation, the development configuration is tracked by
// the watch process the configurator spawns instead
export default async () => {
	const expected = await getExpectedContainers();
	const running = await Promise.all(expected.map(containerIsRunning));
	const stopped = expected.filter((_, i) => !running[i]);
	const state: ShabtiRunState = !stopped.length
		? "running"
		: stopped.length == expected.length
			? "stopped"
			: "partial";
	return { state, stopped };
};
