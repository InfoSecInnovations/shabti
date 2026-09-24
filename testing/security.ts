import path from "node:path";
import * as dotenv from "dotenv";
import createCertificates from "../shabti_configurator/server/createCertificates";
import getKeycloakClientSecret from "../shabti_configurator/server/getKeycloakClientSecret";
import { runCommand } from "./stack";

const REPO = path.join(import.meta.dir, "..");
const CERT_DIR = path.join(import.meta.dir, "self_signed_certificates");
const ENV_DIR = path.join(import.meta.dir, "env");

/** written, not committed: it carries the Keycloak client secret and absolute local paths */
export const GENERATED_ENV = path.join(ENV_DIR, ".generated.env");

const certPaths = () => ({
	ROOT_CA: path.join(CERT_DIR, "root-ca.pem"),
	KEYCLOAK_CERT: path.join(CERT_DIR, "keycloak-cert.pem"),
	KEYCLOAK_CERT_KEY: path.join(CERT_DIR, "keycloak-key.pem"),
	API_CERT: path.join(CERT_DIR, "shabti-cert.pem"),
	API_KEY: path.join(CERT_DIR, "shabti-key.pem"),
});

const writeGeneratedEnv = async (extra: Record<string, string> = {}) => {
	const values = { ...certPaths(), ...extra };
	await Bun.write(
		GENERATED_ENV,
		`${Object.entries(values)
			.map(([key, value]) => `${key}=${value}`)
			.join("\n")}\n`,
	);
	// the host side helpers below read straight from process.env, so keep it in step
	dotenv.config({
		path: [path.join(ENV_DIR, "security-enabled-env"), GENERATED_ENV],
		override: true,
		quiet: true,
	});
};

/**
 * Everything the security-enabled type needs before any test can run: certificates, a Keycloak
 * that has finished importing its realm, and the client secret only Keycloak can tell us. This is
 * the mini-install that makes it the long one.
 */
export default async () => {
	console.log("creating certificates...");
	await createCertificates(CERT_DIR);
	// Keycloak's own compose fragment mounts the cert and key, so these have to be resolvable
	// before it is launched
	await writeGeneratedEnv();

	console.log("launching Keycloak...");
	const launched = await runCommand([
		"docker",
		"compose",
		"-p",
		"shabti",
		"-f",
		path.join(
			REPO,
			"shabti_configurator",
			"docker_compose",
			"docker-compose-launch-keycloak.yml",
		),
		"--env-file",
		path.join(ENV_DIR, "security-enabled-env"),
		"--env-file",
		GENERATED_ENV,
		"up",
		"-d",
	]);
	if (launched.code !== 0) throw new Error("could not launch Keycloak");

	// the secret is only Keycloak's to tell us once it has imported the realm, which takes a while,
	// so this polls
	console.log("waiting for the Keycloak client secret...");
	const secret = await getKeycloakClientSecret();
	await writeGeneratedEnv({ KEYCLOAK_CLIENT_SECRET: secret });
	console.log("got the Keycloak client secret.");
};

/** loads a type's env into process.env for the host side helpers that read it directly */
export const loadEnvFor = (testType: "disabled" | "enabled") =>
	dotenv.config({
		path: path.join(ENV_DIR, `security-${testType}-env`),
		override: true,
		quiet: true,
	});

/**
 * What --no-clean does instead of the mini-install. Regenerating the certificates would hand the
 * clients a key the already-running Keycloak does not have, so a reused run has to reuse the
 * whole security setup, not just the container state.
 */
export const reuseSecurity = async () => {
	if (!(await Bun.file(GENERATED_ENV).exists()))
		throw new Error(
			`--no-clean needs an earlier security-enabled run to have written ${GENERATED_ENV}`,
		);
	dotenv.config({
		path: [path.join(ENV_DIR, "security-enabled-env"), GENERATED_ENV],
		override: true,
		quiet: true,
	});
	console.log("reusing the existing certificates and Keycloak realm.");
};
