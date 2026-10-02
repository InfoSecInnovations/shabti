import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import describeError from "../server/describeError";
import { compose, composePull } from "../server/docker";
import { ComposeCommandError } from "../server/errors";
import { drain } from "./mocks";

const stream = (lines: string[]) =>
	new ReadableStream<Uint8Array>({
		start(controller) {
			for (const line of lines)
				controller.enqueue(new TextEncoder().encode(`${line}\n`));
			controller.close();
		},
	});

/** stands in for the docker process, and records the commands it was started with */
const spawned = (output: {
	stdout?: string[];
	stderr?: string[];
	exitCode?: number;
}) => {
	const commands: string[][] = [];
	spyOn(Bun, "spawn").mockImplementation(((command: string[]) => {
		commands.push(command);
		return {
			stdout: stream(output.stdout || []),
			stderr: stream(output.stderr || []),
			exited: Promise.resolve(output.exitCode ?? 0),
			exitCode: output.exitCode ?? 0,
			kill: () => {},
		};
	}) as any);
	return commands;
};

const event = (id: string, status: string, text: string) =>
	JSON.stringify({ id, status, text });

let logged: string[];
let errored: string[];

beforeEach(() => {
	logged = [];
	errored = [];
	spyOn(console, "log").mockImplementation((line: string) => {
		logged.push(line);
	});
	spyOn(console, "error").mockImplementation((line: string) => {
		errored.push(line);
	});
});

afterEach(() => {
	mock.restore();
});

describe("compose", () => {
	test("asks for the progress as JSON", async () => {
		const commands = spawned({});
		await drain(compose("compose.yml", ["up", "-d"]));
		expect(commands).toEqual([
			[
				"docker",
				"compose",
				"--progress",
				"json",
				"-f",
				"compose.yml",
				"up",
				"-d",
			],
		]);
	});

	test("passes on compose's events and the build's progress", async () => {
		spawned({
			stderr: [
				event("Container tika", "Done", "Started"),
				JSON.stringify({ level: "warning", msg: "a variable is not set" }),
			],
			stdout: [
				JSON.stringify({ vertexes: [{ digest: "a", name: "step" }] }),
				"output of the container",
			],
		});
		const { updates } = await drain(compose("compose.yml", ["up", "-d"]));
		expect(updates).toHaveLength(2);
		expect(updates).toContainEqual({
			kind: "event",
			event: { id: "Container tika", status: "Done", text: "Started" },
		});
		expect(updates).toContainEqual({
			kind: "build",
			status: { vertexes: [{ digest: "a", name: "step" }] },
		});
		expect(logged).toEqual(["output of the container"]);
		expect(errored).toEqual(["warning: a variable is not set"]);
	});

	test("fails with compose's own error text", async () => {
		spawned({
			stderr: ["Error response from daemon: port is already allocated"],
			exitCode: 1,
		});
		const error = await drain(compose("compose.yml", ["up", "-d"])).catch(
			(e) => e,
		);
		expect(error).toBeInstanceOf(ComposeCommandError);
		expect(describeError(error)).toBe(
			"Command failed with exit code 1: Error response from daemon: port is already allocated",
		);
	});

	test("returns the exit code instead of failing when asked to", async () => {
		spawned({ exitCode: 1 });
		const { result } = await drain(
			compose("compose.yml", ["pull"], { nothrow: true }),
		);
		expect(result).toBe(1);
	});
});

describe("composePull", () => {
	const IMAGE = "Image apache/tika:3.3.0.0-full";

	test("goes through when every image is pulled", async () => {
		spawned({
			stderr: [
				event(IMAGE, "Working", "Pulling"),
				event(IMAGE, "Done", "Pulled"),
			],
		});
		expect((await drain(composePull("compose.yml", {}, false))).result).toBe(
			true,
		);
	});

	test("fails when an image never finishes, even though compose exits cleanly", async () => {
		spawned({ stderr: [event(IMAGE, "Working", "Pulling")] });
		expect((await drain(composePull("compose.yml", {}, false))).result).toBe(
			false,
		);
	});

	test("fails when an image reports an error", async () => {
		spawned({
			stderr: [
				event(IMAGE, "Working", "Pulling"),
				event(IMAGE, "Error", "Error"),
			],
		});
		expect((await drain(composePull("compose.yml", {}, false))).result).toBe(
			false,
		);
	});

	test("fails when compose does", async () => {
		spawned({ exitCode: 1 });
		expect((await drain(composePull("compose.yml", {}, false))).result).toBe(
			false,
		);
	});
});
