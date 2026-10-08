import path from "node:path";
import { parseEnv } from "node:util";
import buildProgram from "./buildProgram";

// we set this to 'executable' when building the standalone
const environment = process.env.SHABTI_ENVIRONMENT_TYPE || "local";
// the environment file is in a different location when running in the executable as opposed to the local code
const envPath =
	environment == "executable"
		? ["docker_compose", ".env"]
		: ["..", "shabti_configurator", "docker_compose", ".env"];
const envFile = Bun.file(path.resolve(path.join(...envPath)));
// anything already set in the environment wins over the file
if (await envFile.exists())
	for (const [key, value] of Object.entries(parseEnv(await envFile.text())))
		process.env[key] ??= value;

const program = await buildProgram();
await program.parseAsync(Bun.argv);
