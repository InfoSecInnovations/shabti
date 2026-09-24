import {
	afterEach,
	beforeAll,
	beforeEach,
	describe,
	expect,
	jest,
	test,
} from "bun:test";
import { ShabtiAuthorizationClient } from "../authClient";
import getOpenIdConfig from "../getOpenIdConfig";
import apiUrl from "./apiUrl";
import * as openIdClient from "openid-client";
import path = require("node:path");
import { randomBytes } from "node:crypto";

const filename = "test_doc.txt";
const testDocPath = path.join(import.meta.dir, filename);
const promptFilename = "test_doc.txt";
const promptDocPath = path.join(import.meta.dir, promptFilename);
const url = "https://www.scrapethissite.com/pages/simple/";

// finite, so a hang shows up as a failure in the report instead of stalling the whole run
jest.setTimeout(10 * 60_000);

describe.if(process.env.SHABTI_SECURITY_ENABLED == "True")(
	"Node Client - Security enabled Shabti instance",
	async () => {
		// the package's own helper, which already resolves KEYCLOAK_HOST
		const getConfig = getOpenIdConfig;
		const getClientForUser = async (username: string) => {
			process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
			const config = await getConfig();
			const token = await openIdClient.genericGrantRequest(config, "password", {
				username,
				password: "test",
			});
			return new ShabtiAuthorizationClient(apiUrl("https"), token, config);
		};
		const getAdminClient = async () => {
			process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
			const config = await getConfig();
			const token = await openIdClient.clientCredentialsGrant(config);
			return new ShabtiAuthorizationClient(apiUrl("https"), token, config);
		};
		// prompting runs on whichever chat model is currently loaded, so we make sure
		// there is one
		beforeAll(async () => {
			const client = await getAdminClient();
			const models = await client.getModels(["chat"]);
			const modelName =
				models.data.find((model) => model.tags.includes("default"))?.id ??
				models.data[0]!.id;
			for await (const item of await client.loadModel(modelName)) {
			}
		});
		const createCollectionForUser = (owner: string, location: string) =>
			getAdminClient().then((client) =>
				client.createCollection(
					randomBytes(8).toString("hex"),
					location,
					owner,
				),
			);
		const userInfoUsers = [
			["testadmin", "collection_admin"],
			["testshared", "shared_read_write"],
			["testprivate", "private_collection"],
			["testsharedread", "shared_read"],
			["testnothing", null],
		];
		test.each(userInfoUsers)(
			"user %p has correct info",
			async (username, role) => {
				const client = await getClientForUser(username);
				const info = await client.getUserInfo();
				expect(info.preferred_username).toEqual(username);
				if (!role) expect(info).not.toContainKey("resource_access");
				else
					expect(info.resource_access["shabti-auth"]["roles"]).toContainValue(
						role,
					);
			},
		);

		const canCreateCollectionUsers = [
			["testadmin", "private"],
			["testadmin", "shared"],
			["testshared", "shared"],
			["testprivate", "private"],
		];
		test.each(canCreateCollectionUsers)(
			"user %p can create %p collection",
			async (username, location) => {
				const client = await getClientForUser(username);
				const collectionName = randomBytes(8).toString("hex");
				const collectionId = await client.createCollection(
					collectionName,
					location,
				);
				expect(collectionId).toBeTruthy();
				const collections = await getAdminClient().then((client) =>
					client.getCollections(),
				);
				expect(
					collections.some(
						(collection) =>
							collection.collectionId == collectionId &&
							collection.collectionName == collectionName,
					),
				).toBeTrue();
				await getAdminClient().then((client) =>
					client.deleteCollection(collectionId),
				); // clean up the collection
			},
		);
		const cannotCreateCollectionUsers = [
			["testshared", "private"],
			["testprivate", "shared"],
			["testsharedread", "private"],
			["testsharedread", "shared"],
			["testnothing", "private"],
			["testnothing", "shared"],
		];
		test.each(cannotCreateCollectionUsers)(
			"user %p cannot create %p collection",
			async (username, location) => {
				const client = await getClientForUser(username);
				const collectionName = randomBytes(8).toString("hex");
				expect(async () => {
					await client.createCollection(collectionName, location);
				}).toThrow();
				const collections = await getAdminClient().then((client) =>
					client.getCollections(),
				);
				expect(
					collections.some(
						(collection) => collection.collectionName == collectionName,
					),
				).toBeFalse();
			},
		);
		describe("Node Client - Security enabled Shabti instance - tests with collection ID", () => {
			let collectionId: string;
			const collectionIdFixture = (owner, location) => {
				beforeEach(async () => {
					collectionId = await createCollectionForUser(owner, location);
				});
				afterEach(async () => {
					try {
						await getAdminClient().then((client) =>
							client.deleteCollection(collectionId),
						);
					} catch {}
				});
			};
			const canReadCollectionUsers = [
				["testadmin", "testadmin", "shared"],
				["testadmin", "testadmin", "private"],
				["testadmin", "testprivate", "private"],
				["testsharedread", "testadmin", "shared"],
				["testshared", "testadmin", "shared"],
				["testprivate", "testprivate", "private"],
			];
			describe.each(canReadCollectionUsers)(
				"Node Client - Security enabled Shabti instance - users can read collection",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} can read ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						const docs = await client.getDocuments(collectionId);
						expect(docs).toBeTruthy();
						const collections = await client.getCollections();
						expect(
							collections.some(
								(collection) => collection.collectionId == collectionId,
							),
						).toBeTrue();
					});
				},
			);
			const cannotReadCollectionUsers = [
				["testsharedread", "testadmin", "private"],
				["testshared", "testadmin", "private"],
				["testprivate", "testadmin", "private"],
				["testprivate", "testadmin", "shared"],
				["testnothing", "testadmin", "shared"],
				["testnothing", "testadmin", "private"],
			];
			describe.each(cannotReadCollectionUsers)(
				"Node Client - Security enabled Shabti instance - users cannot read collection",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} cannot read ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						expect(async () => {
							await client.getDocuments(collectionId);
						}).toThrow();
						const collections = await client.getCollections();
						expect(
							collections.some(
								(collection) => collection.collectionId == collectionId,
							),
						).toBeFalse();
					});
				},
			);
			const allPermissions = [
				"collection:shared:create",
				"collection:private:assign",
				"delete",
				"collection:private:create",
				"update",
				"read",
			];
			const userPermissionMappings = [
				[
					"testadmin",
					[
						"collection:shared:create",
						"collection:private:assign",
						"delete",
						"collection:private:create",
						"update",
						"read",
					],
					"testadmin",
					"shared",
				],
				[
					"testshared",
					["collection:shared:create", "delete", "update", "read"],
					"testshared",
					"shared",
				],
				[
					"testprivate",
					["collection:private:create", "delete", "update", "read"],
					"testprivate",
					"private",
				],
				["testsharedread", ["read"], "testadmin", "shared"],
				["testnothing", [], "testadmin", "shared"],
			];
			describe.each(userPermissionMappings)(
				"Node Client - Security enabled Shabti instance - get user permissions",
				async (
					username: string,
					permissions: string[],
					owner: string,
					location: string,
				) => {
					collectionIdFixture(owner, location); // we need a collection to exist for all the permissions to appear
					test(`user ${username} has correct permissions`, async () => {
						const client = await getClientForUser(username);
						const userPermissions = await client.getPermissions();
						for (const permission of allPermissions) {
							if (permissions.includes(permission)) {
								expect(userPermissions).toContain(permission);
							} else {
								expect(userPermissions).not.toContain(permission);
							}
						}
					});
				},
			);
			const allScopes = ["read", "update", "delete"];
			const userCollectionScopeMappings = [
				["testadmin", "testadmin", "shared", ["read", "update", "delete"]],
				["testadmin", "testadmin", "private", ["read", "update", "delete"]],
				["testadmin", "testprivate", "private", ["read", "update", "delete"]],
				["testsharedread", "testadmin", "shared", ["read"]],
				["testshared", "testadmin", "shared", ["read", "update", "delete"]],
				["testprivate", "testprivate", "private", ["read", "update", "delete"]],
				["testsharedread", "testadmin", "private", []],
				["testshared", "testadmin", "private", []],
				["testprivate", "testadmin", "private", []],
				["testprivate", "testadmin", "shared", []],
				["testnothing", "testadmin", "shared", []],
				["testnothing", "testadmin", "private", []],
			];
			describe.each(userCollectionScopeMappings)(
				"Node Client - Security enabled Shabti instance - test collection scopes",
				(
					username: string,
					owner: string,
					location: string,
					scopes: string[],
				) => {
					collectionIdFixture(owner, location);
					test(`user ${username} has correct scopes for ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						const userScopes = await client.getCollectionScopes(collectionId);
						for (const scope of allScopes) {
							if (scopes.includes(scope)) {
								expect(userScopes).toContain(scope);
							} else {
								expect(userScopes).not.toContain(scope);
							}
						}
					});
				},
			);
			const canIngestDocumentUsers = [
				["testadmin", "testadmin", "shared"],
				["testadmin", "testadmin", "private"],
				["testadmin", "testprivate", "private"],
				["testshared", "testadmin", "shared"],
				["testprivate", "testprivate", "private"],
			];
			describe.each(canIngestDocumentUsers)(
				"Node Client - Security enabled Shabti instance - users can ingest documents",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} can ingest into ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						let docId: string;
						for await (const item of await client.insertFiles(collectionId, [
							testDocPath,
						])) {
							docId = item.documentId;
						}
						expect(docId).toBeTruthy();
						const docs = await getAdminClient().then((client) =>
							client.getDocuments(collectionId),
						);
						expect(
							docs.documents.some(
								(document) =>
									document.documentId == docId && document.filename == filename,
							),
						).toBeTrue();
					});
				},
			);
			const cannotIngestDocumentUsers = [
				["testsharedread", "testadmin", "private"],
				["testsharedread", "testadmin", "shared"],
				["testshared", "testadmin", "private"],
				["testprivate", "testadmin", "private"],
				["testprivate", "testadmin", "shared"],
				["testnothing", "testadmin", "shared"],
				["testnothing", "testadmin", "private"],
			];
			describe.each(cannotIngestDocumentUsers)(
				"Node Client - Security enabled Shabti instance - users cannot ingest documents",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} cannot ingest into ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						expect(async () => {
							for await (const item of await client.insertFiles(collectionId, [
								testDocPath,
							])) {
							}
						}).toThrow();
						const docs = await getAdminClient().then((client) =>
							client.getDocuments(collectionId),
						);
						expect(
							docs.documents.some((document) => document.filename == filename),
						).toBeFalse();
					});
				},
			);
			describe.each(canIngestDocumentUsers)(
				"Node Client - Security enabled Shabti instance - users can ingest URLs",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} can ingest URLs into ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						let docId: string;
						for await (const item of await client.insertUrls(collectionId, [
							url,
						])) {
							docId = item.documentId;
						}
						expect(docId).toBeTruthy();
						const docs = await getAdminClient().then((client) =>
							client.getDocuments(collectionId),
						);
						expect(
							docs.documents.some(
								(document) =>
									document.documentId == docId && document.source == url,
							),
						).toBeTrue();
					});
				},
			);
			describe.each(cannotIngestDocumentUsers)(
				"Node Client - Security enabled Shabti instance - users cannot ingest URLs",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} cannot ingest URLs into ${owner}'s ${location} collection`, async () => {
						const client = await getClientForUser(username);
						expect(async () => {
							for await (const item of await client.insertUrls(collectionId, [
								url,
							])) {
							}
						}).toThrow();
						const docs = await getAdminClient().then((client) =>
							client.getDocuments(collectionId),
						);
						expect(
							docs.documents.some((document) => document.source == url),
						).toBeFalse();
					});
				},
			);
			describe("Node Client - Security enabled Shabti instance - tests with document ID", () => {
				let documentId: string;
				const documentIdFixture = (owner, location) => {
					collectionIdFixture(owner, location);
					beforeEach(async () => {
						for await (const item of await getAdminClient().then((client) =>
							client.insertFiles(collectionId, [testDocPath]),
						)) {
							documentId = item.documentId;
						}
					});
				};
				const canDeleteDocumentUsers = [
					["testadmin", "testadmin", "shared"],
					["testadmin", "testadmin", "private"],
					["testadmin", "testprivate", "private"],
					["testshared", "testadmin", "shared"],
					["testprivate", "testprivate", "private"],
				];
				describe.each(canDeleteDocumentUsers)(
					"Node Client - Security enabled Shabti instance - users can delete documents",
					(username, owner, location) => {
						documentIdFixture(owner, location);
						test(`user ${username} can delete document from ${owner}'s ${location} collection`, async () => {
							const userClient = await getClientForUser(username);
							await userClient.deleteDocument(collectionId, documentId);
							const adminClient = await getAdminClient();
							const docs = await adminClient.getDocuments(collectionId);
							expect(
								docs.documents.map((document) => document.documentId),
							).not.toContain(documentId);
						});
					},
				);
				const cannotDeleteDocumentUsers = [
					["testsharedread", "testadmin", "private"],
					["testsharedread", "testadmin", "shared"],
					["testshared", "testadmin", "private"],
					["testprivate", "testadmin", "private"],
					["testprivate", "testadmin", "shared"],
					["testnothing", "testadmin", "shared"],
					["testnothing", "testadmin", "private"],
				];
				describe.each(cannotDeleteDocumentUsers)(
					"Node Client - Security enabled Shabti instance - users cannot delete documents",
					(username, owner, location) => {
						documentIdFixture(owner, location);
						test(`user ${username} cannot delete document from ${owner}'s ${location} collection`, async () => {
							const userClient = await getClientForUser(username);
							expect(async () => {
								await userClient.deleteDocument(collectionId, documentId);
							}).toThrow();
							const adminClient = await getAdminClient();
							const docs = await adminClient.getDocuments(collectionId);
							expect(
								docs.documents.map((document) => document.documentId),
							).toContain(documentId);
						});
					},
				);
			});
			describe("Node Client - Security enabled Shabti instance - tests with prompting document ID", () => {
				let documentId: string;
				const promptDocumentIdFixture = (owner, location) => {
					collectionIdFixture(owner, location);
					beforeEach(async () => {
						for await (const item of await getAdminClient().then((client) =>
							client.insertFiles(collectionId, [promptDocPath]),
						)) {
							documentId = item.documentId;
						}
					});
				};
				const canPromptUsers = [
					["testadmin", "testadmin", "shared"],
					["testadmin", "testadmin", "private"],
					["testadmin", "testprivate", "private"],
					["testsharedread", "testadmin", "shared"],
					["testshared", "testadmin", "shared"],
					["testprivate", "testprivate", "private"],
				];
				describe.each(canPromptUsers)(
					"Node Client - Security enabled Shabti instance - users can prompt",
					(username, owner, location) => {
						promptDocumentIdFixture(owner, location);
						test(`user ${username} can prompt ${owner}'s ${location} collection`, async () => {
							const userClient = await getClientForUser(username);
							for await (const item of await userClient.prompt(
								collectionId,
								"What does the word prompting mean?",
								"question",
							)) {
							}
						});
					},
				);
				const cannotPromptUsers = [
					["testsharedread", "testadmin", "private"],
					["testshared", "testadmin", "private"],
					["testprivate", "testadmin", "private"],
					["testprivate", "testadmin", "shared"],
					["testnothing", "testadmin", "shared"],
					["testnothing", "testadmin", "private"],
				];
				describe.each(cannotPromptUsers)(
					"Node Client - Security enabled Shabti instance - users cannot prompt",
					(username, owner, location) => {
						promptDocumentIdFixture(owner, location);
						test(`user ${username} cannot prompt ${owner}'s ${location} collection`, async () => {
							const userClient = await getClientForUser(username);
							expect(async () => {
								for await (const item of await userClient.prompt(
									collectionId,
									"What does the word prompting mean?",
									"question",
								)) {
								}
							}).toThrow();
						});
					},
				);
			});

			const canDeleteCollectionUsers = [
				["testadmin", "testadmin", "shared"],
				["testadmin", "testadmin", "private"],
				["testadmin", "testshared", "shared"],
				["testadmin", "testprivate", "private"],
				["testshared", "testadmin", "shared"],
				["testprivate", "testprivate", "private"],
			];
			describe.each(canDeleteCollectionUsers)(
				"Node Client - Security enabled Shabti instance - users can delete collections",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} can delete ${owner}'s ${location} collection`, async () => {
						const adminClient = await getAdminClient();
						const userClient = await getClientForUser(username);
						await userClient.deleteCollection(collectionId);
						const collections = await adminClient.getCollections();
						expect(
							collections.map((collection) => collection.collectionId),
						).not.toContain(collectionId);
					});
				},
			);
			const cannotDeleteCollectionUsers = [
				["testsharedread", "testadmin", "shared"],
				["testsharedread", "testadmin", "private"],
				["testshared", "testadmin", "private"],
				["testprivate", "testadmin", "private"],
				["testprivate", "testadmin", "shared"],
				["testnothing", "testadmin", "shared"],
				["testnothing", "testadmin", "private"],
			];
			describe.each(cannotDeleteCollectionUsers)(
				"Node Client - Security enabled Shabti instance - users cannot delete collections",
				(username, owner, location) => {
					collectionIdFixture(owner, location);
					test(`user ${username} cannot delete ${owner}'s ${location} collection`, async () => {
						const adminClient = await getAdminClient();
						const userClient = await getClientForUser(username);
						expect(async () => {
							await userClient.deleteCollection(collectionId);
						}).toThrow();
						const collections = await adminClient.getCollections();
						expect(
							collections.map((collection) => collection.collectionId),
						).toContain(collectionId);
					});
				},
			);
		});
	},
);
