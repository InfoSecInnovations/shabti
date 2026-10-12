import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import { providerId } from "../providerIds";
import { providerRoutes } from "../server/providerRoutes";
import {
	normaliseBaseUrl,
	parseHeaders,
	providerFromForm,
	remoteModels,
	resolveDefaultModel,
	testProvider,
} from "../server/providers";
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
		).toEqual({ baseUrl: "https://api.test/v1", models: ["a", "b"] });
	});

	test("sends the key and headers, and follows no redirect", async () => {
		const fetchSpy = spyOn(globalThis, "fetch").mockImplementation((async () =>
			json({ data: [] })) as unknown as typeof fetch);
		await testProvider({
			baseUrl: "https://api.test/v1",
			key: "sk-1",
			headers: "X-Title: Shabti",
		});
		const init = fetchSpy.mock.calls[0]![1]!;
		expect(init.headers).toEqual({
			"X-Title": "Shabti",
			Authorization: "Bearer sk-1",
		});
		expect(init.redirect).toBe("manual");
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
		mockFetch(() => json({ data: [{ id: "a" }] }));
		const response = await providerRoutes.request("/test-provider", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ base_url: "https://api.test/v1", key: "sk-1" }),
		});
		expect(await response.json()).toEqual({
			baseUrl: "https://api.test/v1",
			models: ["a"],
		});
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

	test("refuses a name another provider has", () => {
		expect(() =>
			providerFromForm(form(PROVIDER_FORM), ["open-router"]),
		).toThrow("There is already a model provider called Open Router.");
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
