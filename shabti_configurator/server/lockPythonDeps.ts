import { runCompose } from "./composeProgress";

// the lockfiles reference paths that only exist inside the containers, so uv has to run in one.
// The default base directory suits both the configurator and the test scripts as they both run one
// level below the repository root
export default (baseDir = "..") =>
	runCompose(`${baseDir}/docker_containers/docker-compose-uv-lock.yml`, [
		"run",
		"--rm",
		"uv-lock",
	]);
