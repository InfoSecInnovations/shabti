/**
 * The uv and Bun installed on this machine, which no file in the repo pins but every lockfile and every
 * script depends on.
 *
 * Each is a dependency with no occurrences: its version is whatever the executable on PATH reports, and
 * setting it means running the tool's own upgrade rather than rewriting a file. Latest is read from the
 * registries that already mirror each release - npm's `bun` package and PyPI's `uv` - so the report and
 * `deps:set` judge these exactly as they judge everything else.
 */

import { run } from "../versioning/git";
import type { Client } from "./http";
import { npmCatalogue } from "./registries/npm";
import { pypiCatalogue } from "./registries/pypi";
import type { Catalogue, Dependency } from "./types";

type Tool = {
	version: string[];
	catalogue: (client: Client) => Promise<Catalogue>;
	/** the command that moves this install to `to`, where `latest` is what the registry calls newest */
	upgrade: (to: string, latest?: string) => string[];
	/** the docker image that is set to the same version, so the containers run what this machine does */
	image?: string;
};

/**
 * Bun's installer is the only way to a version other than the latest, and on Windows it refuses while
 * any bun process from the install is open - which this script, run by bun, always is.
 */
const bunInstaller = (to: string) =>
	process.platform === "win32"
		? [
				"powershell",
				"-c",
				`& ([scriptblock]::Create((irm https://bun.com/install.ps1))) -Version ${to}`,
			]
		: ["bash", "-c", `curl -fsSL https://bun.com/install | bash -s bun-v${to}`];

export const SYSTEM_TOOLS: Record<string, Tool> = {
	bun: {
		version: ["bun", "--version"],
		catalogue: (client) => npmCatalogue("bun", client),
		upgrade: (to, latest) =>
			to === latest ? ["bun", "upgrade"] : bunInstaller(to),
		// testing/images/Dockerfile.bun copies bun out of it; astral/uv is not tied to uv the same way,
		// because it is also the base image of what we ship
		image: "oven/bun",
	},
	uv: {
		version: ["uv", "--version"],
		catalogue: (client) => pypiCatalogue("uv", client),
		upgrade: (to) => ["uv", "self", "update", to],
	},
};

/** `1.3.11` from bun, `uv 0.11.1 (abc123 2026-01-01)` from uv */
export const versionIn = (output: string) =>
	output.match(/\d+\.\d+\.\d+\S*/)?.[0] ?? null;

const installed = async (
	repoDir: string,
	name: string,
	tool: Tool,
): Promise<Dependency> => {
	const executable = Bun.which(name) ?? undefined;
	const { stdout } = executable
		? await run(tool.version, { cwd: repoDir, allowFailure: true })
		: { stdout: "" };
	const version = versionIn(stdout);
	return {
		id: name,
		ecosystem: "system",
		name,
		occurrences: [],
		versions: version ? [version] : [],
		agreement: "agreed",
		precision: "exact",
		executable,
	};
};

/** every tool, installed or not; one that is not has no version, which the report calls unsupported */
export const systemTools = (repoDir: string) =>
	Promise.all(
		Object.entries(SYSTEM_TOOLS).map(([name, tool]) =>
			installed(repoDir, name, tool),
		),
	);

export const upgradeCommand = (name: string, to: string, latest?: string) => {
	const tool = SYSTEM_TOOLS[name];
	if (!tool) throw new Error(`${name} is not a system tool`);
	return tool.upgrade(to, latest);
};

/** runs each upgrade in turn, skipping a tool already at the version asked for */
export const upgrade = async (
	repoDir: string,
	targets: { dependency: Dependency; to: string; latest?: string }[],
) => {
	const ran: string[] = [];
	const warnings: string[] = [];
	for (const { dependency, to, latest } of targets) {
		if (dependency.versions.includes(to)) continue;
		const command = upgradeCommand(dependency.name, to, latest);
		// the script alone, since that is what gets pasted into PowerShell once bun is closed
		if (process.platform === "win32" && command[0] === "powershell") {
			warnings.push(`close bun, then run in PowerShell: ${command[2]}`);
			continue;
		}
		await run(command, { cwd: repoDir });
		ran.push(command.join(" "));
	}
	return { ran, warnings };
};
