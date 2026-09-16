import { describe, expect, spyOn, test } from "bun:test";
import { Hono } from "hono";
import type { StreamingApi } from "hono/utils/stream";
import streamHtml from "../server/streamHtml";

/** the id the success block carries, which nothing else in the page does */
const SUCCESS = 'id="shabti_done"';

const scriptsIn = (body: string) =>
	[...body.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
		([, contents]) => contents!,
	);

// the whole streamed document, which is where the verdict lives: the status is committed before
// anything can go wrong, so the body is the only thing that tells the browser what happened.
// `visible` drops the watchdog's template, whose contents the browser parks in an inert fragment
// rather than rendering, so an assertion about what the user sees can't be satisfied by it
const run = async (
	func: (stream: StreamingApi) => Promise<void>,
	endMessage?: string,
) => {
	const spy = spyOn(console, "error").mockImplementation(() => {});
	try {
		const app = new Hono();
		app.get("/t", (c) => streamHtml(c, "Doing the thing", func, endMessage));
		const res = await app.request("/t");
		const body = await res.text();
		return {
			res,
			body,
			visible: body.replace(/<template[\s\S]*?<\/template>/g, ""),
		};
	} finally {
		spy.mockRestore();
	}
};

const nothing = async () => {};

describe("streamHtml", () => {
	test("reports success only when the operation runs to the end", async () => {
		const { body, visible } = await run(nothing);
		expect(body).toContain(SUCCESS);
		expect(body).toContain('data-message="Operation completed successfully."');
		expect(visible).not.toContain('class="error"');
	});

	test("uses the caller's completion message", async () => {
		const { body } = await run(nothing, "Shabti installed successfully.");
		expect(body).toContain('data-message="Shabti installed successfully."');
	});

	test("serves the page as HTML", async () => {
		const { res } = await run(nothing);
		expect(res.headers.get("Content-Type")).toBe("text/html; charset=UTF-8");
	});

	test("shows a thrown error and reports no success", async () => {
		const { body, visible } = await run(async () => {
			throw new Error("docker is not running");
		});
		expect(visible).toContain("docker is not running");
		expect(visible).toContain('href="/"');
		expect(body).toContain("window.shabtiFinished = true;");
		expect(body).not.toContain(SUCCESS);
	});

	// hono's stream() ignores `throw undefined` outright and only console.errors anything else which
	// isn't an Error, so relying on its onError is what let a crashed install look like a clean one
	test.each([
		["undefined", undefined],
		["a string", "boom"],
		["a plain object", { code: 1 }],
	])("reports a failure when %s is thrown", async (_label, thrown) => {
		const { body, visible } = await run(async () => {
			throw thrown;
		});
		expect(visible).toContain('class="error"');
		expect(body).not.toContain(SUCCESS);
	});

	test("keeps the progress log when the operation fails part way through", async () => {
		const { body, visible } = await run(async (stream) => {
			for (const message of ["first", "second", "third"])
				await stream.writeln(`<p>${message}</p>`);
			throw new Error("boom");
		});
		for (const message of ["first", "second", "third"])
			expect(visible).toContain(message);
		expect(visible.indexOf("boom")).toBeGreaterThan(visible.indexOf("third"));
		expect(body).not.toContain(SUCCESS);
	});

	// the scripts are written into the page verbatim, so a payload which doesn't parse would leave
	// it unable to report anything at all
	test("writes scripts the browser can actually run", async () => {
		const { body } = await run(nothing);
		const scripts = scriptsIn(body);
		expect(scripts.length).toBeGreaterThan(0);
		for (const contents of scripts)
			expect(() => new Function(contents)).not.toThrow();
	});

	// the interrupted message is cloned from a template and the completion message read off an
	// attribute, so neither has to be escaped into script context to get to the browser
	test("keeps every script payload free of interpolated data", async () => {
		const { body } = await run(nothing, 'Done "now" <ok>');
		for (const contents of scriptsIn(body)) {
			expect(contents).not.toContain("Back to the Shabti Configurator");
			expect(contents).not.toContain("Done");
		}
		// real markup, not entities: cloning the template has to yield elements rather than a
		// text node showing the user HTML source
		expect(body).toContain(
			'<template id="shabti_interrupted"><p class="error">',
		);
		expect(body).toContain("Back to the Shabti Configurator");
		// hono escapes the attribute, so the quotes and angle brackets can't break out of it
		expect(body).toContain('data-message="Done &quot;now&quot; &lt;ok&gt;"');
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
