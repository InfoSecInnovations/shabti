import { HTTPException } from "hono/http-exception";
import * as humanize from "ts-humanize";
import getDefaultModelSelection from "../getDefaultModelSelection";
import downloadModel from "./downloadModel";
import { requireModels } from "./listDownloadedModels";
import logMessage from "./logMessage";
import readModelsIni from "./readModelsIni";
import { startLlamaCpp, stopLlamaCpp } from "./restartLlamaCpp";
import writeModelsIni from "./writeModelsIni";

// updates the chat models available to an existing installation without going through a
// full reinstall
export default async function* (options: FormData) {
	const chatModels = options.getAll("language_model").map((v) => v.toString());
	// unlike the install form an empty selection is a mistake rather than an unset field, so
	// we don't fall back to the catalogue defaults here
	if (!chatModels.length)
		throw new HTTPException(400, {
			message: "at least one chat model must be selected",
		});
	const current = await readModelsIni();
	const embeddingsModel =
		current?.embeddingsModel ||
		(await getDefaultModelSelection()).embeddingsModel;
	const requestedDefault = options.get("default_model")?.toString();
	// the selector isn't rendered when there's only one model, and the previous default may
	// have just been deselected
	const defaultModel =
		requestedDefault && chatModels.includes(requestedDefault)
			? requestedDefault
			: chatModels[0]!;
	const added = chatModels.filter(
		(model) => !current?.chatModels.includes(model),
	);
	const removed = (current?.chatModels || []).filter(
		(model) => !chatModels.includes(model),
	);
	// before anything is stopped, so an offline change that can't work leaves things as they were
	const online = await requireModels([...chatModels, embeddingsModel]);
	if (added.length) yield logMessage(`adding models: ${added.join(", ")}`);
	if (removed.length)
		yield logMessage(`removing models: ${removed.join(", ")}`);
	yield logMessage(`the default chat model will be ${defaultModel}.`);
	yield logMessage(
		"stopping the LLM service so the model configuration can be updated...",
	);
	await stopLlamaCpp();
	await writeModelsIni({ chatModels, embeddingsModel, defaultModel });
	yield logMessage("relaunching the LLM service...");
	await startLlamaCpp();
	// we run this over every selected model rather than just the new ones because it's cheap,
	// downloadModel returns straight away if Llama.cpp already has the model, and it repairs
	// the case where a model is in the ini file but was never successfully downloaded.
	// offline, requireModels has already confirmed they're all downloaded
	if (online) {
		for (const modelName of [...chatModels, embeddingsModel]) {
			for await (const json of downloadModel(modelName)) {
				yield logMessage(
					`loaded ${humanize.bytes(json.progress)} / ${humanize.bytes(json.total)} of file ${json.file} for model ${json.modelName}`,
				);
			}
		}
	}
	if (removed.length)
		yield logMessage(
			"the removed models are no longer available to Shabti, but they are still downloaded so you can add them back without waiting for a download.",
		);
	console.log("Model configuration updated\n");
}
