import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdir, readFile, stat } from "node:fs/promises";
import writeProviders, {
	getInstanceSaltPath,
	getProviderKeysPath,
	getProvidersPath,
	writeInstanceSalt,
} from "../server/writeProviders";
import { useTempCwd } from "./mocks";

// an install directory, which has neither file until an install writes them
const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
});

afterEach(async () => {
	await cwd.leave();
});

const PROVIDER = {
	id: "remote",
	name: "Remote",
	base_url: "https://api.test/v1",
	models: { allow: ["chat"] },
};

// Windows has no owner-only mode to check
const posix = process.platform !== "win32";

describe("writeProviders", () => {
	test("writes the providers where the API mounts them", async () => {
		await writeProviders({ providers: [PROVIDER] }, {});
		expect(JSON.parse(await readFile(getProvidersPath(), "utf8"))).toEqual({
			providers: [PROVIDER],
		});
	});

	test("keeps the keys out of the providers file", async () => {
		await writeProviders({ providers: [PROVIDER] }, { remote: "sk-secret" });
		expect(await readFile(getProvidersPath(), "utf8")).not.toContain(
			"sk-secret",
		);
		expect(JSON.parse(await readFile(getProviderKeysPath(), "utf8"))).toEqual({
			remote: "sk-secret",
		});
	});

	test.if(posix)("makes the keys readable by their owner alone", async () => {
		await writeProviders({ providers: [PROVIDER] }, { remote: "sk-secret" });
		expect((await stat(getProviderKeysPath())).mode & 0o777).toBe(0o600);
	});

	test("replaces what an earlier install wrote", async () => {
		await writeProviders({ providers: [PROVIDER] }, { remote: "sk-1" });
		await writeProviders({ providers: [] }, {});
		expect(JSON.parse(await readFile(getProvidersPath(), "utf8"))).toEqual({
			providers: [],
		});
		expect(JSON.parse(await readFile(getProviderKeysPath(), "utf8"))).toEqual(
			{},
		);
	});

	test("replaces a directory Docker made in the file's place", async () => {
		// a container launched before the file existed mounts an empty directory there
		await mkdir(getProviderKeysPath(), { recursive: true });
		await writeProviders({ providers: [] }, {});
		expect((await stat(getProviderKeysPath())).isFile()).toBeTrue();
	});
});

describe("writeInstanceSalt", () => {
	test("is random for each installation", async () => {
		await writeInstanceSalt();
		const first = await readFile(getInstanceSaltPath(), "utf8");
		await writeInstanceSalt();
		const second = await readFile(getInstanceSaltPath(), "utf8");
		expect(first).toMatch(/^[0-9a-f]{64}$/);
		expect(second).not.toBe(first);
	});

	test.if(posix)("is readable by its owner alone", async () => {
		await writeInstanceSalt();
		expect((await stat(getInstanceSaltPath())).mode & 0o777).toBe(0o600);
	});
});
