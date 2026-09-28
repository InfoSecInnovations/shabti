import path from "node:path";
import { file } from "bun";
import shabtiModelsFile from "./shabti_models.ini" with { type: "file" };
import * as ini from "@std/ini";

// the shipped catalogue is embedded in the executable, so this is where users add their own models
export const getCustomModelsPath = () => path.resolve("custom_models.ini");

const parseModels = (text: string) => {
	const parsed = ini.parse(text) as Record<string, any>;
	return Object.entries(parsed).reduce(
		(acc, [k, v]) => {
			acc[k] = {
				...v,
				tags: v.tags.split(",").map((tag: string) => tag.trim()),
			};
			return acc;
		},
		{} as { [key: string]: any },
	);
};

export default async () => {
	const builtIn = parseModels(await file(shabtiModelsFile).text());
	const customFile = file(getCustomModelsPath());
	const custom = (await customFile.exists())
		? parseModels(await customFile.text())
		: {};
	// a custom default takes over from the shipped one
	if (Object.values(custom).some((v) => v.tags.includes("default")))
		for (const v of Object.values(builtIn))
			v.tags = v.tags.filter((tag: string) => tag != "default");
	// a custom section replaces the shipped model of the same name entirely
	return { ...builtIn, ...custom };
};
