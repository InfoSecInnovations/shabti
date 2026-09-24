import { openAsBlob } from "node:fs";
import * as path from "node:path";
import * as querystring from "node:querystring";
import { EXPECTED_CODES } from "./codes";
import {
	DocumentInfo,
	DocumentList,
	IngestInfo,
	ModelLoadInfo,
	ModelStatusInfo,
	parseIngestInfo,
	parseIngestLine,
	PromptConfigInfo,
	TaskInfo,
	WebFile,
} from "./dataTypes";

export class BaseShabtiClient {
	serverUrl: string;
	constructor(serverUrl: string) {
		this.serverUrl = serverUrl;
	}

	protected async createFormData(name: string, filePaths: string[]) {
		const fd = new FormData();
		for (const filePath of filePaths) {
			const file = await openAsBlob(filePath);
			fd.append(name, file, path.basename(filePath));
		}
		return fd;
	}

	protected async makeRequest(
		method: string,
		url: string,
		json?: any,
		files?: FormData,
		params?: {},
	) {
		const body = (() => {
			if (json) return JSON.stringify(json);
			if (files) return files;
			return undefined;
		})();
		const headers = (() => {
			if (json) return { "Content-Type": "application/json" };
			return undefined;
		})();
		const fullUrl = new URL(url, this.serverUrl);
		// querystring is used rather than URLSearchParams as it expands list params
		// into the repeated keys the API expects, instead of joining them into a
		// single comma separated value
		if (params) fullUrl.search = querystring.stringify(params);
		const response = await fetch(fullUrl, {
			method,
			body,
			headers,
		});
		if (!EXPECTED_CODES.includes(response.status)) {
			const text = await response.text();
			throw new Error(`Request failed with status ${response.status}: ${text}`);
		}
		return response;
	}

	protected async streamRequest<T>(
		method: string,
		url: string,
		resultFunction?: (json: any) => T,
		json?: any,
		files?: FormData,
		params?: {},
	): Promise<ReadableStream<T>> {
		const res = await this.makeRequest(method, url, json, files, params);
		if (!res.body) throw new Error("Response had no body");
		const reader = res.body.getReader();
		const decoder = new TextDecoder();
		let current = "";
		const emit = (
			controller: ReadableStreamDefaultController<T>,
			line: string,
		) => {
			if (!line.trim()) return;
			const json = JSON.parse(line);
			controller.enqueue(resultFunction ? resultFunction(json) : json);
		};
		const stream = new ReadableStream<T>({
			async pull(controller) {
				const { done, value } = await reader.read();
				if (done) {
					// a last line with no trailing newline is still a line, and it's the one carrying
					// the completion event. decode() with no argument also flushes whatever the
					// decoder was holding on to of a split multi byte character
					emit(controller, current + decoder.decode());
					current = "";
					controller.close();
					return;
				}
				const text = decoder.decode(value, { stream: true });
				// it's possible we don't receive a full line in one chunk if the lines are long
				const splits = text.split("\n");
				if (splits.length == 1) {
					// this means there was no newline in the whole chunk
					current += splits[0];
				} else if (splits.length) {
					// there was a newline somewhere
					// we add the first split to the currently collected text
					const lines = [current + splits[0], ...splits.slice(1, -1)];
					// the last split is either empty due to the newline being at the end or contains the start of a new chunk
					current = splits[splits.length - 1]!;
					for (const line of lines) emit(controller, line);
				}
			},
			cancel(reason) {
				// detaching from an ingest partway through is an ordinary thing to do now that it
				// keeps running without us, and without this the connection stays open
				return reader.cancel(reason);
			},
		});

		return stream;
	}

	async deleteCollection(collectionId: string): Promise<string> {
		const res = await this.makeRequest("DELETE", `collections/${collectionId}`);
		const json = (await res.json()) as any;
		return json.collection_id;
	}

	async getDocuments(
		collectionId: string,
		search?: string,
		sort?: string,
		maxResults?: number,
		filterDocumentType?: string[],
		page: number = 0,
	): Promise<DocumentList> {
		const params: any = {};
		if (search) params.search = search;
		if (sort) params.sort = sort;
		if (maxResults) params.max_results = maxResults;
		if (filterDocumentType) params.filter_document_type = filterDocumentType;
		params.page = page;
		const res = await this.makeRequest(
			"GET",
			`collections/${collectionId}/documents`,
			undefined,
			undefined,
			params,
		);
		const json = (await res.json()) as any;
		return new DocumentList(
			json.documents.map(
				(item: any) =>
					new DocumentInfo(
						item.document_id,
						item.source,
						item.ingest_date,
						item.page_count,
						item.vector_count,
						item.media_type,
						item.filename,
					),
			),
			json.total_hits,
			json.total_documents,
		);
	}

	async getDocumentTypes(collectionId: string): Promise<string[]> {
		const res = await this.makeRequest(
			"GET",
			`collections/${collectionId}/document_types`,
		);
		return (await res.json()) as string[];
	}

	async startFilesIngest(
		collectionId: string,
		filePaths: string[],
	): Promise<IngestInfo> {
		const res = await this.makeRequest(
			"POST",
			`collections/${collectionId}/documents/files`,
			undefined,
			await this.createFormData("files", filePaths),
		);
		return parseIngestInfo(await res.json());
	}

	async startUrlsIngest(
		collectionId: string,
		urls: string[],
		maxDepth = 1,
	): Promise<IngestInfo> {
		const res = await this.makeRequest(
			"POST",
			`collections/${collectionId}/documents/urls`,
			urls,
			undefined,
			{ max_depth: maxDepth },
		);
		return parseIngestInfo(await res.json());
	}

	async getIngests(): Promise<IngestInfo[]> {
		const res = await this.makeRequest("GET", "ingests");
		const json = (await res.json()) as any[];
		return json.map((item: any) => parseIngestInfo(item));
	}

	async streamIngest(ingestId: string) {
		return this.streamRequest("GET", `ingests/${ingestId}`, parseIngestLine);
	}

	async cancelIngest(ingestId: string): Promise<IngestInfo> {
		const res = await this.makeRequest("DELETE", `ingests/${ingestId}`);
		return parseIngestInfo(await res.json());
	}

	// an ingest outlives the request that starts it now, so this is the POST plus a drain of the
	// stream it returns, which ends when the ingest does. The POST stays inside this await because
	// it's where a permission denial surfaces, and callers get that back before they start
	// iterating rather than partway through
	async insertFiles(collectionId: string, filePaths: string[]) {
		const ingest = await this.startFilesIngest(collectionId, filePaths);
		return this.streamIngest(ingest.ingestId);
	}

	async insertUrls(collectionId: string, urls: string[], maxDepth = 1) {
		const ingest = await this.startUrlsIngest(collectionId, urls, maxDepth);
		return this.streamIngest(ingest.ingestId);
	}

	async deleteDocument(collectionId: string, documentId: string) {
		const res = await this.makeRequest(
			"DELETE",
			`collections/${collectionId}/documents/${documentId}`,
		);
		const json = (await res.json()) as any;
		return json.document_id;
	}

	async getTasks(): Promise<{ [key: string]: TaskInfo }> {
		const res = await this.makeRequest("GET", "tasks");
		const json = (await res.json()) as any;
		return Object.entries(json).reduce((acc, [key, value]: any) => {
			return { ...acc, [key]: new TaskInfo(value.greeting, value.prompt) };
		}, {});
	}

	async getPersonas(): Promise<{ [key: string]: PromptConfigInfo }> {
		const res = await this.makeRequest("GET", "personas");
		const json = (await res.json()) as any;
		return Object.entries(json).reduce((acc, [key, value]: any) => {
			return { ...acc, [key]: new PromptConfigInfo(value.prompt) };
		}, {});
	}

	async getEnhancers(): Promise<{ [key: string]: PromptConfigInfo }> {
		const res = await this.makeRequest("GET", "enhancers");
		const json = (await res.json()) as any;
		return Object.entries(json).reduce((acc, [key, value]: any) => {
			return { ...acc, [key]: new PromptConfigInfo(value.prompt) };
		}, {});
	}

	async prompt(
		collectionId: string,
		prompt: string,
		task: string,
		persona?: string,
		enhancers?: string[],
		filePath?: string,
	): Promise<ReadableStream<any>> {
		const fileId = await (async () => {
			if (filePath) {
				const res = await this.makeRequest(
					"POST",
					"/prompt/source_file",
					undefined,
					await this.createFormData("file", [filePath]),
				).then((res) => res.json() as any);
				return res.id;
			}
			return undefined;
		})();

		return this.streamRequest("POST", "prompt", undefined, {
			collection_id: collectionId,
			user_input: prompt,
			task,
			persona,
			enhancers,
			file_id: fileId,
		});
	}

	async apiStatus(): Promise<boolean> {
		try {
			const res = await this.makeRequest("GET", "/");
			return res.status == 200;
		} catch {
			return false;
		}
	}

	async llmStatus(): Promise<boolean> {
		const res = await this.makeRequest("GET", "status/llm");
		const json = (await res.json()) as any;
		return json.running;
	}

	async opensearchStatus(): Promise<boolean> {
		const res = await this.makeRequest("GET", "status/opensearch");
		const json = (await res.json()) as any;
		return json.running;
	}

	async tikaStatus(): Promise<boolean> {
		const res = await this.makeRequest("GET", "status/tika");
		const json = (await res.json()) as any;
		return json.running;
	}

	// the chat model to start on: the user's last choice when security is enabled,
	// otherwise whichever model is currently loaded
	async getChatModelSelection(): Promise<string | null> {
		const res = await this.makeRequest("GET", "models/chat/selection");
		const json = (await res.json()) as any;
		return json ? json.model_name : null;
	}

	async setChatModelSelection(modelName: string) {
		await this.makeRequest("PUT", "models/chat/selection", {
			model_name: modelName,
		});
	}

	async loadModel(modelName: string) {
		return this.streamRequest(
			"POST",
			"models/pull",
			(json) =>
				new ModelLoadInfo(
					json.progress,
					json.total,
					json.model_name,
					json.info,
				),
			{ model_name: modelName },
		);
	}

	async getFile(collectionId: string, documentId: string) {
		const res = await this.makeRequest(
			"GET",
			`files/${collectionId}/${documentId}`,
		);
		const mediaType = res.headers.get("Content-Type");
		if (!mediaType) throw new Error("Content-Type header was missing");
		return new WebFile(await res.blob(), mediaType);
	}

	async getModels(tags?: string[]): Promise<{ data: ModelStatusInfo[] }> {
		const params: any = {};
		if (tags) params.tags = tags;
		const res = await this.makeRequest(
			"GET",
			"models",
			undefined,
			undefined,
			params,
		);
		const json = (await res.json()) as any;
		return {
			data: json.data.map(
				(item: any) =>
					new ModelStatusInfo(item.id, item.tags, item.status.value),
			),
		};
	}
}
