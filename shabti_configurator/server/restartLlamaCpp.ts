import path from "node:path";
import { $ } from "bun";
import { composeUp } from "./composeProgress";

// this compose file declares the same project, service and container name as the full stack,
// so bringing it up recreates exactly the container docker-compose.yml would
const loaderComposeFile = path.join(
	"docker_compose",
	"docker-compose-download-model.yml",
);

// llama.cpp only reads my-models.ini when it starts and the file is a single file bind mount,
// so it has to be down before we rewrite it. The container is restart: unless-stopped, so
// removing it is the only way to be sure it stays down while we do that.
export const stopLlamaCpp = () =>
	$`docker container rm --force llama_cpp`.nothrow();

export const startLlamaCpp = () => composeUp(loaderComposeFile);
