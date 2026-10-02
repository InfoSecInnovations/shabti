import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import type { PullEvent } from "../server/docker";
import type {
	OperationUpdate,
	ProgressUpdate,
} from "../server/operationProtocol";
import pullProgress from "../server/pullProgress";

const IMAGE = "Image apache/tika:3.3.0.0-full";

const layer = (
	id: string,
	text: string,
	current?: number,
	total?: number,
): PullEvent => ({
	id,
	parent_id: IMAGE,
	status: "Working",
	text,
	current,
	total,
});

/** feeds the events through, keeping what came out and what the pull returned */
const run = async (events: PullEvent[], succeeded = true) => {
	const updates: OperationUpdate[] = [];
	const pull = pullProgress(
		(async function* () {
			yield* events;
			return succeeded;
		})(),
	);
	while (true) {
		const next = await pull.next();
		if (next.done) return { result: next.value, updates };
		updates.push(next.value);
	}
};

const bars = (updates: OperationUpdate[]) =>
	updates.filter(
		(update): update is ProgressUpdate => typeof update != "string",
	);

beforeEach(() => {
	// far enough apart that nothing gets throttled
	let now = 0;
	spyOn(Date, "now").mockImplementation(() => (now += 1000));
	spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
	mock.restore();
});

describe("pullProgress", () => {
	test("adds up the bytes of every layer", async () => {
		const { updates } = await run([
			layer("a", "Downloading", 10, 100),
			layer("b", "Downloading", 20, 50),
		]);
		expect(bars(updates).at(-1)).toMatchObject({
			key: "image/apache/tika:3.3.0.0-full",
			done: 30,
			total: 150,
		});
	});

	test("doesn't move back while the layers are extracted", async () => {
		const { updates } = await run([
			layer("a", "Downloading", 90, 100),
			layer("a", "Download complete"),
			layer("a", "Extracting", 5, 100),
		]);
		expect(bars(updates).map((bar) => bar.done)).toEqual([90, 100, 100]);
		expect(bars(updates).at(-1)!.detail).toBe("extracting");
	});

	test("leaves out the layers that are already downloaded", async () => {
		const { updates } = await run([
			layer("a", "Downloading", 10, 100),
			layer("b", "Already exists"),
		]);
		expect(bars(updates).at(-1)).toMatchObject({ done: 10, total: 100 });
	});

	test("fills the bar of an image that was already up to date", async () => {
		const { updates } = await run([
			{ id: IMAGE, status: "Working", text: "Pulling" },
			{ id: IMAGE, status: "Done", text: "Pulled" },
		]);
		expect(bars(updates).at(-1)).toMatchObject({
			done: 1,
			total: 1,
			detail: "done",
		});
	});

	test("leaves out the services that get built", async () => {
		const { updates } = await run([
			{
				id: "shabti",
				status: "Done",
				text: "Skipped",
				details: "No image to be pulled",
			},
		]);
		expect(updates).toEqual([]);
	});

	test("reports a failed image with the reason", async () => {
		const { result, updates } = await run(
			[
				{
					id: IMAGE,
					status: "Error",
					text: "Error",
					details: "TLS handshake timeout",
				},
			],
			false,
		);
		expect(result).toBe(false);
		expect(updates).toContain(
			"couldn't pull apache/tika:3.3.0.0-full: TLS handshake timeout",
		);
	});
});
