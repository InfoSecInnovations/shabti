import { expect, spyOn, test } from "bun:test";
import * as humanize from "ts-humanize";
import * as downloadModel from "../server/downloadModel";
import modelDownloadProgress from "../server/modelDownloadProgress";

test("gives each file of a model its own progress bar", async () => {
	const spy = spyOn(downloadModel, "default").mockImplementation(
		async function* (modelName: string) {
			for (const file of ["a.gguf", "b.gguf", "a.gguf"])
				yield {
					progress: 1024,
					total: 4096,
					modelName,
					status: "downloading",
					file,
				};
		},
	);
	try {
		const updates = await Array.fromAsync(modelDownloadProgress("MiniCPM5-2B"));
		expect(updates.map((update) => update.key)).toEqual([
			"MiniCPM5-2B/a.gguf",
			"MiniCPM5-2B/b.gguf",
			"MiniCPM5-2B/a.gguf",
		]);
		expect(updates[0]).toEqual({
			key: "MiniCPM5-2B/a.gguf",
			label: "file a.gguf for model MiniCPM5-2B",
			done: 1024,
			total: 4096,
			detail: `loaded ${humanize.bytes(1024)} / ${humanize.bytes(4096)}`,
		});
	} finally {
		spy.mockRestore();
	}
});
