import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import { providerId } from "../providerIds";
import { providerRoutes } from "../server/providerRoutes";
import {
	normaliseBaseUrl,
	parseHeaders,
	providerFromForm,
	providersFromForm,
	remoteEmbeddingsFromForm,
	remoteModels,
	resolveDefaultModel,
	storedProvider,
	testProvider,
} from "../server/providers";
import * as writeProviders from "../server/writeProviders";
import { json, mockFetch } from "./mocks";

afterEach(() => {
	mock.restore();
});

const form = (fields: Record<string, string | string[]>) => {
	const data = new FormData();
	for (const [name, value] of Object.entries(fields))
		for (const item of [value].flat()) data.append(name, item);
	return data;
};

const PROVIDER_FORM = {
	provider_name: "Open Router",
	provider_base_url: "https://openrouter.ai/api/v1/",
	provider_key: "sk-1",
	provider_headers: "X-Title: Shabti",
	provider_model: ["openai/gpt-4o", "meta/llama"],
};

// an installation's providers, as readProviders reads them
const STORED: writeProviders.ProvidersConfig = {
	providers: [
		{
			id: "open-router",
			name: "Open Router",
			base_url: "https://openrouter.ai/api/v1",
			user_id_field: "user",
			reasoning_format: "openrouter",
			models: {
				allow: ["openai/gpt-4o"],
				overrides: { "openai/gpt-4o": { context_length: 128000 } },
			},
		},
	],
};
const STORED_PROVIDERS = {
	config: STORED,
	keys: { "open-router": "sk-stored" },
};

describe("providerId", () => {
	test.each([
		["OpenRouter", "openrouter"],
		["Open Router", "open-router"],
		["  My  LLM! (v2) ", "my-llm-v2"],
		["!!!", ""],
	])("is %p as %p", (name, id) => {
		expect(providerId(name)).toBe(id);
	});
});

describe("normaliseBaseUrl", () => {
	test.each([
		["https://api.test/v1", "https://api.test/v1"],
		["https://api.test/v1/", "https://api.test/v1"],
		["  https://api.test/v1  ", "https://api.test/v1"],
		["https://api.test/v1/chat/completions", "https://api.test/v1"],
		["https://api.test/v1/embeddings", "https://api.test/v1"],
		["https://api.test/v1/models/", "https://api.test/v1"],
		["http://localhost:11434/v1?x=1#y", "http://localhost:11434/v1"],
	])("makes %p %p", (raw, expected) => {
		expect(normaliseBaseUrl(raw)).toBe(expected);
	});

	test.each(["api.test/v1", "ftp://api.test", ""])("refuses %p", (raw) => {
		expect(() => normaliseBaseUrl(raw)).toThrow();
	});
});

describe("parseHeaders", () => {
	test("reads one header per line", () => {
		expect(parseHeaders("X-Title: Shabti\r\n\n  X-Org:  an org  \n")).toEqual({
			"X-Title": "Shabti",
			"X-Org": "an org",
		});
	});

	test.each(["no separator", ": no name", "Bad Name: value"])(
		"refuses %p",
		(raw) => {
			expect(() => parseHeaders(raw)).toThrow("Name: value");
		},
	);
});

describe("testProvider", () => {
	test("lists the provider's models in order", async () => {
		mockFetch((url) =>
			url == "https://api.test/v1/models"
				? json({ data: [{ id: "b" }, { id: "a" }, { id: 3 }] })
				: undefined,
		);
		expect(
			await testProvider({ baseUrl: "https://api.test/v1/chat/completions" }),
		).toEqual({
			baseUrl: "https://api.test/v1",
			models: ["a", "b"],
			embeddingsModels: [],
		});
	});

	test("finds the embeddings models a provider lists apart from the rest", async () => {
		// OpenRouter's /models leaves them out
		mockFetch((url) =>
			url == "https://api.test/v1/models"
				? json({ data: [{ id: "openai/gpt-4o" }] })
				: url == "https://api.test/v1/embeddings/models"
					? json({
							data: [
								{ id: "nvidia/llama-nemotron-embed-vl-1b-v2:free" },
								{ id: "voyageai/voyage-code-4" },
							],
						})
					: undefined,
		);
		expect(await testProvider({ baseUrl: "https://api.test/v1" })).toEqual({
			baseUrl: "https://api.test/v1",
			models: ["openai/gpt-4o"],
			embeddingsModels: [
				"nvidia/llama-nemotron-embed-vl-1b-v2:free",
				"voyageai/voyage-code-4",
			],
		});
	});

	test("tells the embeddings models in one listing apart from the chat models", async () => {
		mockFetch((url) =>
			url == "https://api.test/v1/models"
				? json({
						data: [
							{ id: "gpt-4o" },
							{ id: "text-embedding-3-small" },
							{
								id: "voyage-3",
								architecture: { output_modalities: ["embeddings"] },
							},
						],
					})
				: new Response("not found", { status: 404 }),
		);
		expect(await testProvider({ baseUrl: "https://api.test/v1" })).toEqual({
			baseUrl: "https://api.test/v1",
			models: ["gpt-4o"],
			embeddingsModels: ["text-embedding-3-small", "voyage-3"],
		});
	});

	test("lists a model only once when both listings name it", async () => {
		mockFetch(() => json({ data: [{ id: "text-embed" }] }));
		const result = await testProvider({ baseUrl: "https://api.test/v1" });
		expect(result).toEqual({
			baseUrl: "https://api.test/v1",
			models: [],
			embeddingsModels: ["text-embed"],
		});
	});

	test("sends the key and headers, and follows no redirect", async () => {
		const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((async () =>
			json({ data: [] })) as unknown as typeof fetch);
		await testProvider({
			baseUrl: "https://api.test/v1",
			key: "sk-1",
			headers: "X-Title: Shabti",
		});
		// both listings, the embeddings one too
		expect(fetchSpy.mock.calls.map((call) => String(call[0]))).toEqual([
			"https://api.test/v1/models",
			"https://api.test/v1/embeddings/models",
		]);
		for (const call of fetchSpy.mock.calls) {
			const init = call[1]!;
			expect(init.headers).toEqual({
				"X-Title": "Shabti",
				Authorization: "Bearer sk-1",
			});
			expect(init.redirect).toBe("manual");
		}
	});

	test("sends no authorization without a key", async () => {
		const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((async () =>
			json({ data: [] })) as unknown as typeof fetch);
		await testProvider({ baseUrl: "https://api.test/v1" });
		expect(fetchSpy.mock.calls[0]![1]!.headers).toEqual({});
	});

	test("says when the key is refused, without repeating the answer", async () => {
		mockFetch(() => new Response("bad key sk-secret", { status: 401 }));
		const result = await testProvider({ baseUrl: "https://api.test/v1" });
		expect(result).toEqual({ error: "The provider refused the key." });
	});

	test("suggests a version when the URL has none", async () => {
		mockFetch(() => new Response("not found", { status: 404 }));
		const result = await testProvider({ baseUrl: "https://api.test" });
		expect(result).toEqual({
			error: "https://api.test/models answered with status 404.",
			hint: "Most providers' base URLs end in a version, such as /v1.",
		});
	});

	test("suggests nothing when the URL has a version", async () => {
		mockFetch(() => new Response("not found", { status: 404 }));
		const result = await testProvider({ baseUrl: "https://api.test/v1" });
		expect("hint" in result && result.hint).toBeFalsy();
	});

	test("says when the provider can't be reached", async () => {
		mockFetch(() => undefined);
		expect(await testProvider({ baseUrl: "https://api.test/v1" })).toEqual({
			error: "Couldn't reach https://api.test/v1.",
		});
	});

	test("says when the answer isn't a model list", async () => {
		mockFetch(() => new Response("<html></html>"));
		const result = await testProvider({ baseUrl: "https://api.test/v1" });
		expect("error" in result && result.error).toBe(
			"https://api.test/v1/models didn't list any models.",
		);
	});

	test("reports a malformed header without asking anyone", async () => {
		const fetchSpy = mockFetch(() => json({ data: [] }));
		const result = await testProvider({
			baseUrl: "https://api.test/v1",
			headers: "nonsense",
		});
		expect("error" in result).toBeTrue();
		expect(fetchSpy).not.toHaveBeenCalled();
	});
});

describe("the test-provider route", () => {
	test("tests the provider it is sent", async () => {
		mockFetch((url) =>
			url.endsWith("/embeddings/models")
				? new Response("not found", { status: 404 })
				: json({ data: [{ id: "a" }] }),
		);
		const response = await providerRoutes.request("/test-provider", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ base_url: "https://api.test/v1", key: "sk-1" }),
		});
		expect(await response.json()).toEqual({
			baseUrl: "https://api.test/v1",
			models: ["a"],
			embeddingsModels: [],
		});
	});

	const testInstalled = (baseUrl: string) => {
		spyOn(writeProviders, "readProviders").mockResolvedValue(STORED_PROVIDERS);
		const fetchSpy = mockFetch(() => json({ data: [] }));
		return providerRoutes
			.request("/test-provider", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					base_url: baseUrl,
					key: "",
					existing: "open-router",
				}),
			})
			.then(() => fetchSpy.mock.calls[0]![1]!.headers);
	};

	test("tests an installed provider with the key it has", async () => {
		expect(await testInstalled("https://openrouter.ai/api/v1/")).toEqual({
			Authorization: "Bearer sk-stored",
		});
	});

	test("sends the installed provider's key nowhere else", async () => {
		expect(await testInstalled("https://elsewhere.test/v1")).toEqual({});
	});

	test("only takes JSON, so another site can't post to it without a preflight", async () => {
		const fetchSpy = mockFetch(() => json({ data: [] }));
		const response = await providerRoutes.request("/test-provider", {
			method: "POST",
			headers: { "Content-Type": "text/plain" },
			body: JSON.stringify({ base_url: "https://api.test/v1" }),
		});
		expect(response.status).toBe(415);
		expect(fetchSpy).not.toHaveBeenCalled();
	});
});

describe("providerFromForm", () => {
	test("is nothing when the fields are empty", () => {
		expect(providerFromForm(form({}), [])).toBeUndefined();
		expect(
			providerFromForm(form({ provider_name: " ", provider_key: "" }), []),
		).toBeUndefined();
	});

	test("reads the provider and its key apart", () => {
		expect(providerFromForm(form(PROVIDER_FORM), [])).toEqual({
			provider: {
				id: "open-router",
				name: "Open Router",
				base_url: "https://openrouter.ai/api/v1",
				headers: { "X-Title": "Shabti" },
				models: { allow: ["openai/gpt-4o", "meta/llama"] },
			},
			key: "sk-1",
		});
	});

	test("leaves out what wasn't given", () => {
		const added = providerFromForm(
			form({ ...PROVIDER_FORM, provider_key: "", provider_headers: "" }),
			[],
		);
		expect(added?.key).toBeUndefined();
		expect(added?.provider.headers).toBeUndefined();
	});

	test.each([
		[{ provider_name: "" }, "Give the model provider a name."],
		[{ provider_name: "!!!" }, "!!! needs at least one letter or digit."],
		[{ provider_name: "Local" }, '"Local" is reserved for the local models.'],
		[{ provider_base_url: "" }, "Give Open Router a base URL."],
		[
			{ provider_model: [] },
			"Test the connection to Open Router and choose at least one of its models.",
		],
	])("refuses %p", (fields, message) => {
		expect(() =>
			providerFromForm(form({ ...PROVIDER_FORM, ...fields }), []),
		).toThrow(message);
	});

	test("offers none of the chat models of a provider that only embeds", () => {
		const added = providerFromForm(
			form({ ...PROVIDER_FORM, provider_model: [] }),
			[],
			true,
		);
		// an empty list rather than none, which would offer every model the provider lists
		expect(added?.provider.models).toEqual({ allow: [] });
	});

	test("refuses a name another provider has", () => {
		expect(() =>
			providerFromForm(form(PROVIDER_FORM), ["open-router"]),
		).toThrow("There is already a model provider called Open Router.");
	});

	test("leaves the shared key mode unwritten", () => {
		const added = providerFromForm(
			form({ ...PROVIDER_FORM, provider_credential_mode: "shared" }),
			[],
			false,
			true,
		);
		expect(added?.provider.credential_mode).toBeUndefined();
	});

	test.each(["user", "shared_or_user"])(
		"takes users' own keys (%p) with security enabled",
		(mode) => {
			const added = providerFromForm(
				form({ ...PROVIDER_FORM, provider_credential_mode: mode }),
				[],
				false,
				true,
			);
			expect(added?.provider.credential_mode).toBe(mode as any);
		},
	);

	test("refuses users' own keys without security", () => {
		expect(() =>
			providerFromForm(
				form({ ...PROVIDER_FORM, provider_credential_mode: "user" }),
				[],
			),
		).toThrow("Users' own keys for Open Router need security to be enabled.");
	});

	test("refuses users' own keys for the provider that embeds", () => {
		expect(() =>
			providerFromForm(
				form({ ...PROVIDER_FORM, provider_credential_mode: "user" }),
				[],
				true,
				true,
			),
		).toThrow("Open Router embeds the documents, so it needs a shared key.");
	});

	test("refuses a mode it doesn't know", () => {
		expect(() =>
			providerFromForm(
				form({ ...PROVIDER_FORM, provider_credential_mode: "per-user" }),
				[],
				false,
				true,
			),
		).toThrow("per-user is not a credential mode.");
	});

	test("reads the fields with the given prefix", () => {
		const added = providerFromForm(
			form({
				p_provider_name: "Acme",
				p_provider_base_url: "https://api.acme.test/v1",
				p_provider_model: "chat",
				...PROVIDER_FORM,
			}),
			[],
			false,
			false,
			"p_",
		);
		expect(added?.provider.id).toBe("acme");
		expect(added?.provider.models).toEqual({ allow: ["chat"] });
	});

	const reinstalled = (fields: Record<string, string | string[]>) =>
		providerFromForm(
			form({
				...PROVIDER_FORM,
				provider_key: "",
				provider_existing: "open-router",
				...fields,
			}),
			[],
			false,
			false,
			"",
			STORED_PROVIDERS,
		);

	test("keeps what the installed provider it was prefilled from had", () => {
		const added = reinstalled({});
		expect(added?.key).toBe("sk-stored");
		expect(added?.provider.user_id_field).toBe("user");
		expect(added?.provider.reasoning_format).toBe("openrouter");
		expect(added?.provider.models).toEqual({
			allow: ["openai/gpt-4o", "meta/llama"],
			overrides: { "openai/gpt-4o": { context_length: 128000 } },
		});
	});

	test("takes a key typed in over the installed one", () => {
		expect(reinstalled({ provider_key: "sk-new" })?.key).toBe("sk-new");
	});

	test("keeps nothing of the installed provider at another URL", () => {
		const added = reinstalled({
			provider_base_url: "https://elsewhere.test/v1",
		});
		expect(added?.key).toBeUndefined();
		expect(added?.provider.user_id_field).toBeUndefined();
		expect(added?.provider.reasoning_format).toBeUndefined();
		expect(added?.provider.models).toEqual({
			allow: ["openai/gpt-4o", "meta/llama"],
		});
	});
});

describe("storedProvider", () => {
	test("is the installed provider and its key at the URL it had", () => {
		expect(
			storedProvider(
				STORED_PROVIDERS,
				"open-router",
				"https://openrouter.ai/api/v1/models",
			),
		).toEqual({ provider: STORED.providers[0]!, key: "sk-stored" });
	});

	test.each([
		["open-router", "https://elsewhere.test/v1"],
		["open-router", "not a url"],
		["acme", "https://openrouter.ai/api/v1"],
		["", "https://openrouter.ai/api/v1"],
	])("is nothing for %p at %p", (id, baseUrl) => {
		expect(storedProvider(STORED_PROVIDERS, id, baseUrl)).toBeUndefined();
	});

	test("is nothing without an installation", () => {
		expect(
			storedProvider(undefined, "open-router", "https://openrouter.ai/api/v1"),
		).toBeUndefined();
	});
});

describe("providersFromForm", () => {
	// the fields of one ProviderFieldset, as the form submits them
	const fieldset = (
		prefix: string,
		fields: Record<string, string | string[]>,
	): Record<string, string | string[]> => ({
		provider_prefix: prefix,
		...Object.fromEntries(
			Object.entries(fields).map(([name, value]) => [
				`${prefix}${name}`,
				value,
			]),
		),
	});
	// FormData keeps every value of a name, which a spread of objects wouldn't
	const forms = (...fieldsets: Record<string, string | string[]>[]) => {
		const data = new FormData();
		for (const fields of fieldsets)
			for (const [name, value] of Object.entries(fields))
				for (const item of [value].flat()) data.append(name, item);
		return data;
	};

	test("adds every fieldset filled in, in order, with their keys", () => {
		const added = providersFromForm(
			forms(
				fieldset("a_", PROVIDER_FORM),
				fieldset("b_", { provider_name: "", provider_base_url: "" }),
				fieldset("c_", {
					provider_name: "Acme",
					provider_base_url: "https://api.acme.test/v1",
					provider_model: "chat",
				}),
			),
			[],
		);
		expect(added.map((a) => [a.prefix, a.provider.id, a.key])).toEqual([
			["a_", "open-router", "sk-1"],
			["c_", "acme", undefined],
		]);
	});

	test("keeps the keys of the installed providers they were prefilled from", () => {
		const added = providersFromForm(
			forms(
				fieldset("a_", {
					...PROVIDER_FORM,
					provider_key: "",
					provider_existing: "open-router",
				}),
				fieldset("b_", {
					provider_name: "Acme",
					provider_base_url: "https://api.acme.test/v1",
					provider_model: "chat",
				}),
			),
			[],
			false,
			STORED_PROVIDERS,
		);
		expect(added.map((a) => [a.provider.id, a.key])).toEqual([
			["open-router", "sk-stored"],
			["acme", undefined],
		]);
	});

	test("is nothing without any fieldsets", () => {
		expect(providersFromForm(new FormData(), [])).toEqual([]);
	});

	test("refuses a name used twice", () => {
		expect(() =>
			providersFromForm(
				forms(fieldset("a_", PROVIDER_FORM), fieldset("b_", PROVIDER_FORM)),
				[],
			),
		).toThrow("There is already a model provider called Open Router.");
	});

	test("refuses a name a configured provider has", () => {
		expect(() =>
			providersFromForm(forms(fieldset("a_", PROVIDER_FORM)), ["open-router"]),
		).toThrow("There is already a model provider called Open Router.");
	});

	test("lets only the provider that embeds go without chat models", () => {
		const embedding = {
			embeddings_source: "provider",
			remote_embeddings_model: "b_:text-embed-3",
		};
		const added = providersFromForm(
			forms(
				fieldset("a_", PROVIDER_FORM),
				fieldset("b_", {
					provider_name: "Acme",
					provider_base_url: "https://api.acme.test/v1",
				}),
				embedding,
			),
			[],
		);
		expect(added[1]!.provider.models).toEqual({ allow: [] });
		expect(() =>
			providersFromForm(
				forms(
					fieldset("a_", { ...PROVIDER_FORM, provider_model: [] }),
					fieldset("b_", {
						provider_name: "Acme",
						provider_base_url: "https://api.acme.test/v1",
					}),
					embedding,
				),
				[],
			),
		).toThrow(
			"Test the connection to Open Router and choose at least one of its models.",
		);
	});
});

describe("the default chat model", () => {
	const remote = remoteModels({
		providers: [
			{
				id: "remote",
				name: "Remote",
				base_url: "https://api.test/v1",
				models: { allow: ["vendor/chat"] },
			},
			// every model, which the configurator can't list
			{ id: "open", name: "Open", base_url: "https://open.test/v1" },
		],
	});

	test("is offered from every provider that names its models", () => {
		expect(remote).toEqual(["remote/vendor/chat"]);
	});

	test("goes in the providers file when it is remote", () => {
		expect(
			resolveDefaultModel("remote/vendor/chat", ["a", "b"], remote),
		).toEqual({ defaultModel: "a", defaultChatModel: "remote/vendor/chat" });
	});

	test("goes in the preset file when it is local", () => {
		expect(resolveDefaultModel("b", ["a", "b"], remote)).toEqual({
			defaultModel: "b",
			defaultChatModel: undefined,
		});
	});

	test.each([undefined, "gone", "remote/gone"])(
		"falls back to the first local model from %p",
		(requested) => {
			expect(resolveDefaultModel(requested, ["a", "b"], remote)).toEqual({
				defaultModel: "a",
				defaultChatModel: undefined,
			});
		},
	);
});

describe("remoteEmbeddingsFromForm", () => {
	const PROVIDER = {
		id: "acme",
		name: "Acme",
		base_url: "https://api.acme.test/v1",
	};
	// the fieldset the provider was added with, which the chosen model is named after
	const ADDED = [{ prefix: "acme_", provider: PROVIDER }];

	test("is nothing when the embeddings are local", () => {
		expect(
			remoteEmbeddingsFromForm(form({ embeddings_source: "local" }), ADDED),
		).toBeUndefined();
		expect(remoteEmbeddingsFromForm(form({}), ADDED)).toBeUndefined();
	});

	test("names the provider's model, with defaults for what wasn't given", () => {
		expect(
			remoteEmbeddingsFromForm(
				form({
					embeddings_source: "provider",
					remote_embeddings_model: "acme_:text-embed-3",
					embeddings_dimensions: "",
					embeddings_chunk_size: "",
					embeddings_batch_size: "",
				}),
				ADDED,
			),
		).toEqual({
			model: "acme/text-embed-3",
			settings: { chunk_size: 512, tokenizer: "estimate", batch_size: 64 },
		});
	});

	test("reads everything the admin gave", () => {
		expect(
			remoteEmbeddingsFromForm(
				form({
					embeddings_source: "provider",
					remote_embeddings_model: "acme_:text-embed-3",
					embeddings_dimensions: "256",
					embeddings_chunk_size: "1024",
					embeddings_tokenizer: "tiktoken:cl100k_base",
					embeddings_batch_size: "16",
				}),
				ADDED,
			),
		).toEqual({
			model: "acme/text-embed-3",
			dimensions: 256,
			settings: {
				chunk_size: 1024,
				tokenizer: "tiktoken:cl100k_base",
				batch_size: 16,
			},
		});
	});

	test("names a Hugging Face tokenizer by its repository", () => {
		const embeddings = remoteEmbeddingsFromForm(
			form({
				embeddings_source: "provider",
				remote_embeddings_model: "acme_:bge-m3",
				embeddings_tokenizer: "hf",
				embeddings_tokenizer_repo: "BAAI/bge-m3",
			}),
			ADDED,
		);
		expect(embeddings?.settings.tokenizer).toBe("hf:BAAI/bge-m3");
	});

	test("names the model after the provider it was chosen from", () => {
		const embeddings = remoteEmbeddingsFromForm(
			form({
				embeddings_source: "provider",
				remote_embeddings_model: "other_:vendor/embed:v2",
			}),
			[
				...ADDED,
				{
					prefix: "other_",
					provider: { ...PROVIDER, id: "other", name: "Other" },
				},
			],
		);
		// split at the first colon, a model id can have more
		expect(embeddings?.model).toBe("other/vendor/embed:v2");
	});

	test.each([
		[{}, "Add a model provider"],
		[{ remote_embeddings_model: "" }, "choose its embeddings model"],
		[{ remote_embeddings_model: "acme_:" }, "choose its embeddings model"],
		// the fieldset it was chosen from was emptied or removed
		[
			{ remote_embeddings_model: "gone_:text-embed-3" },
			"choose its embeddings model",
		],
		[{ embeddings_tokenizer: "sentencepiece" }, "not a tokenizer"],
		[{ embeddings_tokenizer: "hf" }, "as owner/name"],
		[
			{ embeddings_tokenizer: "hf", embeddings_tokenizer_repo: "../x" },
			"as owner/name",
		],
		[{ embeddings_chunk_size: "0" }, "Chunk size"],
		[{ embeddings_batch_size: "1.5" }, "Batch size"],
		[{ embeddings_dimensions: "many" }, "Dimensions"],
	])("refuses %p", (fields, message) => {
		expect(() =>
			remoteEmbeddingsFromForm(
				form({
					embeddings_source: "provider",
					remote_embeddings_model: "acme_:text-embed-3",
					...fields,
				}),
				Object.keys(fields).length ? ADDED : [],
			),
		).toThrow(message);
	});
});
