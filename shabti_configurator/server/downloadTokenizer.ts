import path from "node:path";
import { mkdir, writeFile } from "node:fs/promises";
import { HTTPException } from "hono/http-exception";
import { getLlamaModelsDir } from "./writeModelsIni";

const TIMEOUT_MS = 30_000;

/**
 * Where the API reads a Hugging Face tokenizer from, next to the model settings mounted into it.
 * The repository's slash can't be in a file name, so it becomes a double dash.
 */
export const getTokenizerPath = (repo: string, baseDir = path.resolve()) =>
	path.join(
		getLlamaModelsDir(baseDir),
		"tokenizers",
		`${repo.replace("/", "--")}.json`,
	);

/**
 * A provider's embeddings model, counted with the tokenizer its weights were published with. Only
 * fetched here: an install fetches it before the existing installation is torn down, so a
 * repository that can't be reached leaves that installation as it was.
 */
export const fetchTokenizer = async (repo: string) => {
	const url = `https://huggingface.co/${repo}/resolve/main/tokenizer.json`;
	let response: Response;
	try {
		response = await fetch(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
	} catch {
		throw new HTTPException(400, {
			message: `Couldn't download the tokenizer from ${url}.`,
		});
	}
	if (response.status != 200)
		throw new HTTPException(400, {
			message: `Couldn't download the tokenizer from ${url}, it answered with status ${response.status}. The repository may be private or have no tokenizer.json.`,
		});
	return response.text();
};

export const writeTokenizer = async (
	repo: string,
	contents: string,
	baseDir = path.resolve(),
) => {
	const tokenizerPath = getTokenizerPath(repo, baseDir);
	await mkdir(path.dirname(tokenizerPath), { recursive: true });
	await writeFile(tokenizerPath, contents);
	return tokenizerPath;
};
