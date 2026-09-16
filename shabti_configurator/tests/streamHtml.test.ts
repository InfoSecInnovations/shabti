import { describe, expect, spyOn, test } from "bun:test";
import { Hono } from "hono";
import type { StreamingApi } from "hono/utils/stream";
import streamHtml from "../server/streamHtml";

// the whole streamed document, which is where the verdict lives: the status is committed before
// anything can go wrong, so the body is the only thing that tells the browser what happened
const run = async (
	func: (stream: StreamingApi) => Promise<void>,
	endMessage?: string,
) => {
	const spy = spyOn(console, "error").mockImplementation(() => {});
	try {
		const app = new Hono();
		app.get("/t", (c) => streamHtml(c, "Doing the thing", func, endMessage));
		const res = await app.request("/t");
		return { res, body: await res.text() };
	} finally {
		spy.mockRestore();
	}
};

const nothing = async () => {};

describe("streamHtml", () => {
	test("reports success only when the operation runs to the end", async () => {
		const { body } = await run(nothing);
		expect(body).toContain("done=Operation%20completed%20successfully.");
		expect(body).not.toContain('class="error"');
	});

	test("uses the caller's completion message", async () => {
		const { body } = await run(nothing, "Shabti installed successfully.");
		expect(body).toContain("done=Shabti%20installed%20successfully.");
	});

	test("serves the page as HTML", async () => {
		const { res } = await run(nothing);
		expect(res.headers.get("Content-Type")).toBe("text/html; charset=UTF-8");
	});

	test("shows a thrown error and reports no success", async () => {
		const { body } = await run(async () => {
			throw new Error("docker is not running");
		});
		expect(body).toContain("docker is not running");
		expect(body).toContain('href="/"');
		expect(body).toContain("window.shabtiFinished = true;");
		expect(body).not.toContain("done=");
	});

	// hono's stream() ignores `throw undefined` outright and only console.errors anything else which
	// isn't an Error, so relying on its onError is what let a crashed install look like a clean one
	test.each([
		["undefined", undefined],
		["a string", "boom"],
		["a plain object", { code: 1 }],
	])("reports a failure when %s is thrown", async (_label, thrown) => {
		const { body } = await run(async () => {
			throw thrown;
		});
		expect(body).toContain('class="error"');
		expect(body).not.toContain("done=");
	});

	test("keeps the progress log when the operation fails part way through", async () => {
		const { body } = await run(async (stream) => {
			for (const message of ["first", "second", "third"])
				await stream.writeln(`<p>${message}</p>`);
			throw new Error("boom");
		});
		for (const message of ["first", "second", "third"])
			expect(body).toContain(message);
		expect(body.indexOf("boom")).toBeGreaterThan(body.indexOf("third"));
		expect(body).not.toContain("done=");
	});

	// the watchdog carries the failure markup inside a script element, so an unescaped "</script>"
	// or quote in it would end the script early and leave the page unable to report anything
	test("writes a watchdog the browser can actually run", async () => {
		const { body } = await run(nothing);
		const scripts = [...body.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
			([, contents]) => contents!,
		);
		const watchdog = scripts.find((contents) =>
			contents.includes("shabtiFinished = false"),
		);
		expect(watchdog).toBeDefined();
		expect(() => new Function(watchdog!)).not.toThrow();
		expect(watchdog).not.toContain("</");
		// the markup really is in there, it's only the angle brackets which are escaped
		expect(watchdog).toContain("Back to the Shabti Configurator");
	});

	test("arms a failure the page can't silently escape", async () => {
		const { body } = await run(nothing);
		// the meta refresh this replaces sent a silently truncated page back to a clean main page
		expect(body).not.toContain("http-equiv");
		expect(body.indexOf("window.shabtiFinished = false;")).toBeLessThan(
			body.indexOf("window.shabtiFinished = true;"),
		);
	});
});
