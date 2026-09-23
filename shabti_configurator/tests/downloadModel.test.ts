import { afterEach, describe, expect, mock, spyOn, test } from "bun:test";
import * as eventsourceClient from "eventsource-client";
import downloadModel from "../server/downloadModel";
import { LlamaCppUnavailableError, ModelDownloadError } from "../server/errors";

// a model which is really in shabti_models.ini, so getModelsConfig resolves it the way it would
// in an install
const MODEL = "mistral7b";
const HF = "bartowski/Mistral-7B-Instruct-v0.3-GGUF:Q4_K_M";

const event = (name: string, data?: unknown) => ({
	data: JSON.stringify({ model: HF, event: name, data }),
});

const progress = (done: number, total: number) =>
	event("download_progress", { progress: { "model.gguf": { done, total } } });

/** stands in for the SSE client, and records that the generator closed it */
const eventSource = (events: { data: string }[]) => {
	const source = {
		closed: false,
		close: () => {
			source.closed = true;
		},
		async *[Symbol.asyncIterator]() {
			yield* events;
		},
	};
	return source;
};

/** health checks answer `unhealthy` 503s first, then 200 forever */
const stub = (options: {
	unhealthy?: number;
	models?: { status: number; body?: unknown };
	events?: { data: string }[];
}) => {
	const source = eventSource(options.events || []);
	let healthChecks = 0;
	const calls = { health: 0, models: 0, eventSources: 0 };
	spyOn(Bun, "sleep").mockResolvedValue(undefined);
	spyOn(globalThis, "fetch").mockImplementation((async (
		_url: string,
		init?: { method?: string },
	) => {
		if (init?.method != "POST") {
			calls.health++;
			healthChecks++;
			return new Response(null, {
				status: healthChecks <= (options.unhealthy || 0) ? 503 : 200,
			});
		}
		calls.models++;
		const models = options.models || { status: 200 };
		return Response.json(models.body ?? null, { status: models.status });
	}) as unknown as typeof fetch);
	spyOn(eventsourceClient, "createEventSource").mockImplementation((() => {
		calls.eventSources++;
		return source;
	}) as any);
	return { source, calls };
};

const drain = async (
	generator: AsyncGenerator<{ progress: number; total: number }>,
) => {
	const yielded = [];
	for await (const item of generator) yielded.push(item);
	return yielded;
};

afterEach(() => {
	mock.restore();
});

describe("downloadModel", () => {
	test("fails when the language model service never comes online", async () => {
		const { calls } = stub({ unhealthy: Number.POSITIVE_INFINITY });
		await expect(drain(downloadModel(MODEL))).rejects.toBeInstanceOf(
			LlamaCppUnavailableError,
		);
		expect(calls.health).toBe(120); // bounded rather than falling through as it used to
	});

	test("does nothing when the model is already downloaded", async () => {
		const { calls } = stub({
			models: {
				status: 400,
				body: { error: { message: "model already exists" } },
			},
		});
		expect(await drain(downloadModel(MODEL))).toEqual([]);
		expect(calls.eventSources).toBe(0);
	});

	test("reports progress and finishes cleanly", async () => {
		const { source } = stub({
			events: [progress(1, 2), event("download_finished")],
		});
		expect(await drain(downloadModel(MODEL))).toEqual([
			{
				progress: 1,
				total: 2,
				modelName: MODEL,
				status: "downloading",
				file: "model.gguf",
			},
		]);
		expect(source.closed).toBe(true);
	});

	// this used to break out of the loop and let the install go on to say it succeeded
	test("fails when the service reports the download failed", async () => {
		const { source } = stub({
			events: [progress(1, 2), event("download_failed", "out of disk space")],
		});
		await expect(drain(downloadModel(MODEL))).rejects.toThrow(
			/out of disk space/,
		);
		expect(source.closed).toBe(true);
	});

	// so did losing the connection before either terminal event arrived
	test("fails when the service stops reporting before the download finishes", async () => {
		const { source } = stub({ events: [progress(1, 2)] });
		await expect(drain(downloadModel(MODEL))).rejects.toBeInstanceOf(
			ModelDownloadError,
		);
		expect(source.closed).toBe(true);
	});
});
