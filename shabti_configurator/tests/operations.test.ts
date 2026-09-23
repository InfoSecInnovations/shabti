import { describe, expect, spyOn, test } from "bun:test";
import { Hono } from "hono";
import {
	INTERRUPTED,
	type Line,
	type OperationEvent,
	type OperationUpdate,
} from "../server/operationProtocol";
import { operationRoutes, runOperation } from "../server/operationRoutes";

const TITLE = "Doing <the> thing";

const parseEvents = (body: string) =>
	body
		.split("\n\n")
		.filter((block) => block.trim())
		.map((block) => {
			const fields = Object.fromEntries(
				block.split("\n").map((field) => {
					const colon = field.indexOf(": ");
					return [field.slice(0, colon), field.slice(colon + 2)];
				}),
			);
			return {
				event: fields.event,
				data: JSON.parse(fields.data!),
			} as OperationEvent;
		});

/** what the page ends up showing, applied the way the client applies the events */
const linesFrom = (events: OperationEvent[]) => {
	const lines: Line[] = [];
	for (const event of events)
		if (event.event == "line") lines[event.data.index] = event.data.line;
	return lines;
};

const statusesIn = (events: OperationEvent[]) =>
	events.filter((event) => event.event == "status").map((event) => event.data);

const start = async (
	updates: AsyncIterable<OperationUpdate>,
	endMessage?: string,
) => {
	const app = new Hono();
	app.post("/t", (c) => runOperation(c, TITLE, updates, endMessage));
	app.route("/operations", operationRoutes);
	const res = await app.request("/t", { method: "POST" });
	return { app, res, location: res.headers.get("Location")! };
};

const read = async (app: Hono, location: string) => {
	const res = await app.request(`${location}/events`);
	const events = parseEvents(await res.text());
	return {
		res,
		events,
		lines: linesFrom(events),
		statuses: statusesIn(events),
	};
};

// the operation runs whether or not anything is watching, the events stream ends with its verdict
const watch = async (
	updates: AsyncIterable<OperationUpdate>,
	endMessage?: string,
) => {
	const spy = spyOn(console, "error").mockImplementation(() => {});
	try {
		const { app, location } = await start(updates, endMessage);
		return { app, location, ...(await read(app, location)) };
	} finally {
		spy.mockRestore();
	}
};

async function* nothing() {}

describe("operations", () => {
	test("hands the form's POST over to the operation page", async () => {
		const { app, res, location } = await start(nothing());
		expect(res.status).toBe(303);
		expect(location).toMatch(/^\/operations\/[\w-]+$/);
		const page = await app.request(location);
		const html = await page.text();
		expect(html).toContain("Doing &lt;the&gt; thing");
		expect(html).toContain(`events="${location}/events"`);
	});

	test("serves the events as SSE", async () => {
		const { res } = await watch(nothing());
		expect(res.headers.get("Content-Type")).toBe("text/event-stream");
	});

	test("reports success only when the operation runs to the end", async () => {
		const { statuses } = await watch(nothing());
		expect(statuses).toEqual([
			{ status: "done", message: "Operation completed successfully." },
		]);
	});

	test("uses the caller's completion message", async () => {
		const { statuses } = await watch(
			nothing(),
			"Shabti installed successfully.",
		);
		expect(statuses).toEqual([
			{ status: "done", message: "Shabti installed successfully." },
		]);
	});

	test("reports a thrown error and no success", async () => {
		const { statuses } = await watch(
			(async function* () {
				throw new Error("docker is not running");
			})(),
		);
		expect(statuses).toEqual([
			{ status: "failed", message: "docker is not running" },
		]);
	});

	// values which aren't Errors are what let a crashed install look like a clean one before
	test.each([
		["undefined", undefined],
		["a string", "boom"],
		["a plain object", { code: 1 }],
	])("reports a failure when %s is thrown", async (_label, thrown) => {
		const { statuses } = await watch(
			(async function* () {
				throw thrown;
			})(),
		);
		expect(statuses.map((status) => status.status)).toEqual(["failed"]);
	});

	test("keeps the progress log when the operation fails part way through", async () => {
		const { events, lines } = await watch(
			(async function* () {
				yield "first";
				yield "second";
				yield "third";
				throw new Error("boom");
			})(),
		);
		expect(lines).toEqual(
			["first", "second", "third"].map((text) => ({ kind: "message", text })),
		);
		expect(events.at(-1)).toEqual({
			event: "status",
			data: { status: "failed", message: "boom" },
		});
	});

	test("moves a progress bar on in place rather than adding lines", async () => {
		const progress = (done: number) => ({
			key: "model/file",
			label: "file for model",
			done,
			total: 10,
			detail: `${done} / 10`,
		});
		const { lines } = await watch(
			(async function* () {
				yield "before";
				yield progress(1);
				yield progress(5);
				yield "after";
				yield progress(10);
			})(),
		);
		expect(lines).toEqual([
			{ kind: "message", text: "before" },
			{ kind: "progress", ...progress(10) },
			{ kind: "message", text: "after" },
		]);
	});

	// a reload, or EventSource reconnecting, has to catch up without repeating anything
	test("replays each line once to a page connecting after the end", async () => {
		const { app, location } = await watch(
			(async function* () {
				yield "first";
				for (const done of [1, 2, 3])
					yield { key: "k", label: "l", done, total: 3, detail: "" };
			})(),
		);
		const { events } = await read(app, location);
		expect(events.map((event) => event.event)).toEqual([
			"line",
			"line",
			"status",
		]);
		expect(linesFrom(events)[1]).toMatchObject({ done: 3 });
	});

	test("streams what happens after the page connects", async () => {
		let release!: () => void;
		const gate = new Promise<void>((resolve) => {
			release = resolve;
		});
		const { app, location } = await start(
			(async function* () {
				yield "first";
				await gate;
				yield "second";
			})(),
		);
		const res = await app.request(`${location}/events`);
		release();
		const events = parseEvents(await res.text());
		expect(linesFrom(events)).toEqual([
			{ kind: "message", text: "first" },
			{ kind: "message", text: "second" },
		]);
		expect(events.at(-1)?.event).toBe("status");
	});

	test("says the operation is gone when the id isn't known", async () => {
		const app = new Hono().route("/operations", operationRoutes);
		const page = await app.request("/operations/missing");
		expect(page.status).toBe(404);
		expect(await page.text()).toContain(INTERRUPTED.replaceAll("'", "&#39;"));
		const events = await app.request("/operations/missing/events");
		expect(events.status).toBe(404);
	});
});
