import path from "node:path";
import AdmZip from "adm-zip";

const zip = new AdmZip();
zip.addLocalFolder(
	path.join(import.meta.dir, "docker_compose"),
	"docker_compose",
	// the installer extracts this zip over the working directory on every launch, so bundling a
	// model or provider configuration from the machine which built it would wipe out the user's
	// own, and would ship that machine's provider keys or embeddings tokenizer. models-dev.json is
	// fetched on launch, and an old copy from the build would replace the newer one whenever the
	// fetch failed
	(filename) =>
		![
			"my-models.ini",
			"shabti-models.json",
			"shabti-providers.json",
			"models-dev.json",
		].includes(path.basename(filename)) &&
		!filename
			.split(/[\\/]/)
			.some((part) => part == "secrets" || part == "tokenizers"),
);
zip.writeZip(path.join(import.meta.dir, "assets", "docker_compose.zip"));
