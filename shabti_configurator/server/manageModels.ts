import getDefaultModelSelection from "../getDefaultModelSelection";
import { requireModels } from "./listDownloadedModels";
import logMessage from "./logMessage";
import modelDownloadProgress from "./modelDownloadProgress";
import readModelsIni from "./readModelsIni";
import { startLlamaCpp, stopLlamaCpp } from "./restartLlamaCpp";
import writeModelsIni from "./writeModelsIni";

// updates the chat models available to an existing installation without going through a
// full reinstall
export default async function* (options: FormData) {
	// no chat model is a valid choice, it leaves Shabti only able to search
	const chatModels = options.getAll("language_model").map((v) => v.toString());
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
			: chatModels[0];
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
	yield logMessage(
		defaultModel
			? `the default chat model will be ${defaultModel}.`
			: "no chat model is selected, Shabti will only be able to search.",
	);
	yield logMessage(
		"stopping the LLM service so the model configuration can be updated...",
	);
	await stopLlamaCpp();
	await writeModelsIni({ chatModels, embeddingsModel, defaultModel });
	yield logMessage("relaunching the LLM service...");
	yield* startLlamaCpp();
	// we run this over every selected model rather than just the new ones because it's cheap,
	// downloadModel returns straight away if Llama.cpp already has the model, and it repairs
	// the case where a model is in the ini file but was never successfully downloaded.
	// offline, requireModels has already confirmed they're all downloaded
	if (online) {
		for (const modelName of [...chatModels, embeddingsModel])
			yield* modelDownloadProgress(modelName);
	}
	if (removed.length)
		yield logMessage(
			"the removed models are no longer available to Shabti, but they are still downloaded so you can add them back without waiting for a download.",
		);
	console.log("Model configuration updated\n");
}
