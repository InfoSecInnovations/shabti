/**
 * Converts a HuggingFace embeddings model to GGUF with llama.cpp and uploads it to a HuggingFace
 * organization, where Shabti's llama.cpp can load it as `hf = <org>/<name>:<QUANT>`.
 *
 * bun ./gguf/convert.ts <source> [--org <org>] [--name <name>] [--quants <quants...>] [--image <image>] [--force] [--upload] [--no-xet]
 *
 * Without --upload it stops once the files in gguf/work/<model>/out have been checked, so they can be
 * looked over first. Running it again with --upload reuses them rather than converting again. Only Docker
 * is needed: the download and upload run in the uv image, the conversion in llama.cpp's full image.
 *
 * Setting up, once:
 * 1. your HuggingFace account needs the write or admin role in the organization, read cannot create repos
 * 2. create a fine-grained token at https://huggingface.co/settings/tokens, granting the organization
 *    "Write access to contents/settings of all repos", and "Read access to contents of all public gated
 *    repos you can access" for gated models, whose licence also has to be accepted on the model's page
 * 3. put HF_TOKEN=<token> and HF_ORG=<organization> in gguf/.env
 */

import { mkdir } from "node:fs/promises";
import path from "node:path";
import { Command } from "commander";
import { config } from "dotenv";
import { parseImage } from "../dependencies/docker";

const COMPOSE = path.join(
	import.meta.dir,
	"..",
	"shabti_configurator",
	"docker_compose",
	"docker_compose_dependencies",
	"docker-compose-llama-cpp.yml",
);
// keep this in step with the FROM lines in shabti_api/Dockerfile and shabti_web/Dockerfile
const UV_IMAGE = "astral/uv:0.12.18-python3.14-trixie-slim";
const HF_CLI = "huggingface_hub==2.0.0";
// what convert_hf_to_gguf.py writes directly, anything else is quantized from F16 by llama-quantize
const CONVERTIBLE = new Set(["F32", "F16", "BF16", "Q8_0"]);
// weights only other frameworks read, which would multiply the download for nothing
const EXCLUDE = ["onnx/*", "openvino/*", "*.onnx", "*.h5", "*.msgpack", "*.ot"];

const table = (value: unknown) =>
	value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/**
 * The full image of the llama.cpp build Shabti runs: the server image leaves out Python and the
 * converter, and converting with the same build is what guarantees the server can load the result.
 */
const converterImage = async () => {
	const compose = Bun.YAML.parse(await Bun.file(COMPOSE).text());
	const reference = table(
		table(table(compose).services)["llama-cpp-cpu"],
	).image;
	const image = typeof reference === "string" ? parseImage(reference) : null;
	const build = image?.tag?.match(/^server(?:-[a-z0-9]+)?-(b\d+)$/)?.[1];
	if (!image?.registry || !build)
		throw new Error(
			`cannot tell the llama.cpp build from ${reference} in ${COMPOSE}, pass --image`,
		);
	return `${image.registry}/${image.repository}:full-${build}`;
};

// on Linux the bind mount would otherwise fill with files owned by root, Docker Desktop maps ownership
// itself. HOME moves with it, since the caller has no home directory inside the images
const asCaller = [
	...(process.platform === "linux" && process.getuid && process.getgid
		? ["--user", `${process.getuid()}:${process.getgid()}`]
		: []),
	"-e",
	"HOME=/tmp",
];

/** a throwaway container with the work directory at /work, returning its output when captured */
const docker = async (workDir: string, args: string[], capture = false) => {
	const proc = Bun.spawn(
		[
			"docker",
			"run",
			"--rm",
			...asCaller,
			"--mount",
			`type=bind,source=${workDir},target=/work`,
			...args,
		],
		{
			// passed explicitly because Bun hands children the environment it started with, which leaves out
			// the HF_TOKEN that dotenv loaded afterwards
			env: process.env,
			stdout: capture ? "pipe" : "inherit",
			stderr: capture ? "pipe" : "inherit",
		},
	);
	const [stdout, stderr] = capture
		? await Promise.all([
				new Response(proc.stdout as ReadableStream).text(),
				new Response(proc.stderr as ReadableStream).text(),
			])
		: ["", ""];
	const code = await proc.exited;
	if (code) throw new Error(`${stderr}docker run exited with ${code}`);
	return stdout;
};

/** the hf CLI, given the token by name so its value never appears on a command line */
const hf = (workDir: string, args: string[], xet: boolean) =>
	docker(workDir, [
		"-e",
		"HF_TOKEN",
		// the LFS route Hugging Face keeps for older clients, sending files to its S3 storage rather than
		// to the Xet servers
		...(xet ? [] : ["-e", "HF_HUB_DISABLE_XET=1"]),
		"-e",
		"UV_CACHE_DIR=/tmp/uv-cache",
		// hf_xet logs under HOME by default, which goes with the container, and its errors only say a
		// request failed, so the logs are the one place saying how
		"-e",
		"HF_XET_LOG_DEST=/work/logs/",
		"--entrypoint",
		"uvx",
		UV_IMAGE,
		"--from",
		HF_CLI,
		"hf",
		...args,
	]);

/**
 * The size of one embedding, after checking every value in it is a number: a model which computes NaN
 * reaches OpenSearch as a vector of nulls, and llama-embedding prints NaN as `nan`, which is not JSON.
 */
const embeddingSize = async (workDir: string, image: string, file: string) => {
	const stdout = await docker(
		workDir,
		[
			"--entrypoint",
			"/app/llama-embedding",
			image,
			"-m",
			`/work/out/${file}`,
			"-p",
			"hello world",
			"--embd-output-format",
			"array",
		],
		true,
	);
	let vector: unknown;
	try {
		vector = JSON.parse(
			stdout.slice(stdout.indexOf("["), stdout.lastIndexOf("]") + 1),
		)[0];
	} catch {}
	if (
		!Array.isArray(vector) ||
		!vector.length ||
		!vector.every(Number.isFinite)
	)
		throw new Error(`${file} does not produce a usable embedding:\n${stdout}`);
	return vector.length;
};

/** the metadata block at the top of a model card, or nothing when there is no card */
const frontMatter = async (file: string) => {
	const card = Bun.file(file);
	if (!(await card.exists())) return {};
	const match = (await card.text()).match(/^---\r?\n([\s\S]*?)\r?\n---/);
	return match ? table(Bun.YAML.parse(match[1] as string)) : {};
};

const iniSection = (model: string, repoId: string, quant: string) =>
	`[${model}]\nhf = ${repoId}:${quant}\ntags = embeddings`;

const modelCard = async ({
	source,
	sourceDir,
	model,
	repoId,
	build,
	files,
	size,
}: {
	source: string;
	sourceDir: string;
	model: string;
	repoId: string;
	build: string;
	files: { file: string; quant: string }[];
	size: number;
}) => {
	const original = await frontMatter(path.join(sourceDir, "README.md"));
	// JSON is valid YAML, and quotes whatever needs quoting
	const metadata = [
		...["license", "license_name", "license_link", "language"].flatMap((key) =>
			original[key] === undefined
				? []
				: [`${key}: ${JSON.stringify(original[key])}`],
		),
		`base_model: ${source}`,
		"base_model_relation: quantized",
		"library_name: gguf",
		`pipeline_tag: ${JSON.stringify(original.pipeline_tag ?? "feature-extraction")}`,
		"tags:\n- gguf\n- llama.cpp\n- embeddings",
	];
	const quant = (files[0] as { quant: string }).quant;
	return `---
${metadata.join("\n")}
---

# ${model} GGUF

[${source}](https://huggingface.co/${source}) converted to GGUF with llama.cpp ${build}. Embeddings have ${size} dimensions.

| File | Type |
| --- | --- |
${files.map(({ file, quant }) => `| ${file} | ${quant} |`).join("\n")}

## Usage

With llama.cpp:

\`\`\`sh
llama-server -hf ${repoId}:${quant} --embeddings
\`\`\`

With Shabti, in \`custom_models.ini\`:

\`\`\`ini
${iniSection(model, repoId, quant)}
\`\`\`
`;
};

export const convert = async ({
	source,
	org,
	name,
	quants,
	image,
	force,
	upload,
	xet = true,
}: {
	source: string;
	org: string;
	name?: string;
	quants: string[];
	image?: string;
	force?: boolean;
	upload?: boolean;
	xet?: boolean;
}) => {
	const model = source.split("/")[1];
	if (!model || source.split("/").length !== 2)
		throw new Error(`${source} is not a HuggingFace repo id like owner/name`);
	if (upload && !process.env.HF_TOKEN)
		throw new Error(
			"uploading needs HF_TOKEN, in gguf/.env or the environment",
		);

	const repoId = `${org}/${name ?? `${model}-GGUF`}`;
	const converter = image ?? (await converterImage());
	const build = converter.match(/-(b\d+)$/)?.[1] ?? converter;
	const workDir = path.join(import.meta.dir, "work", model);
	const outDir = path.join(workDir, "out");
	await mkdir(outDir, { recursive: true });

	console.log(`downloading ${source}`);
	// hf download skips what is already there, so this only fetches anything missing or changed
	await hf(
		workDir,
		[
			"download",
			source,
			"--local-dir",
			"/work/source",
			...EXCLUDE.flatMap((pattern) => ["--exclude", pattern]),
		],
		xet,
	);

	const fileOf = (quant: string) => `${model}-${quant}.gguf`;
	// llama-quantize works from F16, which is made first but only uploaded when it was asked for
	const needsF16 = quants.some((quant) => !CONVERTIBLE.has(quant));
	for (const quant of [...new Set(needsF16 ? ["F16", ...quants] : quants)]) {
		const file = fileOf(quant);
		if (!force && (await Bun.file(path.join(outDir, file)).exists())) {
			console.log(`keeping ${file}, pass --force to convert again`);
			continue;
		}
		console.log(`writing ${file}`);
		await docker(
			workDir,
			CONVERTIBLE.has(quant)
				? [
						converter,
						"--convert",
						"/work/source",
						"--outtype",
						quant.toLowerCase(),
						"--outfile",
						`/work/out/${file}`,
					]
				: [
						converter,
						"--quantize",
						`/work/out/${fileOf("F16")}`,
						`/work/out/${file}`,
						quant,
					],
		);
	}

	const files = quants.map((quant) => ({ file: fileOf(quant), quant }));
	let size = 0;
	for (const { file } of files) {
		size = await embeddingSize(workDir, converter, file);
		console.log(`checked ${file}: ${size} dimensions`);
	}

	await Bun.write(
		path.join(outDir, "README.md"),
		await modelCard({
			source,
			sourceDir: path.join(workDir, "source"),
			model,
			repoId,
			build,
			files,
			size,
		}),
	);

	if (!upload) {
		console.log(
			`\nfiles are in ${outDir}\nrun again with --upload to publish them to https://huggingface.co/${repoId}`,
		);
		return;
	}

	console.log(`uploading to ${repoId}`);
	// the repo is created, public, when it does not exist yet
	await hf(
		workDir,
		[
			"upload",
			repoId,
			"/work/out",
			".",
			"--repo-type",
			"model",
			"--commit-message",
			`${quants.join(", ")} from llama.cpp ${build}`,
			...["README.md", ...files.map(({ file }) => file)].flatMap((file) => [
				"--include",
				file,
			]),
		],
		xet,
	);
	console.log(
		`\nhttps://huggingface.co/${repoId}\n\nfor custom_models.ini:\n\n${iniSection(model, repoId, quants[0] as string)}`,
	);
};

if (import.meta.main) {
	config({ path: path.join(import.meta.dir, ".env"), quiet: true });
	const command = new Command()
		.argument("<source>", "the HuggingFace model to convert, as owner/name")
		.option(
			"--org <org>",
			"the organization to upload to, defaulting to HF_ORG",
		)
		.option(
			"--name <name>",
			"the repo to upload to, defaulting to <model>-GGUF",
		)
		.option("--quants <quants...>", "the GGUF types to produce", ["Q8_0"])
		.option(
			"--image <image>",
			"the llama.cpp full image, defaulting to the build Shabti runs",
		)
		.option("--force", "convert again even when a file already exists")
		.option(
			"--upload",
			"publish the files to HuggingFace once they are checked",
		)
		.option(
			"--no-xet",
			"transfer through Hugging Face's LFS route rather than Xet",
		)
		.parse();
	const options = command.opts();
	const org = options.org ?? process.env.HF_ORG;
	if (!org) command.error("pass --org or set HF_ORG in gguf/.env");

	await convert({
		source: command.args[0] as string,
		org,
		name: options.name,
		quants: (options.quants as string[]).map((quant) => quant.toUpperCase()),
		image: options.image,
		force: options.force,
		upload: options.upload,
		xet: options.xet,
	});
}
