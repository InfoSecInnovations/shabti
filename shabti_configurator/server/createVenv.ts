import path from "node:path";
import util from "node:util";
import { getConnectivity } from "./connectivity";
const exec = util.promisify(
	await import("node:child_process").then(
		(child_process) => child_process.exec,
	),
);

export default async (pathSegments: string[] = []) => {
	// some systems only have python3 as the executable and others only have python, just in case someone has a whole other configuration we'll log an error but continue installing
	await exec("uv sync", {
		cwd: path.resolve(path.join("..", ...pathSegments)),
		// offline uv can only install from its cache, and this makes it fail fast when that isn't enough
		env:
			(await getConnectivity()) == "offline"
				? { ...process.env, UV_OFFLINE: "1" }
				: process.env,
	});
};
