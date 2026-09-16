import KcAdminClient from "@keycloak/keycloak-admin-client";
import { KeycloakUnavailableError } from "./errors";

// Keycloak has no healthcheck and takes a while to come up, so we retry. Bounded, and with a wait
// between attempts, so a wrong password surfaces as an error instead of spinning forever. 60 x 5s
// matches the "this can take a few minutes" the install tells the user to expect
export default async (attempts = 60, delayMs = 5000) => {
	process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
	let lastError: unknown;
	for (let attempt = 0; attempt < attempts; attempt++) {
		try {
			const kcClient = new KcAdminClient({
				baseUrl: "https://localhost:8443",
			});
			await kcClient.auth({
				username: "admin",
				password: process.env.KEYCLOAK_INITIAL_ADMIN_PASSWORD,
				grantType: "password",
				clientId: "admin-cli",
			});
			const secret = await kcClient.clients.getClientSecret({
				id: "7a3ec428-36f2-49c4-91b1-8288dc44acb0",
				realm: "shabti",
			});
			return secret.value!;
		} catch (error) {
			lastError = error;
			await Bun.sleep(delayMs);
		}
	}
	throw new KeycloakUnavailableError(attempts, { cause: lastError });
};
