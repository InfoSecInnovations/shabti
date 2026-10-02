import * as humanize from "ts-humanize";
import type { PullEvent } from "./docker";
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
	sentAt: number;
};

/**
 * Each image gets its own bar, which moves on in place as the layers download. Failures get a
 * message as well, since the pull carries on without them.
 *
 * Returns whatever the pull returned.
 */
export default async function* (
	events: AsyncGenerator<PullEvent, boolean>,
): AsyncGenerator<OperationUpdate, boolean> {
	const images = new Map<string, Image>();
	const image = (id: string) => {
		let found = images.get(id);
		if (!found) {
			found = { name: id.replace(/^Image /, ""), layers: new Map(), sentAt: 0 };
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
	while (true) {
		const next = await events.next();
		if (next.done) return next.value;
		const event = next.value;
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
		// a service that gets built rather than pulled
		if (event.text == "Skipped") continue;
		const pulled = image(event.id);
		if (event.status == "Done") {
			console.log(`pulled ${pulled.name}`);
			const update = progress(pulled, "done");
			// an image that was already up to date has nothing to show, but it's still finished
			yield update.total
				? { ...update, done: update.total }
				: { ...update, done: 1, total: 1 };
		} else if (event.status == "Error" || event.status == "Warning") {
			yield progress(pulled, "failed");
			yield logMessage(
				`couldn't pull ${pulled.name}${event.details ? `: ${event.details}` : ""}`,
			);
		} else yield progress(pulled);
	}
}
