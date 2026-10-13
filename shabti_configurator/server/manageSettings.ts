import * as envfile from "envfile";
import getEnvPath from "./getEnvPath";
import getEnvs from "./getEnvs";
import getShabtiRunState from "./shabtiRunState";
import launchShabti from "./launchShabti";
import logMessage from "./logMessage";
import stopWatchProcess from "./stopWatchProcess";

// updates the settings of an existing installation without going through a full reinstall.
// Each settings area is its own form, so we only merge in the values that area owns rather
// than rewriting the whole environment
export const applySettings = async function* (
	updates: { [key: string]: string },
	state: { watchProcess?: Bun.Subprocess },
) {
	const envs = await getEnvs();
	const isLocal = envs.SHABTI_LOCAL_VERSION == "True";
	// these only take effect when the containers are recreated, so we check whether Shabti is
	// up before we change anything, and leave it stopped if that's how the user had it
	const wasRunning =
		!!state.watchProcess || (await getShabtiRunState()).state != "stopped";
	yield logMessage("Saving settings...");
	await Bun.write(getEnvPath(), envfile.stringify({ ...envs, ...updates }));
	if (!wasRunning) {
		yield logMessage("The new settings will be applied next time you launch.");
		return;
	}
	yield logMessage("Relaunching Shabti so the new settings take effect...");
	await stopWatchProcess(state);
	yield* launchShabti(isLocal, state);
};

export const manageLogging = (
	options: FormData,
	state: { watchProcess?: Bun.Subprocess },
) =>
	applySettings(
		options.has("activity_logging")
			? {
					SHABTI_BASE_SERVICE: "shabti-logging",
					SHABTI_LOG_DIR: options.get("logging_location")?.toString() || "",
				}
			: { SHABTI_BASE_SERVICE: "shabti" },
		state,
	);

export const manageHostsAndPorts = (
	options: FormData,
	state: { watchProcess?: Bun.Subprocess },
) =>
	applySettings(
		{
			WEB_HOST: options.get("web-host")?.toString() || "localhost",
			WEB_PORT: options.get("web-port")?.toString() || "15130",
			API_HOST: options.get("api-host")?.toString() || "localhost",
			API_PORT: options.get("api-port")?.toString() || "15131",
		},
		state,
	);
