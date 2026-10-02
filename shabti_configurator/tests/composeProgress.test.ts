import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import composeProgress from "../server/composeProgress";
import type {
	BuildStatus,
	ComposeEvent,
	ComposeOutput,
} from "../server/docker";
import type {
	OperationUpdate,
	ProgressUpdate,
} from "../server/operationProtocol";
import { drain } from "./mocks";

const IMAGE = "Image apache/tika:3.3.0.0-full";

const layer = (
	id: string,
	text: string,
	current?: number,
	total?: number,
): ComposeEvent => ({
	id,
	parent_id: IMAGE,
	status: "Working",
	text,
	current,
	total,
});

const PULLING: ComposeEvent = { id: IMAGE, status: "Working", text: "Pulling" };
const PULLED: ComposeEvent = { id: IMAGE, status: "Done", text: "Pulled" };

/** feeds compose's output through, keeping what came out and what the command returned */
const run = (outputs: (ComposeEvent | BuildStatus)[], result = 0) =>
	drain(
		composeProgress(
			(async function* (): AsyncGenerator<ComposeOutput, number> {
				for (const output of outputs)
					yield "id" in output
						? { kind: "event", event: output }
						: { kind: "build", status: output };
				return result;
			})(),
		),
	);

const bars = (updates: OperationUpdate[]) =>
	updates.filter(
		(update): update is ProgressUpdate => typeof update != "string",
	);

let printed: string[];

beforeEach(() => {
	// far enough apart that nothing gets throttled
	let now = 0;
	spyOn(Date, "now").mockImplementation(() => (now += 1000));
	printed = [];
	spyOn(console, "log").mockImplementation((line: string) => {
		printed.push(line);
	});
	spyOn(process.stdout, "write").mockImplementation(() => true);
});

afterEach(() => {
	mock.restore();
});

describe("composeProgress", () => {
	test("adds up the bytes of every layer", async () => {
		const { updates } = await run([
			PULLING,
			layer("a", "Downloading", 10, 100),
			layer("b", "Downloading", 20, 50),
			PULLED,
		]);
		expect(bars(updates).at(-2)).toMatchObject({
			key: "image/apache/tika:3.3.0.0-full",
			done: 30,
			total: 150,
		});
	});

	test("doesn't move back while the layers are extracted", async () => {
		const { updates } = await run([
			PULLING,
			layer("a", "Downloading", 90, 100),
			layer("a", "Download complete"),
			layer("a", "Extracting", 5, 100),
			PULLED,
		]);
		expect(bars(updates).map((bar) => bar.done)).toEqual([
			0, 90, 100, 100, 100,
		]);
		expect(bars(updates).at(-2)!.detail).toBe("extracting");
	});

	test("leaves out the layers that are already downloaded", async () => {
		const { updates } = await run([
			PULLING,
			layer("a", "Downloading", 10, 100),
			layer("b", "Already exists"),
		]);
		expect(bars(updates)[2]).toMatchObject({ done: 10, total: 100 });
	});

	test("fills the bar of an image that was already up to date", async () => {
		const { updates } = await run([PULLING, PULLED]);
		expect(bars(updates).at(-1)).toMatchObject({
			done: 1,
			total: 1,
			detail: "done",
		});
	});

	test("reports a failed image with the reason", async () => {
		const { updates } = await run([
			PULLING,
			{
				id: IMAGE,
				status: "Error",
				text: "Error",
				details: "TLS handshake timeout",
			},
		]);
		expect(updates).toContain(
			"couldn't pull apache/tika:3.3.0.0-full: TLS handshake timeout",
		);
	});

	test("reports an image which never finishes as failed", async () => {
		const { updates } = await run([
			PULLING,
			layer("a", "Downloading", 10, 100),
		]);
		expect(bars(updates).at(-1)!.detail).toBe("failed");
		expect(updates).toContain(
			"couldn't finish pulling apache/tika:3.3.0.0-full",
		);
		expect(updates).toContainEqual(
			expect.stringContaining("max-download-attempts"),
		);
	});

	test("only suggests more download attempts when an image doesn't finish", async () => {
		const { updates } = await run([PULLING, PULLED]);
		expect(updates).not.toContainEqual(
			expect.stringContaining("max-download-attempts"),
		);
	});

	test("leaves out the services that get built", async () => {
		const { updates } = await run([
			{
				id: "shabti",
				status: "Done",
				text: "Skipped",
				details: "No image to be pulled",
			},
			{ id: "Image shabti-shabti", status: "Working", text: "Building" },
			{ id: "Image shabti-shabti", status: "Done", text: "Built" },
		]);
		expect(updates).toEqual([]);
	});

	test("puts containers in the terminal rather than on the page", async () => {
		const { updates } = await run([
			{ id: "Container llama_cpp", status: "Working", text: "Starting" },
			{ id: "Container llama_cpp", status: "Done", text: "Started" },
			{ id: "Container llama_cpp", status: "Done", text: "Started" },
		]);
		expect(updates).toEqual([]);
		expect(printed).toEqual([
			"Container llama_cpp Starting",
			"Container llama_cpp Started",
		]);
	});

	test("puts a container's failure on the page", async () => {
		const { updates } = await run([
			{
				id: "Container llama_cpp",
				status: "Error",
				text: "Error",
				details: "port is already allocated",
			},
		]);
		expect(updates).toContain(
			"Container llama_cpp Error: port is already allocated",
		);
	});

	test("counts the build's completed steps", async () => {
		const { updates } = await run([
			{
				vertexes: [
					{ digest: "a", name: "[shabti 1/2] FROM base", started: "t" },
					{ digest: "b", name: "[shabti 2/2] RUN uv sync" },
				],
			},
			{
				vertexes: [
					{ digest: "a", started: "t", completed: "t" },
					{ digest: "b", name: "[shabti 2/2] RUN uv sync", started: "t" },
				],
			},
		]);
		expect(bars(updates)).toMatchObject([
			{ key: "build", done: 0, total: 2, detail: "[shabti 1/2] FROM base" },
			{ key: "build", done: 1, total: 2, detail: "[shabti 2/2] RUN uv sync" },
			{ key: "build", done: 2, total: 2, detail: "done" },
		]);
	});

	test("reports a build step that fails", async () => {
		const { updates } = await run([
			{
				vertexes: [
					{
						digest: "a",
						name: "[shabti 2/2] RUN uv sync",
						started: "t",
						error: "exit code: 1",
					},
				],
			},
		]);
		expect(updates).toContain("[shabti 2/2] RUN uv sync failed: exit code: 1");
	});

	test("returns what the command returned", async () => {
		expect((await run([], 3)).result).toBe(3);
	});
});
