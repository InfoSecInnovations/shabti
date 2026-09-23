import { afterEach, describe, expect, mock, test } from "bun:test";
import { getConnectivity, resetConnectivity } from "../server/connectivity";
import { mockFetch, reachableHosts } from "./mocks";

afterEach(() => {
	mock.restore();
	resetConnectivity();
});

describe("getConnectivity", () => {
	test("is online when Hugging Face answers", async () => {
		mockFetch(reachableHosts("https://huggingface.co"));
		expect(await getConnectivity()).toBe("online");
	});

	test("counts an error status as reachable", async () => {
		mockFetch((url) =>
			url.startsWith("https://huggingface.co")
				? new Response(null, { status: 503 })
				: undefined,
		);
		expect(await getConnectivity()).toBe("online");
	});

	test("tells Hugging Face being down apart from being offline", async () => {
		mockFetch(reachableHosts("https://api.github.com"));
		expect(await getConnectivity()).toBe("huggingface-unreachable");
	});

	test("accepts Docker Hub as proof of a connection", async () => {
		mockFetch(reachableHosts("https://registry-1.docker.io"));
		expect(await getConnectivity()).toBe("huggingface-unreachable");
	});

	test("is offline when nothing answers", async () => {
		mockFetch(() => undefined);
		expect(await getConnectivity()).toBe("offline");
	});

	test("shares one check between callers", async () => {
		const fetch = mockFetch(reachableHosts("https://huggingface.co"));
		await Promise.all([getConnectivity(), getConnectivity()]);
		await getConnectivity();
		expect(fetch).toHaveBeenCalledTimes(3); // one request per host, once
	});
});
