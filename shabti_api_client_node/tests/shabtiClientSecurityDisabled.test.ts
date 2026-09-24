import {
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	jest,
	test,
} from "bun:test";
import { ShabtiClient } from "../client";
import apiUrl from "./apiUrl";
import path = require("node:path");
import { randomBytes } from "node:crypto";

const filename = "test_doc.txt";
const testDocPath = path.join(import.meta.dir, filename);
const promptFilename = "test_doc.txt";
const promptDocPath = path.join(import.meta.dir, promptFilename);
const url = "https://www.scrapethissite.com/pages/simple/";

// finite, so a hang shows up as a failure in the report instead of stalling the whole run
jest.setTimeout(10 * 60_000);

describe.if(process.env.SHABTI_SECURITY_ENABLED == "False")(
	"Node Client - Security disabled Shabti instance",
	() => {
		const getClient = () => new ShabtiClient(apiUrl("http"));

		// prompting runs on whichever chat model is currently loaded, so we make sure
		// there is one
		let loadedChatModel: string;
		beforeAll(async () => {
			const client = getClient();
			const models = await client.getModels(["chat"]);
			loadedChatModel =
				models.data.find((model) => model.tags.includes("default"))?.id ??
				models.data[0]!.id;
			for await (const item of await client.loadModel(loadedChatModel)) {
			}
		});

		test("get models", async () => {
			const models = await getClient().getModels(["chat"]);
			expect(
				models.data.some(
					(model) =>
						model.id == loadedChatModel &&
						model.tags.includes("chat") &&
						model.status == "loaded",
				),
			).toBeTrue();
		});
		test("get chat model selection", async () => {
			expect(await getClient().getChatModelSelection()).toBe(loadedChatModel);
		});
		test("create collection", async () => {
			const collectionName = randomBytes(8).toString("hex");
			const client = getClient();
			const collectionId = await client.createCollection(collectionName);
			expect(collectionId).toBeTruthy();
			const collections = await client.getCollections();
			expect(
				collections.some(
					(collection) =>
						collection.collectionId == collectionId &&
						collection.collectionName == collectionName,
				),
			).toBeTrue();
			await client.deleteCollection(collectionId); // clean up the collection
		});
		describe("Node Client - Security disabled Shabti instance - tests with collection ID", () => {
			let collectionId: string;
			beforeEach(async () => {
				collectionId = await getClient().createCollection(
					randomBytes(8).toString("hex"),
				);
			});
			afterEach(async () => {
				try {
					await getClient().deleteCollection(collectionId);
				} catch {
					// collection may have already been deleted by a test
				}
			});
			test("list collections", async () => {
				const collectionList = await getClient().getCollections();
				expect(
					collectionList.some(
						(collection) => collection.collectionId == collectionId,
					),
				).toBeTrue();
			});
			test("insert document", async () => {
				let docId: string;
				const client = getClient();
				for await (const item of await client.insertFiles(collectionId, [
					testDocPath,
				])) {
					docId = item.documentId;
				}
				expect(docId).toBeTruthy();
				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.some(
						(document) =>
							document.documentId == docId && document.filename == filename,
					),
				).toBeTrue();
			});
			test("insert URL", async () => {
				let docId: string;
				const client = getClient();
				for await (const item of await client.insertUrls(collectionId, [url])) {
					docId = item.documentId;
				}
				expect(docId).toBeTruthy();
				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.some(
						(document) =>
							document.source == url && document.documentId == docId,
					),
				).toBeTrue();
			});
			test("prompt", async () => {
				const client = getClient();
				for await (const item of await client.insertFiles(collectionId, [
					promptDocPath,
				])) {
				}
				for await (const item of await client.prompt(
					collectionId,
					"What does the word prompting mean?",
					"question",
				)) {
				}
			});
			describe("Node Client - Security disabled Shabti instance - tests with document ID", () => {
				let documentId: string;
				beforeEach(async () => {
					const client = getClient();
					for await (const item of await client.insertFiles(collectionId, [
						testDocPath,
					])) {
						documentId = item.documentId;
					}
				});
				test("list documents", async () => {
					const docs = await getClient().getDocuments(collectionId);
					expect(docs.documents.map((doc) => doc.documentId)).toContain(
						documentId,
					);
				});
				test("delete document", async () => {
					const client = getClient();
					await client.deleteDocument(collectionId, documentId);
					const docs = await getClient().getDocuments(collectionId);
					expect(docs.documents.map((doc) => doc.documentId)).not.toContain(
						documentId,
					);
				});
			});
			test("delete collection", async () => {
				const client = getClient();
				await client.deleteCollection(collectionId);
				const collectionList = await client.getCollections();
				expect(
					collectionList.some(
						(collection) => collection.collectionId == collectionId,
					),
				).toBeFalse();
			});
		});

		test("get llm status", async () => {
			const status = await getClient().llmStatus();
			expect(status).toBeTrue();
		});
		test("get OpenSearch status", async () => {
			const status = await getClient().opensearchStatus();
			expect(status).toBeTrue();
		});
		test("get API status", async () => {
			const status = await getClient().apiStatus();
			expect(status).toBeTrue();
		});
	},
);
