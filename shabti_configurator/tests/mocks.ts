import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { spyOn } from "bun:test";
import * as docker from "../server/docker";
import type { ComposeService } from "../server/docker";

/** what the mocked Docker commands answer with, and what they were asked to do */
export const dockerState = () => ({
	pullSucceeds: true,
	buildSucceeds: true,
	images: new Set<string>(),
	volumes: new Set<string>(),
	services: {} as Record<string, ComposeService>,
	started: [] as string[],
	removed: [] as string[],
});

/** replaces each Docker command with an answer from `state`, undone by mock.restore */
export const mockDocker = (state: ReturnType<typeof dockerState>) => {
	spyOn(docker, "imageExists").mockImplementation(async (image) =>
		state.images.has(image),
	);
	spyOn(docker, "volumeExists").mockImplementation(async (volume) =>
		state.volumes.has(volume),
	);
	spyOn(docker, "composePull").mockImplementation(
		async () => state.pullSucceeds,
	);
	spyOn(docker, "composeBuild").mockImplementation(async () => {
		// shaped like Bun's ShellError, which is all describeError looks at
		if (!state.buildSucceeds)
			throw { exitCode: 1, stderr: Buffer.from("failed to solve") };
	});
	spyOn(docker, "composeServices").mockImplementation(
		async () => state.services,
	);
	spyOn(docker, "startModelProbe").mockImplementation((async (
		_image: string,
		name: string,
	) => {
		state.started.push(name);
	}) as any);
	spyOn(docker, "removeContainer").mockImplementation((async (name: string) => {
		state.removed.push(name);
	}) as any);
};

/** answers each request with the handler's response, or fails it like an unreachable host */
export const mockFetch = (handler: (url: string) => Response | undefined) =>
	spyOn(globalThis, "fetch").mockImplementation((async (
		input: string | URL | Request,
	) => {
		const res = handler(input instanceof Request ? input.url : String(input));
		if (!res) throw new TypeError("fetch failed");
		return res;
	}) as unknown as typeof fetch);

export const json = (body: unknown) => Response.json(body);

/** requests to these hosts get an answer, which is all the connectivity check looks at */
export const reachableHosts =
	(...hosts: string[]) =>
	(url: string) =>
		hosts.some((host) => url.startsWith(host))
			? new Response(null, { status: 200 })
			: undefined;

/** the files the configurator keeps relative to the working directory go somewhere disposable */
export const useTempCwd = () => {
	const original = process.cwd();
	let dir: string | undefined;
	return {
		enter: async () => {
			dir = await mkdtemp(path.join(tmpdir(), "shabti-configurator-"));
			process.chdir(dir);
			return dir;
		},
		leave: async () => {
			process.chdir(original);
			if (dir) await rm(dir, { recursive: true, force: true });
			dir = undefined;
		},
	};
};

export const HF = {
	mistral7b: "bartowski/Mistral-7B-Instruct-v0.3-GGUF:Q4_K_M",
	qwen: "madhusudhan001/qwen2.5-0.5b-materials-science:Q8_0",
	snowflake: "Snowflake/snowflake-arctic-embed-m-v1.5:Q8_0",
	paraphrase: "mykor/paraphrase-multilingual-mpnet-base-v2.gguf:Q4_K_M",
};

/** a llama.cpp router's /models, with the given ids in its cache and a preset that isn't */
export const routerModels = (...cachedIds: string[]) =>
	json({
		data: [
			...cachedIds.map((id) => ({ id, tags: [], source: "cache" })),
			{ id: "mistral7b", tags: ["chat", "default"], source: "preset" },
		],
	});
