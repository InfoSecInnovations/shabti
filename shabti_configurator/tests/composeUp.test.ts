import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import { composeUp } from "../server/composeProgress";
import * as docker from "../server/docker";
import { ComposeCommandError } from "../server/errors";
import { drain } from "./mocks";

// what the NVIDIA hook reports when its ldconfig goes over its CPU time limit on a busy machine
const LDCACHE_KILLED =
	"nvidia-container-cli: ldcache error: process /sbin/ldconfig terminated with signal 9";
const PORT_TAKEN = "Bind for 127.0.0.1:15130 failed: port is already allocated";

/** what each `up` in turn fails with, after which they all succeed */
let failures: string[];
let calls: string[][];

beforeEach(() => {
	failures = [];
	calls = [];
	spyOn(docker, "compose").mockImplementation(async function* (_file, args) {
		calls.push(args);
		const stderr = failures.shift();
		if (stderr) throw new ComposeCommandError(args, 1, stderr);
		return 0;
	});
	spyOn(Bun, "sleep").mockResolvedValue(undefined);
	spyOn(console, "error").mockImplementation(() => {});
	spyOn(console, "log").mockImplementation(() => {});
});

afterEach(() => {
	mock.restore();
});

describe("composeUp", () => {
	test("brings the containers up", async () => {
		const { result, updates } = await drain(composeUp("docker-compose.yml"));
		expect(result).toBe(0);
		expect(calls).toEqual([["up", "-d"]]);
		expect(updates).toEqual([]);
	});

	test("gets past the NVIDIA hook being killed", async () => {
		failures = [LDCACHE_KILLED];
		const { result, updates } = await drain(composeUp("docker-compose.yml"));
		expect(result).toBe(0);
		expect(calls).toHaveLength(2);
		expect(updates).toEqual([
			"retrying the container launch (attempt 2 of 3)...",
		]);
	});

	test("gives up with compose's error when the NVIDIA hook keeps failing", async () => {
		failures = [LDCACHE_KILLED, LDCACHE_KILLED, LDCACHE_KILLED];
		const error = await drain(composeUp("docker-compose.yml")).catch(
			(error) => error,
		);
		expect(error).toBeInstanceOf(ComposeCommandError);
		expect(error.stderr).toBe(LDCACHE_KILLED);
		expect(calls).toHaveLength(3);
	});

	test("doesn't retry other failures", async () => {
		failures = [PORT_TAKEN];
		const error = await drain(composeUp("docker-compose.yml")).catch(
			(error) => error,
		);
		expect(error).toBeInstanceOf(ComposeCommandError);
		expect(error.stderr).toBe(PORT_TAKEN);
		expect(calls).toHaveLength(1);
	});
});
