import path from "node:path";
import { composeServices, gpuError } from "./docker";
import { GpuUnavailableError } from "./errors";

// the NVIDIA hook can fail for reasons other than a missing GPU, such as its ldconfig being killed
// for going over its CPU time limit while Docker is busy, so a failure is retried before we give up
const GPU_ATTEMPTS = 3;
const GPU_DELAY_MS = 5000;

const loaderComposeFile = path.join(
	"docker_compose",
	"docker-compose-download-model.yml",
);

/** checks Docker can actually run the CUDA service before anything relies on it */
export default async function requireGpu(envs: Record<string, string>) {
	if (envs.SHABTI_COMPUTE != "cuda") return;
	const image = (await composeServices(loaderComposeFile, envs))["llama-cpp"]
		?.image;
	if (!image)
		throw new GpuUnavailableError("the language model service has no image");
	let error: string | undefined;
	for (let attempt = 1; attempt <= GPU_ATTEMPTS; attempt++) {
		error = await gpuError(image);
		if (!error) return;
		console.error(`GPU check ${attempt}/${GPU_ATTEMPTS} failed: ${error}`);
		if (attempt < GPU_ATTEMPTS) await Bun.sleep(GPU_DELAY_MS);
	}
	throw new GpuUnavailableError(error!);
}
