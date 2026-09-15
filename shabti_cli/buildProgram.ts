import { readdir } from "node:fs/promises";
import path from "node:path";
import * as cliProgress from "cli-progress";
import * as commander from "commander";
import {
	DocumentIngestError,
	UnsupportedFileError,
} from "@infosecinnovations/shabti-api-client";
import type { ShabtiAuthorizationClient } from "@infosecinnovations/shabti-api-client";
import type { ShabtiClient } from "@infosecinnovations/shabti-api-client";
import {
	type DocumentIngestInfo,
	type PromptConfigInfo,
} from "@infosecinnovations/shabti-api-client/dist/dataTypes";
import getAuthClient from "./getAuthClient";
import getClient from "./getClient";

export default async () => {
	const program = new commander.Command();

	const authEnabled = process.env.SHABTI_SECURITY_ENABLED == "True";
	const client = authEnabled ? await getAuthClient() : getClient();

	const collection = program.command("collection");
	if (authEnabled) {
		collection
			.command("create <name>")
			.requiredOption("-l, --location <location>", "shared or private")
			.requiredOption(
				"-o, --owner <owner>",
				"username this collection will be owned by",
			)
			.action((name, options, command) =>
				(client as ShabtiAuthorizationClient)
					.createCollection(name, options.location, options.owner)
					.then((collectionId) =>
						console.log(`Created collection ${name} with id ${collectionId}`),
					),
			);
	} else {
		collection
			.command("create <name>")
			.action((name, options, command) =>
				(client as ShabtiClient)
					.createCollection(name)
					.then((collectionId) =>
						console.log(`Created collection ${name} with id ${collectionId}`),
					),
			);
	}

	collection
		.command("delete <collections...>")
		.description("Delete collections with the specified IDs")
		.action(async (collections, options, command) => {
			for (const collectionId of collections) {
				await client
					.deleteCollection(collectionId)
					.then((collectionId) =>
						console.log(`Deleted collection with id ${collectionId}`),
					);
			}
		});
	collection
		.command("list")
		.action((options, command) =>
			client
				.getCollections()
				.then((collections) =>
					collections.forEach((collection) => console.log(collection)),
				),
		);

	const insert = async (
		insertStream: ReadableStream<
			DocumentIngestInfo | DocumentIngestError | UnsupportedFileError
		>,
		command: any,
	) => {
		let bar: cliProgress.SingleBar | undefined = undefined;
		let currentLabel;
		let currentId;
		for await (const item of insertStream) {
			if (item instanceof UnsupportedFileError) {
				if (bar) bar.stop();
				bar = undefined;
				currentLabel = undefined;
				console.log(item.message);
				continue;
			}
			// anything else that failed: a duplicate, an empty document, a forbidden URL. it carries no
			// `total` or `documentId`, so falling through to the bar below drove it with NaN and then
			// claimed the file had been ingested, with an id of `undefined`
			if (item instanceof DocumentIngestError) {
				if (bar) bar.stop();
				bar = undefined;
				currentLabel = undefined;
				const name = item.label ?? item.filename;
				console.log(name ? `${name}: ${item.message}` : item.message);
				continue;
			}
			if (currentLabel != item.label) {
				if (bar) {
					bar.stop();
					console.log(`Ingested ${currentLabel} with ID ${currentId}`);
				}
				bar = new cliProgress.SingleBar(
					{ format: "{bar} {value}/{total} pages {label}" },
					cliProgress.Presets.shades_classic,
				);
				bar.start(item.total, 0, { label: item.label });
				currentLabel = item.label;
				currentId = item.documentId;
			}
			// a crawl doesn't know how many pages it has until it has finished finding them, so the
			// total moves as it goes and only the last items carry the real count
			if (bar && bar.getTotal() !== item.total) bar.setTotal(item.total);
			if (bar) bar.update(item.progress + 1); // progress is 0 indexed but the bar is 1 indexed
		}
		if (bar) {
			bar.stop();
			console.log(`Ingested ${currentLabel} with ID ${currentId}`);
		}
	};

	const ingest = program.command("ingest");
	ingest
		.command("file <filepath>")
		.description("ingest a file into a collection")
		.requiredOption(
			"-c, --collection <collection>",
			"collection id to ingest into",
		)
		.action(async (filepath, options, command) =>
			insert(await client.insertFiles(options.collection, [filepath]), command),
		);
	ingest
		.command("directory <directory>")
		.description("ingest all files in a directory to a collection")
		.requiredOption(
			"-c, --collection <collection>",
			"collection id to ingest into",
		)
		.action(async (directory, options, command) => {
			const files = await readdir(directory, {
				withFileTypes: true,
				recursive: true,
			});
			const actualFiles = files.filter((file) => file.isFile());
			await insert(
				await client.insertFiles(
					options.collection,
					actualFiles.map((file) => path.join(file.parentPath, file.name)),
				),
				command,
			);
		});
	ingest
		.command("urls <urls...>")
		.description("ingest a list of URLs to a collection")
		.requiredOption(
			"-c, --collection <collection>",
			"collection id to ingest into",
		)
		.option(
			"-d, --depth [number]",
			"how many levels of links to follow, staying within the directory of each URL",
			"1",
		)
		.action(async (urls, options, command) =>
			insert(
				await client.insertUrls(
					options.collection,
					urls,
					parseInt(options.depth),
				),
				command,
			),
		);

	const model = program.command("model");
	model
		.command("pull <model>")
		.description("load a model, unloading any other of the same kind first")
		.action(async (modelName, options, command) => {
			let bar: cliProgress.SingleBar | undefined = undefined;
			for await (const item of await client.loadModel(modelName)) {
				if (!bar) {
					bar = new cliProgress.SingleBar(
						{ format: "{bar} {percentage}% {info}" },
						cliProgress.Presets.shades_classic,
					);
					bar.start(item.total, item.progress, { info: item.info ?? "" });
				}
				bar.update(item.progress, { info: item.info ?? "" });
			}
			if (bar) bar.stop();
			console.log(`Model ${modelName} is loaded`);
		});
	model
		.command("list")
		.description("list the models available to the server")
		.option("-t, --tags [tags...]", "only list models with these tags")
		.action((options, command) =>
			client
				.getModels(options.tags)
				.then((models) => models.data.forEach((model) => console.log(model))),
		);
	model
		.command("current")
		.description("show the chat model that will be used for prompting")
		.action((options, command) =>
			client
				.getChatModelSelection()
				.then((modelName) =>
					console.log(modelName ?? "No chat model is available"),
				),
		);

	program
		.command("prompt <userInput>")
		.requiredOption(
			"-c, --collection <collection>",
			"collection id to source the response from",
		)
		.requiredOption("-t, --task <task>", "task to use for the prompt")
		.option("-p, --persona <persona>", "persona to use for the prompt")
		.option("-e, --enhancers <enhancers...>", "enhancers to use for the prompt")
		.option(
			"-f, --file <file>",
			"file to add information to the prompt context",
		)
		.action(async (userInput, options, command) => {
			let sourceFound = false;
			for await (const item of await client.prompt(
				options.collection,
				userInput,
				options.task,
				options.persona,
				options.enhancers,
				options.file,
			)) {
				if (item.source) {
					if (!sourceFound) {
						console.log("Answering using the following sources:");
						sourceFound = true;
					}
					let docText = "";
					if (item.source.page_metadata.page)
						docText += `page ${item.source.page_metadata.page} of `;
					if (item.source.document_metadata.filename)
						docText += item.source.document_metadata.filename;
					else docText += item.source.document_metadata.source;
					console.log(docText);
				}
				if (item.response) {
					process.stdout.write(item.response);
				}
			}
		});

	const document = program.command("document");
	document
		.command("list <collection>")
		.option(
			"-s, --search [search]",
			"search for documents using this term. matches ID, filename, URL or document contents",
		)
		.option(
			"-o, --order [order]",
			'sort the results, the options are "relevance", "date_desc" and "date_asc"',
			"relevance",
		)
		.option(
			"-m, --max-results [number]",
			"number of results returned at a time",
			"10",
		)
		.option("-f, --filter [types...]", "filter by document type")
		.option(
			"-p, --page [number]",
			"page number, use this if you need to browse through the results",
			"0",
		)
		.action((collection, options, command) =>
			client
				.getDocuments(
					collection,
					options.search,
					options.order,
					parseInt(options.maxResults),
					options.filter,
					parseInt(options.page),
				)
				.then((documentList) => {
					console.log(
						`Total documents in collection: ${documentList.totalDocuments}`,
					);
					if (options.search || options.filter)
						console.log(
							`Number of results from current query: ${documentList.totalHits}`,
						);
					documentList.documents.forEach((document) => console.log(document));
				}),
		);
	document
		.command("delete <documents...>")
		.requiredOption(
			"-c, --collection <collection>",
			"collection id containing the documents to be deleted",
		)
		.action(async (documents, options, command) => {
			for (const documentId of documents) {
				await client
					.deleteDocument(options.collection, documentId)
					.then((documentId) =>
						console.log(`deleted document with id ${documentId}`),
					);
			}
		});

	const listPromptConfig = (
		items: { [key: string]: PromptConfigInfo },
		command: any,
	) =>
		Object.entries(items).forEach(([key, value]) => {
			console.log(key);
			console.log("");
			if (value.prompt) {
				const splits = value.prompt.split("\n");
				splits.forEach((split) => console.log(split.trim()));
			} else {
				// in reality only the search task should have an empty prompt!
				console.log(
					"this task just searches for matching documents without forming a response",
				);
			}
			console.log("");
		});

	program
		.command("task list")
		.action((options, command) =>
			client.getTasks().then((tasks) => listPromptConfig(tasks, command)),
		);

	program
		.command("persona list")
		.action((options, command) =>
			client
				.getPersonas()
				.then((personas) => listPromptConfig(personas, command)),
		);

	program
		.command("enhancer list")
		.action((options, command) =>
			client
				.getEnhancers()
				.then((enhancers) => listPromptConfig(enhancers, command)),
		);

	program.command("status").action(async (options, command) => {
		console.log(`LLM: ${(await client.llmStatus()) ? "online" : "offline"}`);
		console.log(
			`OpenSearch: ${(await client.opensearchStatus()) ? "online" : "offline"}`,
		);
	});

	return program;
};
