import getEnvs from "./getEnvs";

/**
 * doInstall sets this before it writes anything and clears it only once it reaches the end, so
 * finding it in the environment file means an install crashed part way through.
 */
export const INCOMPLETE_KEY = "SHABTI_INSTALL_INCOMPLETE";

export default async () => (await getEnvs())[INCOMPLETE_KEY] == "True";
