import { zxcvbn, zxcvbnOptions } from "@zxcvbn-ts/core";
import * as zxcvbnCommonPackage from "@zxcvbn-ts/language-common";
import * as zxcvbnEnPackage from "@zxcvbn-ts/language-en";
const options = {
	translations: zxcvbnEnPackage.translations,
	graphs: zxcvbnCommonPackage.adjacencyGraphs,
	dictionary: {
		...zxcvbnCommonPackage.dictionary,
		...zxcvbnEnPackage.dictionary,
	},
};

zxcvbnOptions.setOptions(options);

export default (formData: FormData) => {
	// the version list can be empty when we're offline
	if (!formData.get("version")) return false;
	const securityLevel = formData.get("security_level")?.toString();
	if (!securityLevel || securityLevel == "none") return true; // if you don't have security enabled Shabti will install fine with no options set
	// the install tears down any existing configuration, so Keycloak is always installed fresh
	// and we always need a valid password for it
	const keycloakPassword = formData.get("keycloak_password")?.toString();
	if (!keycloakPassword || zxcvbn(keycloakPassword).score < 4) return false;
	return true;
};
