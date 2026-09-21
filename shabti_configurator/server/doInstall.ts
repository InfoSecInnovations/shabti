import crypto from "node:crypto";
import path from "node:path";
import { $ } from "bun";
import * as envfile from "envfile";
// import configurePlaywright from "./configurePlaywright";
import configurePreCommit from "./configurePreCommit";
import createCertificates from "./createCertificates";
import doUninstall from "./doUninstall";
import getEnvPath from "./getEnvPath";
import logMessage from "./logMessage";
import createVenv from "./createVenv";
import getKeycloakClientSecret from "./getKeycloakClientSecret";
import getCurrentVersion from "./getCurrentVersion";
import downloadModel from "./downloadModel";
import * as humanize from "ts-humanize";
import getDefaultModelSelection from "../getDefaultModelSelection";
import writeModelsIni from "./writeModelsIni";
import getVersionData from "./getVersionData";
import lockPythonDeps from "./lockPythonDeps";
import { INCOMPLETE_KEY } from "./installIsIncomplete";

export default async function* (
	options: FormData,
	selectedVersion: string,
	defaultVersion: string | undefined,
	state: { watchProcess?: Bun.Subprocess },
	installVenv = true,
) {
	// installing over an existing configuration isn't supported, so we tear the whole stack
	// down first. This has to be read before that as the uninstall removes the environment file
	const existingVersion = await getCurrentVersion();
	// keeping the language models, they're slow to download and the install can reuse them
	yield* doUninstall(false, state);
	// we keep track of the environment variables so we can write to the .env file and the process environment as needed
	const envs: { [key: string]: string } = {};
	const updateEnv = () => {
		Object.entries(envs).forEach(([key, value]) => (process.env[key] = value));
		return Bun.write(getEnvPath(), envfile.stringify(envs));
	};
	// written from the first updateEnv onwards and cleared at the very end, so a crash anywhere in
	// between leaves the main page saying the install didn't finish rather than that Shabti is ready
	envs[INCOMPLETE_KEY] = "True";
	envs.WEB_HOST = options.get("web-host")?.toString() || "localhost";
	envs.WEB_PORT = options.get("web-port")?.toString() || "15130";
	envs.API_HOST = options.get("api-host")?.toString() || "localhost";
	envs.API_PORT = options.get("api-port")?.toString() || "15131";
	yield logMessage(`installing Shabti version: ${selectedVersion}`);
	const securityLevel = options.get("security_level")?.toString();
	if (securityLevel && securityLevel != "none") {
		yield logMessage("configuring security...");
		envs.SHABTI_SECURITY_ENABLED = "True";
		envs.SHABTI_SERVICE = "shabti-enable-security";
		envs.SHABTI_WEB_SERVICE = "shabti-web-enable-security";
		const certDir = path.resolve("self_signed_certificates");
		await createCertificates(certDir);
		envs.ROOT_CA = path.join(certDir, "root-ca.pem");
		envs.KEYCLOAK_CERT = path.join(certDir, "keycloak-cert.pem");
		envs.KEYCLOAK_CERT_KEY = path.join(certDir, "keycloak-key.pem");
		envs.API_CERT = path.join(certDir, "shabti-cert.pem");
		envs.API_KEY = path.join(certDir, "shabti-key.pem");
		envs.WEB_CERT = path.join(certDir, "shabti-web-cert.pem");
		envs.WEB_KEY = path.join(certDir, "shabti-web-key.pem");
		yield logMessage("configured TLS certificates.");
		const keycloakPassword = options.get("keycloak_password")?.toString();
		// TODO: create error type for this
		if (!keycloakPassword)
			throw new Error("Keycloak admin password is invalid!");
		// TODO: validate password strength
		envs.KEYCLOAK_INITIAL_ADMIN_PASSWORD = keycloakPassword;
		const postgresPassword = crypto.randomBytes(25).toString("hex");
		// TODO: validate password strength
		envs.POSTGRES_DB_PASSWORD = postgresPassword;
		await updateEnv();
		yield logMessage(
			"getting OpenID credentials from Keycloak service. This can take a few minutes!",
		);
		const keycloakComposeFile = path.join(
			"docker_compose",
			"docker-compose-launch-keycloak.yml",
		);
		// update the keycloak image otherwise it can get overwritten when we launch everything below
		await $`docker compose -f ${keycloakComposeFile} pull`;
		await $`docker compose -f ${keycloakComposeFile} up -d`;
		envs.KEYCLOAK_CLIENT_ID = "shabti-auth";
		envs.KEYCLOAK_CLIENT_SECRET = await getKeycloakClientSecret();
		yield logMessage("got Keycloak credentials.");
		envs.KEYCLOAK_SERVICE_FILE = "docker-compose-keycloak.yml";
	} else {
		envs.SHABTI_SERVICE = "shabti-disable-security";
		envs.SHABTI_WEB_SERVICE = "shabti-web";
		envs.SHABTI_SECURITY_ENABLED = "False";
		envs.KEYCLOAK_SERVICE_FILE = "docker-compose-blank.yml";
	}
	envs.ENVIRONMENT = selectedVersion == "local" ? "development" : "production";
	const newVersion =
		selectedVersion == "local"
			? existingVersion || defaultVersion
			: selectedVersion;
	// defaultVersion can be null, we should only set an env var if not actually null
	if (newVersion) envs.SHABTI_VERSION = newVersion;
	const versionData =
		envs.SHABTI_API_VERSION && (await getVersionData(envs.SHABTI_VERSION));
	// if the version is a legacy version, the API and Web images will have the same tag as the overall version
	// if it's a newer version, versionData will tell us which tags to look up
	envs.SHABTI_API_VERSION =
		versionData?.apiVersion || envs.SHABTI_VERSION || "latest";
	envs.SHABTI_WEB_VERSION =
		versionData?.webVersion || envs.SHABTI_VERSION || "latest";
	envs.SHABTI_LOCAL_VERSION = selectedVersion == "local" ? "True" : "False";
	envs.SHABTI_COMPUTE = options.has("use_gpu") ? "cuda" : "cpu";
	if (securityLevel == "demo") envs.IS_SECURITY_DEMO = "True";
	if (options.has("activity_logging")) {
		envs.SHABTI_BASE_SERVICE = "shabti-logging";
		envs.SHABTI_LOG_DIR = options.get("logging_location")?.toString() || "";
	} else {
		envs.SHABTI_BASE_SERVICE = "shabti";
	}
	await updateEnv();
	yield logMessage("loading selected models...");
	const loaderComposeFile = path.join(
		"docker_compose",
		"docker-compose-download-model.yml",
	);
	const defaults = await getDefaultModelSelection();
	const selectedChatModels = options
		.getAll("language_model")
		.map((v) => v.toString());
	const chatModels = selectedChatModels.length
		? selectedChatModels
		: defaults.chatModels;
	const embeddingsModel =
		options.get("embeddings_model")?.toString() || defaults.embeddingsModel;
	const defaultModel =
		options.get("default_model")?.toString() ||
		(selectedChatModels.length ? chatModels[0]! : defaults.defaultModel);
	await writeModelsIni({ chatModels, embeddingsModel, defaultModel });
	yield logMessage(
		"launching LLM service. This can take quite a long time if this is your first launch or updates have been released to the Docker images...",
	);
	await $`docker compose -f ${loaderComposeFile} up -d`; // launch llama.cpp
	for (const modelName of [...chatModels, embeddingsModel]) {
		for await (const json of downloadModel(modelName)) {
			yield logMessage(
				`loaded ${humanize.bytes(json.progress)} / ${humanize.bytes(json.total)} of file ${json.file} for model ${json.modelName}`,
			);
		}
	} // ensure requested models are downloaded so they will be available once the install is done
	await $`docker compose -f ${loaderComposeFile} down`;
	yield logMessage(
		"launching Docker containers. This can take quite a long time if this is your first launch or updates have been released to the Docker images...",
	);
	if (selectedVersion == "local") {
		yield logMessage("updating Python lockfiles...");
		await lockPythonDeps();
		// TODO: catch only if internet is offline
		await $`docker compose -f ./docker_compose/docker-compose-dev.yml pull --ignore-buildable`.catch(
			(rej) => undefined,
		);
		await $`docker compose -f ./docker_compose/docker-compose-dev.yml build`;
		await $`docker compose -f ./docker_compose/docker-compose-dev.yml up -d`;
		if (installVenv) {
			// if we're running the install for automated testing we assume the venv is already configured, so we want to skip this step
			yield logMessage(
				"configuring Python environments. This can take some time if you have slow internet...",
			);
			await createVenv();
			await configurePreCommit();
		}
	} else {
		// TODO: catch only if internet is offline
		await $`docker compose -f ./docker_compose/docker-compose.yml pull`.catch(
			(rej) => undefined,
		);
		await $`docker compose -f ./docker_compose/docker-compose.yml up -d`;
	}
	if (securityLevel == "demo") {
		yield logMessage("adding demo users");
		await $`docker exec shabti uv run -m add_keycloak_demo_users`;
	}
	// TODO: wait for Llama.cpp to come online and preload the models
	if (selectedVersion == "local") {
		// in the development environment we stop the containers as the expectation is that they will be run in watch mode
		await $`docker compose -f ./docker_compose/docker-compose-dev.yml stop`;
	}
	delete envs[INCOMPLETE_KEY];
	delete process.env[INCOMPLETE_KEY];
	await updateEnv();
	console.log("Installation done\n");
}
