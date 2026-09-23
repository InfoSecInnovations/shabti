import _ from "lodash";
import getModelsConfig from "../getModelsConfig";
import { getConnectivity } from "./connectivity";
import listDownloadedModels from "./listDownloadedModels";

// the catalogue entries that can be used right now: all of them when Hugging Face is reachable,
// only the downloaded ones otherwise
export default async () => {
	const models = await getModelsConfig();
	const connectivity = await getConnectivity();
	if (connectivity == "online") return { models, connectivity };
	// this is rendering a page, which a failed probe mustn't stop
	const downloaded = await listDownloadedModels().catch((error) => {
		console.error("Couldn't list the downloaded models:", error);
		return new Set<string>();
	});
	return {
		models: _.pickBy(models, (model) => downloaded.has(model.hf)),
		connectivity,
	};
};
