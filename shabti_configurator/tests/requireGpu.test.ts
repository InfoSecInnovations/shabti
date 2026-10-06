import {
	afterEach,
	beforeEach,
	describe,
	expect,
	mock,
	spyOn,
	test,
} from "bun:test";
import { GpuUnavailableError } from "../server/errors";
import requireGpu from "../server/requireGpu";
import { dockerState, mockDocker } from "./mocks";

const LLAMA_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda";
// what Docker on Windows without the WSL2 backend reports
const NO_GPU =
	"nvidia-container-cli: initialization error: load library failed: libnvidia-ml.so.1: cannot open shared object file: no such file or directory";
// what the NVIDIA hook reports when its ldconfig goes over its CPU time limit on a busy machine
const LDCACHE_KILLED =
	"nvidia-container-cli: ldcache error: process /sbin/ldconfig terminated with signal 9";

let docker: ReturnType<typeof dockerState>;

beforeEach(() => {
	docker = dockerState();
	docker.services = { "llama-cpp": { image: LLAMA_IMAGE } };
	mockDocker(docker);
	spyOn(Bun, "sleep").mockResolvedValue(undefined);
	spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
	mock.restore();
});

describe("requireGpu", () => {
	test("doesn't check the GPU without GPU acceleration", async () => {
		docker.gpuErrors = [NO_GPU, NO_GPU, NO_GPU];
		await requireGpu({ SHABTI_COMPUTE: "cpu" });
		expect(docker.gpuProbes).toEqual([]);
	});

	test("checks with the llama.cpp service's image", async () => {
		await requireGpu({ SHABTI_COMPUTE: "cuda" });
		expect(docker.gpuProbes).toEqual([LLAMA_IMAGE]);
	});

	test("gets past a failure that doesn't last", async () => {
		docker.gpuErrors = [LDCACHE_KILLED];
		await requireGpu({ SHABTI_COMPUTE: "cuda" });
		expect(docker.gpuProbes).toEqual([LLAMA_IMAGE, LLAMA_IMAGE]);
	});

	// e.g. Docker Desktop's VMM or Hyper-V backends, or Linux without the NVIDIA container toolkit
	test("fails with Docker's error when it can't access the GPU", async () => {
		docker.gpuErrors = [NO_GPU, NO_GPU, NO_GPU];
		const error = await requireGpu({ SHABTI_COMPUTE: "cuda" }).catch(
			(error) => error,
		);
		expect(error).toBeInstanceOf(GpuUnavailableError);
		expect(error.message).toContain(NO_GPU);
		expect(docker.gpuProbes).toHaveLength(3);
	});

	test("fails when there's no llama.cpp service to check with", async () => {
		docker.services = {};
		await expect(requireGpu({ SHABTI_COMPUTE: "cuda" })).rejects.toBeInstanceOf(
			GpuUnavailableError,
		);
		expect(docker.gpuProbes).toEqual([]);
	});
});
