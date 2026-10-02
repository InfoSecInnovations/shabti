import * as humanize from "ts-humanize";
import { type BuildStatus, type ComposeOutput, compose } from "./docker";
import logMessage from "./logMessage";
import type { OperationUpdate, ProgressUpdate } from "./operationProtocol";

// compose reports on each layer many times a second, far more often than the page needs redrawing
const THROTTLE_MS = 250;

// past any of these the layer's download is over, and the current and total compose reports
// belong to the extraction
const DOWNLOADED = new Set([
	"Download complete",
	"Verifying Checksum",
	"Extracting",
	"Pull complete",
]);

type Image = {
	name: string;
	/** downloaded and total bytes of each layer, from when it reports its size */
	layers: Map<string, { done: number; total: number }>;
	finished: boolean;
	sentAt: number;
};

type Step = {
	name: string;
	started: boolean;
	completed: boolean;
	failed: boolean;
};

/**
 * Each image being pulled gets its own bar, which moves on in place as the layers download, and a
 * build gets one bar for all of its steps. Everything else compose reports goes to the terminal,
 * apart from failures, which get a message on the page as well.
 *
 * Returns whatever the command returned.
 */
export default async function* composeProgress<T>(
	outputs: AsyncGenerator<ComposeOutput, T>,
): AsyncGenerator<OperationUpdate, T> {
	const images = new Map<string, Image>();
	const image = (id: string) => {
		let found = images.get(id);
		if (!found) {
			found = {
				name: id.replace(/^Image /, ""),
				layers: new Map(),
				finished: false,
				sentAt: 0,
			};
			images.set(id, found);
		}
		return found;
	};
	const progress = (image: Image, detail?: string): ProgressUpdate => {
		let done = 0;
		let total = 0;
		for (const layer of image.layers.values()) {
			done += layer.done;
			total += layer.total;
		}
		image.sentAt = Date.now();
		return {
			key: `image/${image.name}`,
			label: `image ${image.name}`,
			done,
			total,
			detail:
				detail ??
				(!total
					? "pulling"
					: done == total
						? "extracting"
						: `downloaded ${humanize.bytes(done)} / ${humanize.bytes(total)}`),
		};
	};
	/** the last thing printed about each resource, so the terminal only hears about changes */
	const shown = new Map<string, string>();
	const steps = new Map<string, Step>();
	let currentStep = "";
	let buildSentAt = 0;
	const buildProgress = (): ProgressUpdate => {
		buildSentAt = Date.now();
		return {
			key: "build",
			label: "building local images",
			done: [...steps.values()].filter((step) => step.completed).length,
			total: steps.size,
			detail: currentStep,
		};
	};
	/** reports on a build, yielding a failed step's error */
	const build = function* (status: BuildStatus) {
		for (const vertex of status.vertexes || []) {
			let step = steps.get(vertex.digest);
			if (!step) {
				step = {
					name: vertex.name || "",
					started: false,
					completed: false,
					failed: false,
				};
				steps.set(vertex.digest, step);
			}
			if (vertex.started && !step.started) {
				step.started = true;
				currentStep = step.name;
				console.log(`${step.name}${vertex.cached ? " (cached)" : ""}`);
			}
			if (vertex.completed) step.completed = true;
			if (vertex.error && !step.failed) {
				step.failed = true;
				yield logMessage(`${step.name} failed: ${vertex.error}`);
			}
		}
		for (const log of status.logs || [])
			process.stdout.write(Buffer.from(log.data, "base64").toString());
	};
	while (true) {
		const next = await outputs.next();
		if (next.done) {
			// a download which fails partway through isn't reported, the image just never finishes
			const unfinished = [...images.values()].filter(
				(image) => !image.finished,
			);
			for (const image of unfinished) {
				yield progress(image, "failed");
				yield logMessage(`couldn't finish pulling ${image.name}`);
			}
			// Docker resumes an interrupted layer, but only so many times before it gives up on the
			// pull and deletes everything it had downloaded
			if (unfinished.length)
				yield logMessage(
					"Docker gave up after repeated interruptions. Raising max-download-attempts in Docker Desktop → Settings → Docker Engine lets it keep resuming the download.",
				);
			if (steps.size)
				yield {
					...buildProgress(),
					done: steps.size,
					detail: "done",
				};
			return next.value;
		}
		const output = next.value;
		if (output.kind == "build") {
			yield* build(output.status);
			if (Date.now() - buildSentAt >= THROTTLE_MS) yield buildProgress();
			continue;
		}
		const { event } = output;
		if (event.parent_id) {
			const parent = image(event.parent_id);
			const layer = parent.layers.get(event.id);
			// nothing to download, so it shouldn't count towards the bar
			if (event.text == "Already exists") parent.layers.delete(event.id);
			else if (event.text == "Downloading" && event.total)
				parent.layers.set(event.id, {
					done: event.current || 0,
					total: event.total,
				});
			else if (layer && DOWNLOADED.has(event.text || ""))
				layer.done = layer.total;
			if (Date.now() - parent.sentAt >= THROTTLE_MS) yield progress(parent);
			continue;
		}
		if (event.text == "Pulling") {
			yield progress(image(event.id));
			continue;
		}
		const pulled = images.get(event.id);
		if (pulled) {
			if (event.status == "Done") {
				pulled.finished = true;
				console.log(`pulled ${pulled.name}`);
				const update = progress(pulled, "done");
				// an image that was already up to date has nothing to show, but it's still finished
				yield update.total
					? { ...update, done: update.total }
					: { ...update, done: 1, total: 1 };
			} else if (event.status == "Error" || event.status == "Warning") {
				pulled.finished = true;
				yield progress(pulled, "failed");
				yield logMessage(
					`couldn't pull ${pulled.name}${event.details ? `: ${event.details}` : ""}`,
				);
			} else yield progress(pulled);
			continue;
		}
		// containers, networks, volumes, and the images that are built or skipped rather than pulled
		const line = `${event.id} ${event.text}${event.details ? `: ${event.details}` : ""}`;
		if (shown.get(event.id) == line) continue;
		shown.set(event.id, line);
		if (event.status == "Error") yield logMessage(line);
		else console.log(line);
	}
}

/** runs a compose command, with its progress on the page */
export const runCompose = (
	composeFile: string,
	args: string[],
	env?: Record<string, string>,
) => composeProgress(compose(composeFile, args, { env }));
