import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import * as git from "../../versioning/git";
import { systemTools, upgrade, upgradeCommand, versionIn } from "../system";
import type { Dependency } from "../types";

const platform = process.platform;

afterEach(() => {
	mock.restore();
	Object.defineProperty(process, "platform", { value: platform });
});

const onPlatform = (value: NodeJS.Platform) =>
	Object.defineProperty(process, "platform", { value });

/** every command recorded, answering each version query with `output` */
const recorder = (output: Record<string, string> = {}) => {
	const ran: string[][] = [];
	spyOn(git, "run").mockImplementation(async (command) => {
		ran.push(command);
		return {
			exitCode: 0,
			stdout: output[command[0] as string] ?? "",
			stderr: "",
		};
	});
	return { ran };
};

const tool = (name: string, version?: string): Dependency => ({
	id: name,
	ecosystem: "system",
	name,
	occurrences: [],
	versions: version ? [version] : [],
	agreement: "agreed",
	precision: "exact",
	executable: `/usr/local/bin/${name}`,
});

describe("versionIn", () => {
	test("reads both tools' output", () => {
		expect(versionIn("1.3.11\n")).toBe("1.3.11");
		expect(versionIn("uv 0.11.1 (abc1234 2026-01-01)\n")).toBe("0.11.1");
		expect(versionIn("")).toBeNull();
	});
});

describe("systemTools", () => {
	test("asks each executable on PATH for its version", async () => {
		spyOn(Bun, "which").mockImplementation((name) => `/usr/local/bin/${name}`);
		recorder({ bun: "1.3.11\n", uv: "uv 0.11.1 (abc1234 2026-01-01)\n" });

		const tools = await systemTools("/repo");
		expect(
			tools.map(({ name, versions, executable }) => [
				name,
				versions,
				executable,
			]),
		).toEqual([
			["bun", ["1.3.11"], "/usr/local/bin/bun"],
			["uv", ["0.11.1"], "/usr/local/bin/uv"],
		]);
	});

	test("a tool not on PATH has no version and runs nothing", async () => {
		spyOn(Bun, "which").mockReturnValue(null);
		const { ran } = recorder();

		const tools = await systemTools("/repo");
		expect(tools.map(({ versions }) => versions)).toEqual([[], []]);
		expect(ran).toEqual([]);
	});
});

describe("upgradeCommand", () => {
	test("bun upgrade reaches the latest", () => {
		expect(upgradeCommand("bun", "1.3.12", "1.3.12")).toEqual([
			"bun",
			"upgrade",
		]);
	});

	test("any other bun version goes through the installer", () => {
		onPlatform("linux");
		expect(upgradeCommand("bun", "1.3.10", "1.3.12")).toEqual([
			"bash",
			"-c",
			"curl -fsSL https://bun.com/install | bash -s bun-v1.3.10",
		]);
		onPlatform("win32");
		expect(upgradeCommand("bun", "1.3.10", "1.3.12")[2]).toEndWith(
			"-Version 1.3.10",
		);
	});

	test("uv updates itself to any version", () => {
		expect(upgradeCommand("uv", "0.11.7", "0.12.0")).toEqual([
			"uv",
			"self",
			"update",
			"0.11.7",
		]);
	});
});

describe("upgrade", () => {
	test("runs each tool's command, skipping one already there", async () => {
		const { ran } = recorder();
		const result = await upgrade("/repo", [
			{ dependency: tool("bun", "1.3.11"), to: "1.3.12", latest: "1.3.12" },
			{ dependency: tool("uv", "0.11.1"), to: "0.11.1", latest: "0.12.0" },
		]);

		expect(ran).toEqual([["bun", "upgrade"]]);
		expect(result).toEqual({ ran: ["bun upgrade"], warnings: [] });
	});

	test("on Windows the bun installer is printed rather than run", async () => {
		onPlatform("win32");
		const { ran } = recorder();
		const result = await upgrade("/repo", [
			{ dependency: tool("bun", "1.3.12"), to: "1.3.10", latest: "1.3.12" },
		]);

		expect(ran).toEqual([]);
		expect(result.ran).toEqual([]);
		expect(result.warnings).toEqual([
			"close bun, then run in PowerShell: & ([scriptblock]::Create((irm https://bun.com/install.ps1))) -Version 1.3.10",
		]);
	});
});
