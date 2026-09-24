import KcAdminClient from "@keycloak/keycloak-admin-client";
import getEnvs from "./getEnvs";
import { KeycloakUnavailableError } from "./errors";

// an admin connection as the shabti-auth service account, which the realm grants realm-admin
// so it can manage the realm's clients. The initial admin password is only used to bootstrap
// the realm during the install, the client secret is the connection method from then on
export default async (attempts = 60, delayMs = 5000) => {
	process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0"; // the Keycloak certificate is self signed
	const envs = await getEnvs();
	// the port is published on the host, so this reaches Keycloak whatever KC_HOSTNAME is set to
	const client = new KcAdminClient({
		baseUrl: "https://localhost:8443",
		realmName: "shabti",
	});
	let lastError: unknown;
	// Keycloak takes a while to come up, and `up -d` doesn't wait for its healthcheck, so we
	// retry. Bounded so a permanently wrong secret surfaces as an error instead of hanging forever
	for (let attempt = 0; attempt < attempts; attempt++) {
		try {
			await client.auth({
				grantType: "client_credentials",
				clientId: envs.KEYCLOAK_CLIENT_ID!,
				clientSecret: envs.KEYCLOAK_CLIENT_SECRET!,
			});
			return client;
		} catch (error) {
			lastError = error;
			await Bun.sleep(delayMs);
		}
	}
	throw new KeycloakUnavailableError(attempts, { cause: lastError });
};
