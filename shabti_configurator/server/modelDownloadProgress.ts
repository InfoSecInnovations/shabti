import * as humanize from "ts-humanize";
import downloadModel from "./downloadModel";
import type { ProgressUpdate } from "./operationProtocol";

// each file of the model gets its own bar, which moves on in place as llama.cpp reports on it
export default async function* (
	modelName: string,
): AsyncGenerator<ProgressUpdate> {
	for await (const { progress, total, file } of downloadModel(modelName))
		yield {
			key: `${modelName}/${file}`,
			label: `file ${file} for model ${modelName}`,
			done: progress,
			total,
			detail: `loaded ${humanize.bytes(progress)} / ${humanize.bytes(total)}`,
		};
}
