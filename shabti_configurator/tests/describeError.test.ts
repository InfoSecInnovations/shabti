import { describe, expect, spyOn, test } from "bun:test";
import describeError from "../server/describeError";

// Bun's ShellError is whatever `$` throws; describeError duck types it so the shape is all we need
const shellError = (exitCode: number, stderr: string) =>
	Object.assign(new Error(`Failed with exit code ${exitCode}`), {
		exitCode,
		stderr: Buffer.from(stderr),
	});

const quietly = <T>(func: () => T) => {
	const spy = spyOn(console, "error").mockImplementation(() => {});
	try {
		return func();
	} finally {
		spy.mockRestore();
	}
};

describe("describeError", () => {
	test("uses an error's own message", () => {
		expect(quietly(() => describeError(new Error("boom")))).toBe("boom");
	});

	test("falls back rather than showing an empty banner", () => {
		expect(quietly(() => describeError(new Error("")))).toMatch(
			/unknown error/,
		);
	});

	// the regression this whole change exists for: hono's stream() only reports what is
	// `instanceof Error`, so everything else used to vanish and look like a successful install
	test.each([
		["a string", "boom"],
		["undefined", undefined],
		["null", null],
		["a plain object", { code: 1 }],
	])("describes %s", (_label, thrown) => {
		expect(quietly(() => describeError(thrown))).toBeTruthy();
	});

	test("passes a thrown string through", () => {
		expect(quietly(() => describeError("boom"))).toBe("boom");
	});

	test("prefers a shell error's stderr to its useless message", () => {
		const description = quietly(() =>
			describeError(shellError(1, "Cannot connect to the Docker daemon")),
		);
		expect(description).toContain("exit code 1");
		expect(description).toContain("Cannot connect to the Docker daemon");
	});

	test("still names the exit code when a shell error said nothing", () => {
		expect(quietly(() => describeError(shellError(127, "  ")))).toBe(
			"Command failed with exit code 127.",
		);
	});

	test("caps a long message, keeping the start", () => {
		const description = quietly(() =>
			describeError(new Error(`start${"x".repeat(5000)}`), 400),
		);
		expect(description.length).toBe(400);
		expect(description.startsWith("start")).toBe(true);
	});

	test("caps long stderr, keeping the end", () => {
		const description = quietly(() =>
			describeError(shellError(1, `${"x".repeat(5000)}the actual reason`), 400),
		);
		expect(description.endsWith("the actual reason")).toBe(true);
		expect(description.length).toBeLessThan(500);
	});

	test("logs the original value to the terminal", () => {
		const spy = spyOn(console, "error").mockImplementation(() => {});
		const thrown = { code: 1 };
		try {
			describeError(thrown);
			expect(spy).toHaveBeenCalledTimes(1);
			expect(spy).toHaveBeenCalledWith(thrown);
		} finally {
			spy.mockRestore();
		}
	});
});
