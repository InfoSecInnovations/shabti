export class CollectionInfo {
	collectionName: string;
	collectionId: string;

	constructor(collectionName: string, collectionId: string) {
		this.collectionName = collectionName;
		this.collectionId = collectionId;
	}
}

export class UserInfo {
	username: string;
	userId: string;

	constructor(username: string, userId: string) {
		this.username = username;
		this.userId = userId;
	}
}

export class AuthzCollectionInfo extends CollectionInfo {
	location: string;
	owner: UserInfo;

	constructor(
		collectionName: string,
		collectionId: string,
		location: string,
		owner: UserInfo,
	) {
		super(collectionName, collectionId);
		this.location = location;
		this.owner = owner;
	}
}

export class DocumentInfo {
	source: string;
	ingestDate: number;
	filename?: string;
	mediaType: string;
	documentId: string;
	pageCount: number;
	vectorCount: number;
	constructor(
		documentId: string,
		source: string,
		ingestDate: number,
		pageCount: number,
		vectorCount: number,
		mediaType: string,
		filename?: string,
	) {
		this.documentId = documentId;
		this.source = source;
		this.ingestDate = ingestDate;
		this.pageCount = pageCount;
		this.vectorCount = vectorCount;
		this.mediaType = mediaType;
		this.filename = filename;
	}
}

export class DocumentList {
	documents: DocumentInfo[];
	totalHits: number;
	totalDocuments: number;
	constructor(
		documents: DocumentInfo[],
		totalHits: number,
		totalDocuments: number,
	) {
		this.documents = documents;
		this.totalHits = totalHits;
		this.totalDocuments = totalDocuments;
	}
}

export class DocumentIngestInfo {
	progress: number;
	total: number;
	documentId: string;
	documentType: string;
	label: string;
	// the last event for a document, sent once it is in the index. absent from the lines the API
	// emitted before this existed, so read it as `json.complete ?? false`
	complete: boolean;

	constructor(
		progress: number,
		total: number,
		documentId: string,
		documentType: string,
		label: string,
		complete = false,
	) {
		this.progress = progress;
		this.total = total;
		this.documentId = documentId;
		this.documentType = documentType;
		this.label = label;
		this.complete = complete;
	}
}

export class DocumentIngestError {
	error: string;
	message: string;
	filename?: string;
	label?: string;

	constructor(
		error: string,
		message: string,
		filename?: string,
		label?: string,
	) {
		this.error = error;
		this.message = message;
		this.filename = filename;
		this.label = label;
	}
}

// queued is an ingest the server accepted but has not started: the concurrency caps hold it back
// rather than the POST being refused
export type IngestStatus =
	| "queued"
	| "running"
	| "complete"
	| "failed"
	| "cancelled";

export class IngestItemInfo {
	itemId: string;
	label: string;
	info?: DocumentIngestInfo;
	error?: DocumentIngestError;
	// how many files an archive was expanded into. An archive produces no document of its own, so
	// it has neither info nor error, and it never reaches the progress stream either
	expanded?: number;

	constructor(
		itemId: string,
		label: string,
		info?: DocumentIngestInfo,
		error?: DocumentIngestError,
		expanded?: number,
	) {
		this.itemId = itemId;
		this.label = label;
		this.info = info;
		this.error = error;
		this.expanded = expanded;
	}
}

export class IngestInfo {
	ingestId: string;
	collectionId: string;
	status: IngestStatus;
	started: number;
	items: IngestItemInfo[];
	finished?: number;
	error?: string;

	constructor(
		ingestId: string,
		collectionId: string,
		status: IngestStatus,
		started: number,
		items: IngestItemInfo[],
		finished?: number,
		error?: string,
	) {
		this.ingestId = ingestId;
		this.collectionId = collectionId;
		this.status = status;
		this.started = started;
		this.items = items;
		this.finished = finished;
		this.error = error;
	}
}

export class UnsupportedFileError {
	message: string;
	filename: string;

	constructor(message: string, filename: string) {
		this.message = message;
		this.filename = filename;
	}
}

// The API's keys are snake_case, and `response_model_exclude_unset` means an optional the server
// never set is absent rather than null - except the fields an ingest snapshot always passes
// explicitly, which arrive as null while it is still running. Both collapse to undefined here so a
// caller only has one kind of absent to check for. Written out per type rather than as a generic
// converter because these build class instances, and the constructors don't take their arguments
// in the order the wire uses.
export const parseDocumentIngestInfo = (json: any) =>
	new DocumentIngestInfo(
		json.progress,
		json.total,
		json.document_id,
		json.document_type,
		json.label,
		json.complete ?? false,
	);

export const parseDocumentIngestError = (json: any) =>
	new DocumentIngestError(
		json.error,
		json.message,
		json.filename ?? undefined,
		json.label ?? undefined,
	);

export const parseIngestItemInfo = (json: any) =>
	new IngestItemInfo(
		json.item_id,
		json.label,
		json.info ? parseDocumentIngestInfo(json.info) : undefined,
		json.error ? parseDocumentIngestError(json.error) : undefined,
		json.expanded ?? undefined,
	);

export const parseIngestInfo = (json: any) =>
	new IngestInfo(
		json.ingest_id,
		json.collection_id,
		json.status,
		json.started,
		json.items.map((item: any) => parseIngestItemInfo(item)),
		json.finished ?? undefined,
		json.error ?? undefined,
	);

// one line of an ingest stream. An `error` key means the item failed: an unsupported file keeps its
// own class, which is what the `instanceof` checks in the CLI and the web UI are written against,
// and anything else comes back as an object so one bad document doesn't end the stream for the rest
export const parseIngestLine = (
	json: any,
): DocumentIngestInfo | DocumentIngestError | UnsupportedFileError => {
	if (json.error) {
		if (json.error == "UnsupportedFileError")
			return new UnsupportedFileError(json.message, json.filename);
		return parseDocumentIngestError(json);
	}
	return parseDocumentIngestInfo(json);
};

export class PromptConfigInfo {
	prompt?: string;
	constructor(prompt?: string) {
		this.prompt = prompt;
	}
}

export class TaskInfo extends PromptConfigInfo {
	greeting: string;
	constructor(greeting: string, prompt?: string) {
		super(prompt);
		this.greeting = greeting;
	}
}

export class ModelLoadInfo {
	progress: number;
	total: number;
	modelName: string;
	info?: string;
	constructor(
		progress: number,
		total: number,
		modelName: string,
		info?: string,
	) {
		this.progress = progress;
		this.total = total;
		this.modelName = modelName;
		this.info = info;
	}
}

export class ModelStatusInfo {
	id: string;
	tags: string[];
	status: string;
	constructor(id: string, tags: string[], status: string) {
		this.id = id;
		this.tags = tags;
		this.status = status;
	}
}

export class ProviderInfo {
	id: string;
	name: string;
	credentialMode: string;
	hasSharedKey: boolean;
	constructor(
		id: string,
		name: string,
		credentialMode: string,
		hasSharedKey: boolean,
	) {
		this.id = id;
		this.name = name;
		this.credentialMode = credentialMode;
		this.hasSharedKey = hasSharedKey;
	}
}

export class ProviderModelInfo {
	// qualified as `<provider>/<model>`, which is what a prompt or a selection names
	id: string;
	providerId: string;
	modelId: string;
	displayName: string;
	category: string;
	contextLength?: number;
	// only llama.cpp's models have one, a remote model is always ready
	status?: string;
	constructor(
		id: string,
		providerId: string,
		modelId: string,
		displayName: string,
		category: string,
		contextLength?: number,
		status?: string,
	) {
		this.id = id;
		this.providerId = providerId;
		this.modelId = modelId;
		this.displayName = displayName;
		this.category = category;
		this.contextLength = contextLength;
		this.status = status;
	}
}

export class WebFile {
	bytes: Blob;
	mediaType: string;
	constructor(bytes: Blob, mediaType: string) {
		this.bytes = bytes;
		this.mediaType = mediaType;
	}
}
