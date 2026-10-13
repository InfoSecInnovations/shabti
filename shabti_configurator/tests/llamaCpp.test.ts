import { describe, expect, test } from "bun:test";
import { llamaCppEnv, needsLlamaCpp, usesLlamaCpp } from "../server/llamaCpp";

describe("needsLlamaCpp", () => {
	test("is needed for a local chat model", () => {
		expect(needsLlamaCpp(["chat"], true)).toBe(true);
	});

	test("is needed for local embeddings, even without a chat model", () => {
		expect(needsLlamaCpp([], false)).toBe(true);
	});

	test("isn't needed when every model is remote", () => {
		expect(needsLlamaCpp([], true)).toBe(false);
	});
});

describe("llamaCppEnv", () => {
	test("includes llama.cpp and tells the API where it is", () => {
		const envs = llamaCppEnv(true);
		expect(envs.LLM_HOST).toBe("llama_cpp");
		expect(usesLlamaCpp(envs)).toBe(true);
	});

	test("leaves it out, and the API without a host for it", () => {
		const envs = llamaCppEnv(false);
		expect(envs.LLM_HOST).toBe("");
		expect(usesLlamaCpp(envs)).toBe(false);
	});

	test("an environment from before it was optional includes it", () => {
		expect(usesLlamaCpp({})).toBe(true);
	});
});
