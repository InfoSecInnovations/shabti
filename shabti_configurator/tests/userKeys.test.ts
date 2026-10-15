import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import * as docker from "../server/docker";
import * as shabtiRunState from "../server/shabtiRunState";
import { rotateUserKeys, userKeysEnabled } from "../server/userKeys";
import {
	getSecretsKeyPath,
	type SecretsKey,
	writeSecretsKey,
} from "../server/writeProviders";
import { drain, useTempCwd } from "./mocks";

const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
	await writeSecretsKey();
});

afterEach(async () => {
	mock.restore();
	await cwd.leave();
});

describe("userKeysEnabled", () => {
	test.each([
		[{ SHABTI_SECURITY_ENABLED: "True" }, true],
		// the demo's accounts are shared by everyone who has the password
		[{ SHABTI_SECURITY_ENABLED: "True", IS_SECURITY_DEMO: "True" }, false],
		[{ SHABTI_SECURITY_ENABLED: "False" }, false],
		[{}, false],
	])("%p is %p", (envs, expected) => {
		expect(userKeysEnabled(envs)).toBe(expected);
	});
});

describe("rotateUserKeys", () => {
	const mockShabti = (state: "stopped" | "running") => {
		spyOn(shabtiRunState, "default").mockImplementation(
			async () => ({ state }) as any,
		);
		const modules: string[] = [];
		spyOn(docker, "runApiModule").mockImplementation((async (
			module: string,
		) => {
			modules.push(module);
		}) as any);
		return modules;
	};

	const currentVersion = async () =>
		(JSON.parse(await readFile(getSecretsKeyPath(), "utf8")) as SecretsKey)
			.current;

	test("re-encrypts the keys in a running Shabti", async () => {
		const modules = mockShabti("running");
		await drain(rotateUserKeys({}));
		expect(await currentVersion()).toBe(2);
		expect(modules).toEqual(["rewrap_provider_keys"]);
	});

	test("leaves a stopped Shabti to re-encrypt them as they're used", async () => {
		const modules = mockShabti("stopped");
		const { updates } = await drain(rotateUserKeys({}));
		expect(await currentVersion()).toBe(2);
		expect(modules).toEqual([]);
		expect(updates.join("\n")).toContain("re-encrypted as they're next used");
	});
});

describe("the realm", () => {
	test("declares users' provider keys, for admins alone", async () => {
		// Keycloak drops user attributes its user profile doesn't declare
		const realm = JSON.parse(
			await readFile(
				path.join(
					import.meta.dir,
					"..",
					"docker_compose",
					"docker_compose_dependencies",
					"keycloak_import",
					"shabti-realm.json",
				),
				"utf8",
			),
		);
		const profile = JSON.parse(
			realm.components["org.keycloak.userprofile.UserProfileProvider"][0]
				.config["kc.user.profile.config"][0],
		);
		const attribute = profile.attributes.find(
			(a: { name: string }) => a.name == "shabti_provider_keys",
		);
		expect(attribute.multivalued).toBeTrue();
		expect(attribute.permissions).toEqual({ view: ["admin"], edit: ["admin"] });
		// a sealed key is longer than Keycloak's default limit would allow
		expect(attribute.validations.length).toBeUndefined();
	});
});
