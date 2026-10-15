import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdir, writeFile } from "node:fs/promises";
import manageProviders from "../server/manageProviders";
import writeProviders, { readProviders } from "../server/writeProviders";
import { drain, useTempCwd } from "./mocks";

// an existing installation, with its providers in the working directory
const cwd = useTempCwd();

beforeEach(async () => {
	await cwd.enter();
	await writeProviders(
		{
			providers: [
				{
					id: "remote",
					name: "Remote",
					base_url: "https://api.test/v1",
					models: { allow: ["chat"] },
				},
				{
					id: "other",
					name: "Other",
					base_url: "https://other.test/v1",
					models: { allow: ["chat"] },
				},
			],
			default_chat_model: "remote/chat",
		},
		{ remote: "sk-remote", other: "sk-other" },
	);
});

afterEach(async () => {
	await cwd.leave();
});

const form = (fields: Record<string, string | string[]>) => {
	const data = new FormData();
	for (const [name, value] of Object.entries(fields))
		for (const item of [value].flat()) data.append(name, item);
	return data;
};

describe("manageProviders", () => {
	test("changes nothing when nothing was filled in", async () => {
		const before = await readProviders();
		await drain(manageProviders(form({ key_remote: "" })));
		expect(await readProviders()).toEqual(before);
	});

	test("adds a provider with its key", async () => {
		await drain(
			manageProviders(
				form({
					provider_name: "New",
					provider_base_url: "https://new.test/v1/models",
					provider_key: "sk-new",
					provider_model: "chat",
				}),
			),
		);
		const { config, keys } = await readProviders();
		expect(config.providers.map((p) => p.id)).toEqual([
			"remote",
			"other",
			"new",
		]);
		expect(config.providers[2]!.base_url).toBe("https://new.test/v1");
		expect(keys.new).toBe("sk-new");
	});

	test("removes a provider, its key and the default it was", async () => {
		const { updates } = await drain(
			manageProviders(form({ remove_provider: "remote" })),
		);
		const { config, keys } = await readProviders();
		expect(config.providers.map((p) => p.id)).toEqual(["other"]);
		expect(keys).toEqual({ other: "sk-other" });
		expect(config.default_chat_model).toBeUndefined();
		expect(updates.join("\n")).toContain("remote/chat was the default");
	});

	test("keeps the default when another provider is removed", async () => {
		await drain(manageProviders(form({ remove_provider: "other" })));
		expect((await readProviders()).config.default_chat_model).toBe(
			"remote/chat",
		);
	});

	test("replaces a key that is filled in and keeps the rest", async () => {
		await drain(manageProviders(form({ key_remote: "sk-new", key_other: "" })));
		expect((await readProviders()).keys).toEqual({
			remote: "sk-new",
			other: "sk-other",
		});
	});

	test("can replace a removed provider with one of the same name", async () => {
		await drain(
			manageProviders(
				form({
					remove_provider: "remote",
					provider_name: "Remote",
					provider_base_url: "https://moved.test/v1",
					provider_model: "chat",
				}),
			),
		);
		const { config, keys } = await readProviders();
		expect(config.providers.find((p) => p.id == "remote")?.base_url).toBe(
			"https://moved.test/v1",
		);
		// the old key went with the provider it belonged to
		expect(keys.remote).toBeUndefined();
	});

	test("leaves everything as it was when the new provider is refused", async () => {
		const before = await readProviders();
		await expect(
			drain(
				manageProviders(
					form({
						remove_provider: "other",
						provider_name: "Remote",
						provider_base_url: "https://api.test/v1",
						provider_model: "chat",
					}),
				),
			),
		).rejects.toThrow("There is already a model provider called Remote.");
		expect(await readProviders()).toEqual(before);
	});

	test("won't remove the provider that embeds", async () => {
		const { config, keys } = await readProviders();
		await writeProviders(
			{ ...config, embeddings: { model: "remote/text-embed" } },
			keys,
		);
		const before = await readProviders();
		await expect(
			drain(manageProviders(form({ remove_provider: "remote" }))),
		).rejects.toThrow("The embeddings provider can't be removed.");
		expect(await readProviders()).toEqual(before);
	});

	test("keeps the embeddings model through other changes", async () => {
		const { config, keys } = await readProviders();
		await writeProviders(
			{
				...config,
				embeddings: { model: "remote/text-embed", dimensions: 256 },
			},
			keys,
		);
		await drain(
			manageProviders(form({ remove_provider: "other", key_remote: "sk-new" })),
		);
		expect((await readProviders()).config.embeddings).toEqual({
			model: "remote/text-embed",
			dimensions: 256,
		});
	});
});

describe("whose key a provider uses", () => {
	// users' own keys need security fully enabled, which is in the environment file
	const securityLevel = async (level: "none" | "demo" | "enabled") => {
		await mkdir("docker_compose", { recursive: true });
		await writeFile(
			"docker_compose/.env",
			[
				`SHABTI_SECURITY_ENABLED=${level == "none" ? "False" : "True"}`,
				...(level == "demo" ? ["IS_SECURITY_DEMO=True"] : []),
			].join("\n"),
		);
	};

	test("changes to users' own keys with security enabled", async () => {
		await securityLevel("enabled");
		const { updates } = await drain(
			manageProviders(form({ mode_remote: "user", mode_other: "shared" })),
		);
		const { config, keys } = await readProviders();
		expect(config.providers[0]!.credential_mode).toBe("user");
		expect(config.providers[1]!.credential_mode).toBeUndefined();
		// the shared key stays, for when it's changed back
		expect(keys.remote).toBe("sk-remote");
		expect(updates.join("\n")).toContain("changing whose API key Remote uses");
	});

	test("changes back to the shared key", async () => {
		await securityLevel("enabled");
		await drain(manageProviders(form({ mode_remote: "shared_or_user" })));
		await drain(manageProviders(form({ mode_remote: "shared" })));
		expect(
			(await readProviders()).config.providers[0]!.credential_mode,
		).toBeUndefined();
	});

	test("adds a provider taking users' own keys", async () => {
		await securityLevel("enabled");
		await drain(
			manageProviders(
				form({
					provider_name: "New",
					provider_base_url: "https://new.test/v1",
					provider_model: "chat",
					provider_credential_mode: "user",
				}),
			),
		);
		expect((await readProviders()).config.providers[2]!.credential_mode).toBe(
			"user",
		);
	});

	test.each(["none", "demo"] as const)(
		"refuses users' own keys with security %p",
		async (level) => {
			await securityLevel(level);
			const before = await readProviders();
			await expect(
				drain(manageProviders(form({ mode_remote: "user" }))),
			).rejects.toThrow(
				"Users' own keys for Remote need security to be enabled.",
			);
			expect(await readProviders()).toEqual(before);
		},
	);

	test("refuses users' own keys for the embeddings provider", async () => {
		await securityLevel("enabled");
		const { config, keys } = await readProviders();
		await writeProviders(
			{ ...config, embeddings: { model: "remote/text-embed" } },
			keys,
		);
		await expect(
			drain(manageProviders(form({ mode_remote: "user" }))),
		).rejects.toThrow("Remote embeds the documents, so it needs a shared key.");
	});
});
