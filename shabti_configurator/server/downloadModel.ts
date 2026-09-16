import { HTTPException } from "hono/http-exception";
import { sleep } from "bun";
import getModelsConfig from "../getModelsConfig";
import { createEventSource } from "eventsource-client";
import { LlamaCppUnavailableError, ModelDownloadError } from "./errors";

// the health check is bounded the same way keycloakAdminClient's retries are, so a service which
// never comes up fails with a message instead of falling through as though it had. A cold llama.cpp
// container can take a while, hence a budget rather than the 10 seconds this used to allow
const DEFAULTS = {
	fetch: globalThis.fetch,
	createEventSource,
	sleep,
	healthAttempts: 120,
	healthDelayMs: 1000,
};

export default async function* (
	modelName: string,
	deps: Partial<typeof DEFAULTS> = {},
) {
	const { fetch, createEventSource, sleep, healthAttempts, healthDelayMs } = {
		...DEFAULTS,
		...deps,
	};
	const shabtiModels = await getModelsConfig();
	const modelData = shabtiModels[modelName];
	if (!modelData) throw new HTTPException(404, { message: "model not found" });
	let ready = false;
	for (let attempt = 0; attempt < healthAttempts; attempt++) {
		try {
			if (
				await fetch("http://localhost:11434/v1/health").then(
					(res) => res.status == 200,
				)
			) {
				ready = true;
				break;
			}
		} catch {} // the service isn't listening yet, which is what the retries are for
		await sleep(healthDelayMs);
	}
	if (!ready)
		throw new LlamaCppUnavailableError((healthAttempts * healthDelayMs) / 1000);
	console.log(`loading ${modelData.hf}`);
	// this must be the actual repo instead of modelName
	// unfortunately llama.cpp won't show progress when pulling a model saved in the ini file?
	const res = await fetch("http://localhost:11434/models", {
		body: JSON.stringify({ model: modelData.hf }),
		method: "POST",
	});
	if (res.status != 200) {
		const json = (await res.json()) as any;
		// if the model already exists we don't need to download it again
		if (res.status == 400 && json.error?.message?.includes("already exists"))
			return;
		console.log(json);
		throw new ModelDownloadError(
			modelName,
			json.error?.error || json.error?.message,
		);
	}
	const eventSource = createEventSource("http://localhost:11434/models/sse");
	let finished = false;
	try {
		for await (const { data } of eventSource) {
			const jsonData = JSON.parse(data) as { [key: string]: any };
			if (jsonData.model != modelData.hf) continue;
			if (jsonData.event == "download_progress") {
				for (const [k, v] of Object.entries(
					jsonData.data.progress as { [key: string]: any },
				)) {
					yield {
						progress: v.done,
						total: v.total,
						modelName,
						status: "downloading",
						file: k,
					};
				}
			}
			if (jsonData.event == "download_failed")
				throw new ModelDownloadError(
					modelName,
					typeof jsonData.data == "string"
						? jsonData.data
						: jsonData.data?.error || jsonData.data?.message,
				);
			if (jsonData.event == "download_finished") {
				finished = true;
				break;
			}
		}
	} finally {
		eventSource.close(); // the stream was only closed on the happy path before
	}
	// the stream ending early is as much a failure as an explicit download_failed, and letting it
	// through is what made a failed download report a successful install
	if (!finished)
		throw new ModelDownloadError(
			modelName,
			"the language model service closed the connection before the download finished",
		);
}
