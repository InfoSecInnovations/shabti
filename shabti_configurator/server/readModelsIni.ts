import * as ini from "@std/ini";
import getModelsConfig from "../getModelsConfig";
import type { ModelSelection } from "../getDefaultModelSelection";
import { getModelsIniPath } from "./writeModelsIni";

// reads back the selection written by writeModelsIni so we can display and edit the current
// configuration, returns undefined if there's nothing usable in there so callers can fall
// back to getDefaultModelSelection
export default async (
	baseDir?: string,
): Promise<ModelSelection | undefined> => {
	const modelsIniFile = Bun.file(getModelsIniPath(baseDir));
	if (!(await modelsIniFile.exists())) return undefined;
	let sections: Record<string, any>;
	try {
		sections = ini.parse(await modelsIniFile.text()) as Record<string, any>;
	} catch {
		return undefined; // a corrupt file shouldn't stop the page from rendering
	}
	const shabtiModels = await getModelsConfig();
	const tags = Object.entries(sections).reduce(
		(acc, [key, value]) => {
			// writeModelsIni looks every model up in the catalogue, so anything which isn't in
			// there can't be fed back into it. a section renamed in the catalogue still carries
			// the same hf, so matching on that keeps an existing install's selection
			const model = shabtiModels[key]
				? key
				: Object.keys(shabtiModels).find(
						(k) => value?.hf && shabtiModels[k].hf == value.hf,
					);
			if (!model) return acc;
			acc[model] = String(value?.tags || "")
				.split(",")
				.map((tag) => tag.trim());
			return acc;
		},
		{} as { [key: string]: string[] },
	);
	const entries = Object.entries(tags);
	const chatModels = entries
		.filter(([_, v]) => v.includes("chat"))
		.map(([k]) => k);
	if (!chatModels.length) return undefined;
	const embeddingsModel = entries.find(([_, v]) =>
		v.includes("embeddings"),
	)?.[0];
	if (!embeddingsModel) return undefined;
	return {
		chatModels,
		embeddingsModel,
		defaultModel:
			chatModels.find((k) => tags[k].includes("default")) || chatModels[0]!,
	};
};
