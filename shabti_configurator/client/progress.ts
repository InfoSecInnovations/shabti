import { LitElement, html, nothing } from "lit";
import {
	BACK,
	INTERRUPTED,
	type Line,
	type LineEvent,
	type OperationStatus,
	RETURNING,
} from "../server/operationProtocol";

// how far from the bottom still counts as being at the bottom, scroll positions aren't always whole
// pixels
const PIN_THRESHOLD = 4;

const renderLine = (line: Line) =>
	line.kind == "message"
		? html`<p>${line.text}</p>`
		: // without a value the bar is indeterminate, which is the honest thing to show before the
			// total is known
			html`<p>
				<label
					>${line.label}
					<progress
						value=${line.total ? line.done : nothing}
						max=${line.total || nothing}
					></progress
				></label>
				${line.detail}
			</p>`;

/**
 * The scrollable log of an operation. It follows the newest line as long as it's scrolled to the
 * bottom, and leaves the view alone while the user has scrolled up to read something.
 */
class ShabtiConsole extends LitElement {
	static properties = { lines: { attribute: false } };
	declare lines: Line[];
	private pinned = true;

	constructor() {
		super();
		this.lines = [];
	}

	// rendered into the page rather than a shadow root, so style.css applies
	protected createRenderRoot() {
		return this;
	}

	protected willUpdate() {
		this.pinned =
			this.scrollHeight - this.scrollTop - this.clientHeight <= PIN_THRESHOLD;
	}

	protected updated() {
		if (this.pinned) this.scrollTop = this.scrollHeight;
	}

	protected render() {
		return this.lines.map(renderLine);
	}
}

/** watches an operation's events and shows its progress, then where it ended up */
class ShabtiOperation extends LitElement {
	static properties = {
		events: { type: String },
		lines: { state: true },
		failure: { state: true },
	};
	declare events: string;
	declare lines: Line[];
	declare failure: string | undefined;
	private source?: EventSource;

	constructor() {
		super();
		this.lines = [];
	}

	protected createRenderRoot() {
		return this;
	}

	connectedCallback() {
		super.connectedCallback();
		const source = new EventSource(this.events);
		this.source = source;
		source.addEventListener("line", (e) => {
			const { index, line } = JSON.parse(e.data) as LineEvent;
			// replayed lines arrive again after a reconnect, so they replace rather than append
			const lines = [...this.lines];
			lines[index] = line;
			this.lines = lines;
		});
		source.addEventListener("status", (e) => {
			const status = JSON.parse(e.data) as OperationStatus;
			if (status.status == "running") return;
			// the server closes the stream after this, which would otherwise read as a lost connection
			source.close();
			if (status.status == "failed") {
				this.failure = status.message;
				return;
			}
			this.lines = [...this.lines, { kind: "message", text: RETURNING }];
			window.location.href = `/?done=${encodeURIComponent(status.message)}`;
		});
		// EventSource reconnects by itself, the warning stands until it manages to. When the
		// configurator has restarted the operation is gone, the retry gets a 404 and the source gives
		// up, leaving the warning in place
		source.addEventListener("open", () => {
			this.failure = undefined;
		});
		source.addEventListener("error", () => {
			this.failure = INTERRUPTED;
		});
	}

	disconnectedCallback() {
		super.disconnectedCallback();
		this.source?.close();
	}

	protected render() {
		return html`<shabti-console .lines=${this.lines}></shabti-console>
			${
				this.failure
					? html`<p class="error">${this.failure}</p>
							<p><a href="/">${BACK}</a></p>`
					: nothing
			}`;
	}
}

customElements.define("shabti-console", ShabtiConsole);
customElements.define("shabti-operation", ShabtiOperation);
