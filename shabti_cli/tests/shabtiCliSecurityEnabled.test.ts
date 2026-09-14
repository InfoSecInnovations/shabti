import {
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	jest,
	test,
} from "bun:test";
import buildProgram from "../buildProgram";
import { $ } from "bun";
import path from "node:path";
import getAuthClient from "../getAuthClient";
import { randomBytes } from "node:crypto";

// finite, so a hang shows up as a failure in the report instead of stalling the whole run
jest.setTimeout(10 * 60_000);

describe.if(process.env.SHABTI_SECURITY_ENABLED == "True")(
	"CLI - Security enabled Shabti instance",
	() => {
		const filename = "test_doc.txt";
		const filePath = path.join(import.meta.dir, filename);
		// a collection refuses a second copy of a file it already holds, so a test that wants
		// two documents in one collection has to hand it two different ones
		const otherFilePath = path.join(import.meta.dir, "prompt_test.md");
		// prompting runs on whichever chat model is currently loaded, so we make sure
		// there is one
		beforeAll(async () => {
			const client = await getAuthClient();
			const models = await client.getModels(["chat"]);
			const modelName =
				models.data.find((model) => model.tags.includes("default"))?.id ??
				models.data[0]!.id;
			for await (const item of await client.loadModel(modelName)) {
			}
		});
		test("create collection", async () => {
			const collectionName = randomBytes(8).toString("hex");
			const program = await buildProgram();
			await program.parseAsync(
				[
					"collection",
					"create",
					collectionName,
					"--location",
					"shared",
					"--owner",
					"testadmin",
				],
				{
					from: "user",
				},
			);
			const client = await getAuthClient();
			const collections = await client.getCollections();
			const matchingCollection = collections.find(
				(collection) => collection.collectionName == collectionName,
			);
			expect(matchingCollection).toBeTruthy();
			if (matchingCollection)
				await getAuthClient().then((client) =>
					client.deleteCollection(matchingCollection.collectionId),
				); // clean up the collection
		});
		describe("CLI - Security enabled Shabti instance - tests with collection ID", () => {
			let collectionId: string;
			beforeEach(async () => {
				collectionId = await getAuthClient().then((client) =>
					client.createCollection(
						randomBytes(8).toString("hex"),
						"shared",
						"testadmin",
					),
				);
			});
			afterEach(async () => {
				try {
					await getAuthClient().then((client) =>
						client.deleteCollection(collectionId),
					);
				} catch {
					// collection may have already been deleted by a test
				}
			});
			test("list collections", async () => {
				// TODO: can we call the CLI app programatically instead of running the shell like this?
				const output = await $`bun run index.ts collection list`
					.cwd(path.resolve(path.join(import.meta.dir, "..")))
					.env({ ...process.env })
					.text();
				expect(output).toInclude(collectionId);
			});
			test("ingest file", async () => {
				const filename = "test_doc.txt";
				const filePath = path.join(import.meta.dir, filename);
				const program = await buildProgram();
				await program.parseAsync(
					["ingest", "file", filePath, "--collection", collectionId],
					{ from: "user" },
				);
				const client = await getAuthClient();
				const docs = await client.getDocuments(collectionId);
				const matchingDocument = docs.documents.find(
					(document) => document.filename == filename,
				);
				expect(matchingDocument).toBeTruthy();
			});
			test("ingest urls", async () => {
				const urls = [
					"https://www.scrapethissite.com/pages/forms/",
					"https://www.scrapethissite.com/pages/simple/",
				];
				const program = await buildProgram();
				await program.parseAsync(
					["ingest", "urls", ...urls, "--collection", collectionId],
					{ from: "user" },
				);
				const client = await getAuthClient();
				const docs = await client.getDocuments(collectionId);
				const matchingDocuments = docs.documents.filter((document) =>
					urls.includes(document.source),
				);
				expect(matchingDocuments.length == urls.length).toBeTrue();
			});
			test("ingest directory", async () => {
				const directoryName = "test_dir";
				const directoryFiles = ["test_doc2.txt", "test_doc3.txt"];
				const directoryPath = path.join(import.meta.dir, directoryName);
				const program = await buildProgram();
				await program.parseAsync(
					["ingest", "directory", directoryPath, "--collection", collectionId],
					{ from: "user" },
				);
				const client = await getAuthClient();
				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.map((document) => document.filename),
				).toContainValues(directoryFiles);
			});
			test("prompt", async () => {
				const filename = "prompt_test.md";
				const filePath = path.join(import.meta.dir, filename);
				const client = await getAuthClient();
				for await (const item of await client.insertFiles(collectionId, [
					filePath,
				])) {
				}
				const program = await buildProgram();
				await program.parseAsync(
					[
						"prompt",
						"What does the word prompting mean?",
						"--collection",
						collectionId,
						"--task",
						"question",
					],
					{ from: "user" },
				);
			});
			describe("CLI - Security enabled Shabti instance - tests with document IDs", () => {
				let documentIds: string[];
				beforeEach(async () => {
					const client = await getAuthClient();
					documentIds = [];
					let documentId;
					for await (const item of await client.insertFiles(collectionId, [
						filePath,
					])) {
						documentId = item.documentId;
					}
					documentIds.push(documentId);
					for await (const item of await client.insertFiles(collectionId, [
						otherFilePath,
					])) {
						documentId = item.documentId;
					}
					documentIds.push(documentId);
				});
				test("list documents", async () => {
					const output =
						await $`bun run index.ts document list "--" ${collectionId}`
							.cwd(path.resolve(path.join(import.meta.dir, "..")))
							.env({ ...process.env })
							.text();
					for (const documentId of documentIds) {
						expect(output).toInclude(documentId);
					}
				});
				test("delete documents", async () => {
					const program = await buildProgram();
					await program.parseAsync(
						[
							"document",
							"delete",
							"--collection",
							collectionId,
							"--",
							...documentIds,
						],
						{ from: "user" },
					);
					const client = await getAuthClient();
					const docs = await client.getDocuments(collectionId);
					expect(
						docs.documents.map((document) => document.documentId),
					).not.toContainAnyValues(documentIds);
				});
			});
			test("delete collection", async () => {
				const program = await buildProgram();
				await program.parseAsync(["collection", "delete", collectionId], {
					from: "user",
				});
				const client = await getAuthClient();
				const collections = await client.getCollections();
				const matchingCollection = collections.find(
					(collection) => collection.collectionId == collectionId,
				);
				expect(matchingCollection).toBeFalsy();
			});
		});
	},
);
