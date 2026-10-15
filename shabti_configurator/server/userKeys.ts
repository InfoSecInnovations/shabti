import getShabtiRunState from "./shabtiRunState";
import logMessage from "./logMessage";
import { runApiModule } from "./docker";
import { rotateSecretsKey } from "./writeProviders";

// users can only add keys of their own with security fully enabled: the demo's accounts are
// shared by everyone who has the password
export const userKeysEnabled = (envs: Record<string, string | undefined>) =>
	envs.SHABTI_SECURITY_ENABLED == "True" && envs.IS_SECURITY_DEMO != "True";

/**
 * Encrypts users' API keys under a new version of the installation's key. The API also
 * re-encrypts each one as it's used, so a stopped Shabti needn't be launched for this.
 */
export async function* rotateUserKeys(state: {
	watchProcess?: Bun.Subprocess;
}) {
	const version = await rotateSecretsKey();
	yield logMessage(`added version ${version} of the encryption key.`);
	const running =
		!!state.watchProcess || (await getShabtiRunState()).state != "stopped";
	if (!running) {
		yield logMessage(
			"Users' API keys will be re-encrypted as they're next used.",
		);
		return;
	}
	yield logMessage("re-encrypting users' API keys...");
	await runApiModule("rewrap_provider_keys");
}
