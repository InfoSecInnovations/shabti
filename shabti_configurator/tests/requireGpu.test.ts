import { afterEach, beforeEach, describe, expect, mock, test } from "bun:test";
import { GpuUnavailableError } from "../server/errors";
import requireGpu from "../server/requireGpu";
import { dockerState, mockDocker } from "./mocks";

const LLAMA_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda";

let docker: ReturnType<typeof dockerState>;

beforeEach(() => {
	docker = dockerState();
	docker.services = { "llama-cpp": { image: LLAMA_IMAGE } };
	mockDocker(docker);
});

afterEach(() => {
	mock.restore();
});

describe("requireGpu", () => {
	test("doesn't check the GPU without GPU acceleration", async () => {
		docker.gpuWorks = false;
		await requireGpu({ SHABTI_COMPUTE: "cpu" });
		expect(docker.gpuProbes).toEqual([]);
	});

	test("checks with the llama.cpp service's image", async () => {
		await requireGpu({ SHABTI_COMPUTE: "cuda" });
		expect(docker.gpuProbes).toEqual([LLAMA_IMAGE]);
	});

	// e.g. Docker Desktop's VMM or Hyper-V backends, or Linux without the NVIDIA container toolkit
	test("fails when Docker can't access the GPU", async () => {
		docker.gpuWorks = false;
		await expect(requireGpu({ SHABTI_COMPUTE: "cuda" })).rejects.toBeInstanceOf(
			GpuUnavailableError,
		);
	});

	test("fails when there's no llama.cpp service to check with", async () => {
		docker.services = {};
		await expect(requireGpu({ SHABTI_COMPUTE: "cuda" })).rejects.toBeInstanceOf(
			GpuUnavailableError,
		);
		expect(docker.gpuProbes).toEqual([]);
	});
});
