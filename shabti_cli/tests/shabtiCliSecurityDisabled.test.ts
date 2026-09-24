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
import getClient from "../getClient";
import { $ } from "bun";
import path from "node:path";
import { randomBytes } from "node:crypto";

// finite, so a hang shows up as a failure in the report instead of stalling the whole run
jest.setTimeout(10 * 60_000);

describe.if(process.env.SHABTI_SECURITY_ENABLED == "False")(
	"CLI - Security disabled Shabti instance",
	() => {
		const filename = "test_doc.txt";
		const filePath = path.join(import.meta.dir, filename);
		// a collection refuses a second copy of a file it already holds, so a test that wants
		// two documents in one collection has to hand it two different ones
		const otherFilePath = path.join(import.meta.dir, "prompt_test.md");
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
		test("pull model", async () => {
			const program = await buildProgram();
			await program.parseAsync(["model", "pull", loadedChatModel], {
				from: "user",
			});
			expect(await getClient().getChatModelSelection()).toBe(loadedChatModel);
		});
		test("list models", async () => {
			const output = await $`bun run index.ts model list --tags chat embeddings`
				.cwd(path.resolve(path.join(import.meta.dir, "..")))
				.env({ ...process.env })
				.text();
			expect(output).toInclude(loadedChatModel);
		});
		test("current model", async () => {
			const output = await $`bun run index.ts model current`
				.cwd(path.resolve(path.join(import.meta.dir, "..")))
				.env({ ...process.env })
				.text();
			expect(output).toInclude(loadedChatModel);
		});
		test("create collection", async () => {
			const collectionName = randomBytes(8).toString("hex");
			const program = await buildProgram();
			await program.parseAsync(["collection", "create", collectionName], {
				from: "user",
			});
			const client = getClient();
			const collections = await client.getCollections();
			expect(
				collections.some(
					(collection) => collection.collectionName == collectionName,
				),
			).toBeTrue();
		});
		describe("CLI - Security disabled Shabti instance - tests with collection ID", () => {
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
				// TODO: can we call the CLI app programatically instead of running the shell like this?
				const output = await $`bun run index.ts collection list`
					.cwd(path.resolve(path.join(import.meta.dir, "..")))
					.env({ ...process.env })
					.text();
				expect(output).toInclude(collectionId);
			});
			test("ingest file", async () => {
				const program = await buildProgram();
				await program.parseAsync(
					["ingest", "file", filePath, "--collection", collectionId],
					{ from: "user" },
				);
				const client = getClient();
				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.some((document) => document.filename == filename),
				).toBeTrue();
			});
			test("ingest a file the collection already holds", async () => {
				// the refusal reaches the stream as a DocumentIngestError, which has no page count and no
				// document id. it used to fall through to the progress bar, which drove it with NaN and
				// then reported the file as ingested with an id of `undefined` - the opposite of true
				// each test gets a fresh collection, so put the first copy in before capturing output
				const client = getClient();
				for await (const item of await client.insertFiles(collectionId, [
					filePath,
				])) {
				}
				const lines: string[] = [];
				const log = jest
					.spyOn(console, "log")
					.mockImplementation((...args: unknown[]) => {
						lines.push(args.map(String).join(" "));
					});
				try {
					const program = await buildProgram();
					await program.parseAsync(
						["ingest", "file", filePath, "--collection", collectionId],
						{ from: "user" },
					);
				} finally {
					log.mockRestore();
				}
				const output = lines.join("\n");
				expect(output).toInclude("already in this collection");
				expect(output).not.toInclude("undefined");

				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.filter((document) => document.filename == filename)
						.length,
				).toBe(1);
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
				const client = getClient();
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
				const client = getClient();
				const docs = await client.getDocuments(collectionId);
				expect(
					docs.documents.map((document) => document.filename),
				).toContainValues(directoryFiles);
			});
			test("prompt", async () => {
				const filename = "prompt_test.md";
				const filePath = path.join(import.meta.dir, filename);
				const client = getClient();
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
			describe("CLI - Security disabled Shabti instance - tests with document IDs", () => {
				let documentIds: string[];
				beforeEach(async () => {
					const client = getClient();
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
					const client = getClient();
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
				const client = getClient();
				const collections = await client.getCollections();
				const matchingCollection = collections.find(
					(collection) => collection.collectionId == collectionId,
				);
				expect(matchingCollection).toBeFalsy();
			});
		});
	},
);
