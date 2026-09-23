/**
 * Reading the image pins out of compose files and Dockerfiles.
 *
 * Compose files are parsed as YAML rather than scanned. That is what makes `image: !reset null` - which
 * docker-compose-dev.yml uses twice to unset an inherited image and force a local build - something we
 * can skip rather than a dependency named "null", what gives a service name to report against, and what
 * stops the word `image` inside docker-compose-uv-lock.yml's shell `command:` being read as one.
 *
 * Writing is a different matter, and deliberately never re-serialises: the compose files carry comments
 * that encode invariants, `${VAR:-default}` interpolations, and inconsistent trailing newlines, and
 * Biome does not format YAML so nothing would tidy the damage back up.
 */

import type { ImageRef, Pin } from "./types";
import { shapeOf } from "./tag";

const table = (value: unknown) =>
	value && typeof value === "object" ? (value as Record<string, unknown>) : {};

/**
 * An image reference split into its parts, or null when it names no fixed image: an interpolated tag
 * such as `${SHABTI_API_VERSION:-latest}` is decided by the environment at run time rather than here.
 */
export const parseImage = (reference: string): ImageRef | null => {
	const trimmed = reference.trim();
	if (!trimmed || trimmed.includes("$")) return null;

	const at = trimmed.indexOf("@");
	const named = at < 0 ? trimmed : trimmed.slice(0, at);
	const digest = at < 0 ? null : trimmed.slice(at + 1);

	// the tag's colon is the one after the last slash: splitting on the last breaks localhost:5000/x,
	// and splitting on the first breaks quay.io:443/x/y:1
	const colon = named.indexOf(":", named.lastIndexOf("/") + 1);
	const path = colon < 0 ? named : named.slice(0, colon);
	const tag = colon < 0 ? null : named.slice(colon + 1);
	if (!path) return null;

	const segments = path.split("/");
	const first = segments[0] as string;
	// only the FIRST segment can be a registry: ghcr.io/ggml-org/llama.cpp has a dot in the repository
	const hosted =
		segments.length > 1 &&
		(first.includes(".") || first.includes(":") || first === "localhost");

	return {
		registry: hosted ? first : null,
		repository: hosted ? segments.slice(1).join("/") : path,
		tag: tag || null,
		digest,
	};
};

/** what both commands match a docker dependency on, and what `set` takes as its name */
export const idOf = (image: ImageRef) =>
	image.registry ? `${image.registry}/${image.repository}` : image.repository;

/**
 * Exactness here is syntactic: a version was named, or it was not. `postgres:18.3` counts as exact even
 * though upstream may move that tag, because the requirement is that we write exact versions, not that
 * upstream tags be immutable. Digests are exact too, and `set` refuses to drop one silently.
 */
const pinOf = (
	file: string,
	location: string,
	line: number,
	reference: string,
	image: ImageRef,
): Pin => {
	const shape = image.tag ? shapeOf(image.tag) : null;
	return {
		name: idOf(image),
		id: idOf(image),
		ecosystem: "docker",
		file,
		line,
		specifier: image.tag ?? "",
		// the whole reference, because that is the string a rewrite has to find and replace
		raw: reference.trim(),
		version: shape?.value ?? null,
		precision: image.digest
			? "exact"
			: !image.tag
				? "absent"
				: shape
					? "exact"
					: "tag",
		extras: [],
		marker: null,
		location,
		image,
	};
};

const lineOf = (text: string, needle: string) => {
	const at = text.indexOf(needle);
	return at < 0 ? 0 : text.slice(0, at).split("\n").length;
};

/** every image a compose file names for one of its services */
export const composePins = (file: string, text: string): Pin[] => {
	let document: unknown;
	try {
		document = Bun.YAML.parse(text);
	} catch (error) {
		throw new Error(`could not parse ${file}: ${error}`);
	}
	return Object.entries(table(table(document).services)).flatMap(
		([service, definition]) => {
			const reference = table(definition).image;
			// Bun's parser drops the !reset tag and leaves `!reset null` as the string
			if (typeof reference !== "string" || reference === "null") return [];
			const image = parseImage(reference);
			if (!image) return [];
			return [
				pinOf(
					file,
					`services.${service}.image`,
					lineOf(text, reference),
					reference,
					image,
				),
			];
		},
	);
};

const FROM = /^\s*FROM\s+((?:--[\w-]+=\S+\s+)*)(\S+)/i;
const STAGE = /^\s*FROM\s+.*\sAS\s+(\S+)\s*$/i;

/** every image a Dockerfile builds from, skipping the stages it declares for itself */
export const dockerfilePins = (file: string, text: string): Pin[] => {
	const lines = text.split("\n");
	// four of the seven real FROM lines in this repo name an earlier stage rather than an image
	const stages = new Set(
		lines.flatMap((line) => {
			const name = STAGE.exec(line)?.[1];
			return name ? [name.toLowerCase()] : [];
		}),
	);

	return lines.flatMap((line, index) => {
		const reference = FROM.exec(line)?.[2];
		if (!reference) return [];
		const lowered = reference.toLowerCase();
		if (stages.has(lowered) || lowered === "scratch") return [];
		const image = parseImage(reference);
		if (!image) return [];
		return [pinOf(file, "FROM", index + 1, reference, image)];
	});
};
