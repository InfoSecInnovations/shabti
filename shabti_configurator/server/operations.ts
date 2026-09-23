import describeError from "./describeError";
import type {
	Line,
	OperationEvent,
	OperationStatus,
	OperationUpdate,
} from "./operationProtocol";

/**
 * The state of one long running form action. It's kept as the lines the page should show rather than
 * a log of events, so a page connecting late, or reconnecting, gets each line once in its latest form
 * however many times a progress bar has moved.
 */
export class Operation {
	readonly lines: Line[] = [];
	status: OperationStatus = { status: "running" };
	private readonly listeners = new Set<(event: OperationEvent) => void>();

	constructor(readonly title: string) {}

	get finished() {
		return this.status.status != "running";
	}

	/** the events which bring a page that has seen nothing up to date */
	snapshot(): OperationEvent[] {
		const events: OperationEvent[] = this.lines.map((line, index) => ({
			event: "line",
			data: { index, line },
		}));
		if (this.finished) events.push({ event: "status", data: this.status });
		return events;
	}

	subscribe(listener: (event: OperationEvent) => void) {
		this.listeners.add(listener);
		return () => {
			this.listeners.delete(listener);
		};
	}

	add(update: OperationUpdate) {
		const line: Line =
			typeof update == "string"
				? { kind: "message", text: update }
				: { kind: "progress", ...update };
		const existing =
			line.kind == "progress"
				? this.lines.findIndex(
						(other) => other.kind == "progress" && other.key == line.key,
					)
				: -1;
		const index = existing == -1 ? this.lines.length : existing;
		this.lines[index] = line;
		this.emit({ event: "line", data: { index, line } });
	}

	finish(status: OperationStatus) {
		this.status = status;
		this.emit({ event: "status", data: status });
	}

	private emit(event: OperationEvent) {
		for (const listener of this.listeners) listener(event);
	}
}

const operations = new Map<string, Operation>();

export const getOperation = (id: string) => operations.get(id);

/**
 * Runs the updates in the background, independently of whoever is watching, and returns the id to
 * watch them by. Anything thrown fails the operation, including `undefined` and values which aren't
 * Errors, and only falling off the end of the updates reports success.
 */
export const startOperation = (
	title: string,
	updates: AsyncIterable<OperationUpdate>,
	endMessage?: string,
) => {
	// nobody needs a finished operation once its page has moved on, this keeps them from piling up
	for (const [id, operation] of operations)
		if (operation.finished) operations.delete(id);
	const id = crypto.randomUUID();
	const operation = new Operation(title);
	operations.set(id, operation);
	(async () => {
		try {
			for await (const update of updates) operation.add(update);
		} catch (error) {
			operation.finish({ status: "failed", message: describeError(error) });
			return;
		}
		operation.finish({
			status: "done",
			message: endMessage || "Operation completed successfully.",
		});
	})();
	return id;
};
