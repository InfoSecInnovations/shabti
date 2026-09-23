/**
 * Which lockfiles a set of edits invalidated, and regenerating them.
 *
 * Membership is derived rather than listed. Whether a project locks inside Docker is decided by whether
 * its [tool.uv.sources] paths are absolute, which is exactly the condition docs/developer/LOCKFILES.md
 * describes - /app/python_packages exists only in the container - so the rule stays correct on its own
 * instead of restating two directory names that could change.
 */

import path from "node:path";
import { run } from "../versioning/git";
import { escape } from "../versioning/write";

export const LOCK_ACTIONS = ["uv-host", "uv-docker", "bun"] as const;
export type LockAction = (typeof LOCK_ACTIONS)[number];

const COMMANDS: Record<LockAction, string[]> = {
	// the root uv.lock covers the uv workspace, and these sources resolve on the host
	"uv-host": ["uv", "lock"],
	// one run locks both container projects, and it is the only way they can be locked at all
	"uv-docker": ["bun", "run", "lock"],
	// one bun.lock covers every workspace member; --lockfile-only leaves node_modules alone
	bun: ["bun", "install", "--lockfile-only"],
};

const table = (value: unknown) =>
	value && typeof value === "object" ? (value as Record<string, unknown>) : {};

const dirOf = (file: string) => path.posix.dirname(file).replace(/^\.$/, "");

const globToPattern = (glob: string) =>
	new RegExp(
		`^${glob.replace(/\/+$/, "").split("*").map(escape).join("[^/]*")}$`,
	);

const readJson = async (repoDir: string, file: string) => {
	const handle = Bun.file(path.join(repoDir, file));
	return (await handle.exists()) ? table(JSON.parse(await handle.text())) : {};
};

const readToml = async (repoDir: string, file: string) => {
	const handle = Bun.file(path.join(repoDir, file));
	return (await handle.exists())
		? table(Bun.TOML.parse(await handle.text()))
		: {};
};

const ABSOLUTE = /^(\/|[A-Za-z]:[\\/])/;

/** whether this project's local sources only exist inside the container that builds it */
const locksInDocker = async (repoDir: string, file: string) => {
	const sources = table(table((await readToml(repoDir, file)).tool).uv);
	return Object.values(table(sources.sources)).some((source) => {
		const declared = table(source).path;
		return typeof declared === "string" && ABSOLUTE.test(declared);
	});
};

/**
 * The lockfiles these files invalidated, deduped and in the order to run them. A package.json outside
 * the bun workspace is refused rather than guessed at, and a python_packages project maps to nothing,
 * because those are libraries and have no lockfile.
 */
export const lockActionsFor = async (repoDir: string, files: string[]) => {
	const root = await readJson(repoDir, "package.json");
	const workspaces = Array.isArray(root.workspaces)
		? root.workspaces.filter((glob): glob is string => typeof glob === "string")
		: [];
	const patterns = workspaces.map(globToPattern);
	const members = (
		table(table((await readToml(repoDir, "pyproject.toml")).tool).uv)
			.workspace as { members?: unknown } | undefined
	)?.members;
	const uvMembers = (Array.isArray(members) ? members : [])
		.filter((glob): glob is string => typeof glob === "string")
		.map(globToPattern);

	const actions = new Set<LockAction>();
	for (const file of files) {
		const base = path.posix.basename(file);
		const dir = dirOf(file);
		if (base === "package.json") {
			if (dir === "" || patterns.some((pattern) => pattern.test(dir))) {
				actions.add("bun");
				continue;
			}
			throw new Error(
				`${file} is not in the root bun workspace, so its lockfile cannot be regenerated from here`,
			);
		}
		if (base !== "pyproject.toml") continue;
		if (await locksInDocker(repoDir, file)) {
			actions.add("uv-docker");
			continue;
		}
		if (dir === "" || uvMembers.some((pattern) => pattern.test(dir)))
			actions.add("uv-host");
		// anything else is a published library with no lockfile of its own
	}
	return LOCK_ACTIONS.filter((action) => actions.has(action));
};

/** the command each action would run, for a dry run or for --no-lock to print */
export const commandFor = (action: LockAction) => COMMANDS[action].join(" ");

export const regenerate = async (repoDir: string, actions: LockAction[]) => {
	const ran: string[] = [];
	for (const action of actions) {
		try {
			await run(COMMANDS[action] as string[], { cwd: repoDir });
		} catch (error) {
			throw new Error(
				action === "uv-docker"
					? `${commandFor(action)} failed, and it needs Docker: ${error}. On Linux set UV_LOCK_USER to "$(id -u):$(id -g)" so the lockfiles are not written as root`
					: `${commandFor(action)} failed: ${error}`,
			);
		}
		ran.push(commandFor(action));
	}
	return ran;
};
