import getModelsConfig from "./getModelsConfig";

export interface ModelSelection {
	chatModels: string[];
	embeddingsModel: string;
	// undefined when there are no chat models, which leaves Shabti only able to search
	defaultModel?: string;
}

// derives a usable model selection straight from the bundled catalogue,
// used by the test harness and as a fallback for anything the install form didn't set
export default async (): Promise<ModelSelection> => {
	const shabtiModels = await getModelsConfig();
	const entries = Object.entries(shabtiModels);
	const chatModels = entries
		.filter(([_, v]) => v.tags.includes("chat"))
		.map(([k]) => k);
	const embeddingsModel = entries.find(([_, v]) =>
		v.tags.includes("embeddings"),
	)?.[0];
	if (!embeddingsModel)
		throw new Error("no embeddings model is available in the models config");
	return {
		chatModels,
		embeddingsModel,
		defaultModel:
			chatModels.find((k) => shabtiModels[k].tags.includes("default")) ||
			chatModels[0],
	};
};
