import path from "node:path";
import { composeServices, gpuAvailable } from "./docker";
import { GpuUnavailableError } from "./errors";

const loaderComposeFile = path.join(
	"docker_compose",
	"docker-compose-download-model.yml",
);

/** checks Docker can actually run the CUDA service before anything relies on it */
export default async function requireGpu(envs: Record<string, string>) {
	if (envs.SHABTI_COMPUTE != "cuda") return;
	const image = (await composeServices(loaderComposeFile, envs))["llama-cpp"]
		?.image;
	if (!image || !(await gpuAvailable(image))) throw new GpuUnavailableError();
}
