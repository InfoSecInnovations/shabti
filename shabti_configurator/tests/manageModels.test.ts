import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import * as envfile from "envfile";
import getEnvPath from "../server/getEnvPath";
import * as listDownloadedModels from "../server/listDownloadedModels";
import { llamaCppEnv } from "../server/llamaCpp";
import manageModels from "../server/manageModels";
import * as manageSettings from "../server/manageSettings";
import * as modelDownloadProgress from "../server/modelDownloadProgress";
import readModelsIni from "../server/readModelsIni";
import * as restartLlamaCpp from "../server/restartLlamaCpp";
import writeModelsIni, { readShabtiModels } from "../server/writeModelsIni";
import writeProviders from "../server/writeProviders";
import { drain, useTempCwd } from "./mocks";

const CHAT = "MiniCPM5-2B";
const EMBED = "all-MiniLM-L6-v2";
const REMOTE_EMBEDDINGS = {
	model: "acme/text-embed",
	settings: { chunk_size: 512, tokenizer: "estimate", batch_size: 64 },
};

const cwd = useTempCwd();
const original = {
	LLAMA_CPP_SERVICE_FILE: process.env.LLAMA_CPP_SERVICE_FILE,
	LLM_HOST: process.env.LLM_HOST,
};

/** what the LLM service and the relaunch were asked to do */
const calls = () => ({
	stopped: 0,
	started: 0,
	downloaded: [] as string[],
	applied: [] as Record<string, string>[],
});
let seen = calls();

beforeEach(async () => {
	await cwd.enter();
	seen = calls();
	spyOn(restartLlamaCpp, "stopLlamaCpp").mockImplementation((async () => {
		seen.stopped++;
	}) as any);
	spyOn(restartLlamaCpp, "startLlamaCpp").mockImplementation(
		async function* () {
			seen.started++;
		} as any,
	);
	spyOn(modelDownloadProgress, "default").mockImplementation(async function* (
		name: string,
	) {
		seen.downloaded.push(name);
	} as any);
	spyOn(manageSettings, "applySettings").mockImplementation(async function* (
		updates: Record<string, string>,
	) {
		seen.applied.push(updates);
	} as any);
	spyOn(listDownloadedModels, "requireModels").mockResolvedValue(true);
});

afterEach(async () => {
	mock.restore();
	for (const [key, value] of Object.entries(original))
		if (value === undefined) delete process.env[key];
		else process.env[key] = value;
	await cwd.leave();
});

const install = async (options: {
	chatModels: string[];
	remote: boolean;
	envs: Record<string, string>;
}) => {
	await Bun.write(getEnvPath(), envfile.stringify(options.envs));
	await writeModelsIni(
		options.remote
			? { chatModels: options.chatModels, remoteEmbeddings: REMOTE_EMBEDDINGS }
			: { chatModels: options.chatModels, embeddingsModel: EMBED },
	);
	await writeProviders(
		{
			providers: options.remote
				? [{ id: "acme", name: "Acme", base_url: "https://api.acme.test/v1" }]
				: [],
			...(options.remote
				? { embeddings: { model: REMOTE_EMBEDDINGS.model } }
				: {}),
		},
		{},
	);
};

const form = (chatModels: string[]) => {
	const data = new FormData();
	for (const model of chatModels) data.append("language_model", model);
	return data;
};

const state = {};

describe("manageModels", () => {
	test("keeps llama.cpp for local embeddings, and downloads them", async () => {
		// an environment from before llama.cpp could be left out
		await install({ chatModels: [], remote: false, envs: {} });
		await drain(manageModels(form([CHAT]), state));
		expect(seen.applied).toEqual([]);
		expect(seen.stopped).toBe(1);
		expect(seen.started).toBe(1);
		expect(seen.downloaded).toEqual([CHAT, EMBED]);
		expect((await readModelsIni())?.chatModels).toEqual([CHAT]);
	});

	test("leaves llama.cpp out once every model is remote", async () => {
		await install({
			chatModels: [CHAT],
			remote: true,
			envs: llamaCppEnv(true),
		});
		await drain(manageModels(form([]), state));
		expect(seen.stopped).toBe(1);
		expect(seen.applied).toEqual([llamaCppEnv(false)]);
		expect(process.env.LLM_HOST).toBe("");
		expect(seen.started).toBe(0);
		expect(seen.downloaded).toEqual([]);
		// the provider's embeddings settings outlive the rewrite
		expect(await readShabtiModels()).toEqual({
			[REMOTE_EMBEDDINGS.model]: REMOTE_EMBEDDINGS.settings,
		});
	});

	test("brings llama.cpp in for a first local model", async () => {
		await install({ chatModels: [], remote: true, envs: llamaCppEnv(false) });
		await drain(manageModels(form([CHAT]), state));
		// there was no llama.cpp to stop
		expect(seen.stopped).toBe(0);
		expect(seen.applied).toEqual([llamaCppEnv(true)]);
		expect(process.env.LLM_HOST).toBe("llama_cpp");
		expect(seen.started).toBe(1);
		// the embeddings come from the provider, so there's nothing local to download for them
		expect(seen.downloaded).toEqual([CHAT]);
	});

	test("changes nothing about llama.cpp for a remote install without local models", async () => {
		await install({ chatModels: [], remote: true, envs: llamaCppEnv(false) });
		await drain(manageModels(form([]), state));
		expect(seen.stopped).toBe(0);
		expect(seen.applied).toEqual([]);
		expect(seen.started).toBe(0);
	});
});
