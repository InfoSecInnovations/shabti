import * as client from "openid-client";
import { BaseShabtiClient } from "./baseClient";
import { EXPECTED_CODES } from "./codes";
import { AuthzCollectionInfo, UserInfo } from "./dataTypes";

class ShabtiTokenExpiredError extends Error {
	constructor(...params: any[]) {
		super(...params);
		this.name = "TokenExpiredError";
	}
}

class ShabtiAuthenticationError extends Error {
	constructor(...params: any[]) {
		super(...params);
		this.name = "ShabtiAuthenticationError";
	}
}

export class ShabtiAuthorizationClient extends BaseShabtiClient {
	token: client.TokenEndpointResponse;
	openIdConfig: client.Configuration;
	refreshTask: Promise<client.TokenEndpointResponse> | null = null;

	constructor(
		serverUrl: string,
		token: client.TokenEndpointResponse,
		openIdConfig: client.Configuration,
	) {
		super(serverUrl);
		this.token = token;
		this.openIdConfig = openIdConfig;
	}

	protected override async makeRequest(
		method: string,
		url: string,
		json?: any,
		files?: FormData,
		params?: {},
	): Promise<Response> {
		const doRequest = async (token: client.TokenEndpointResponse) => {
			const body = (() => {
				if (json) return JSON.stringify(json);
				if (files) return files;
				return undefined;
			})();
			// if there is a JSON body, add the correct content type header
			const headers: Record<string, string> = {
				Authorization: `Bearer ${token.access_token}`,
				...(json && { "Content-Type": "application/json" }),
			};
			const fullUrl = new URL(url, this.serverUrl);
			if (params && Object.keys(params).length) {
				fullUrl.search = new URLSearchParams(params).toString();
			}
			const response = await fetch(fullUrl, {
				method,
				body,
				headers,
			});
			if (response.status === 401) {
				throw new ShabtiTokenExpiredError("Token expired");
			}
			if (response.status === 403) {
				throw new ShabtiAuthenticationError("Authentication failed");
			}
			if (!EXPECTED_CODES.includes(response.status)) {
				const text = await response.text();
				throw new Error(
					`Request failed with status ${response.status}: ${text}`,
				);
			}
			return response;
		};
		const currentToken = this.token;
		try {
			// if we're already refreshing the token, wait for it to finish
			if (this.refreshTask) {
				await this.refreshTask;
			}
			return await doRequest(this.token);
		} catch (error) {
			if (error instanceof ShabtiTokenExpiredError) {
				if (currentToken === this.token) {
					// this means we're probably using an expired token
					if (!this.refreshTask) {
						if (!this.token.refresh_token)
							throw new Error("No refresh token found!");
						this.refreshTask = client.refreshTokenGrant(
							this.openIdConfig,
							this.token.refresh_token,
						);
					}
					this.token = await this.refreshTask;
					this.refreshTask = null;
				} // if we're using a different token from the stored one, it's likely we already did a refresh and we can just rerun
				return await this.makeRequest(method, url, json, files, params);
			} else {
				throw error;
			}
		}
	}

	async createCollection(
		collectionName: string,
		location: string,
		ownerUsername?: string,
	): Promise<string> {
		const res = await this.makeRequest("POST", "collections", {
			collection_name: collectionName,
			location,
			owner_username: ownerUsername,
		});
		const json = (await res.json()) as any;
		return json.collection_id;
	}

	async getCollections(): Promise<AuthzCollectionInfo[]> {
		const res = await this.makeRequest("GET", "collections");
		const json = (await res.json()) as any;
		return json.map(
			(item: any) =>
				new AuthzCollectionInfo(
					item.collection_name,
					item.collection_id,
					item.location,
					new UserInfo(item.owner.username, item.owner.user_id),
				),
		);
	}

	async getUserInfo() {
		const res = await this.makeRequest("GET", "user_info");
		const json = await res.json();
		return json;
	}

	async getPermissions() {
		const res = await this.makeRequest("GET", "permissions");
		const json = (await res.json()) as string[];
		return new Set(json);
	}

	async getCollectionScopes(collectionId: string) {
		const res = await this.makeRequest(
			"GET",
			`collections/${collectionId}/scopes`,
		);
		const json = (await res.json()) as string[];
		return new Set(json);
	}

	// the user's own keys for the providers that take them. Only the last four characters of each
	// are ever sent back, by provider id
	async getApiKeys(): Promise<Record<string, string>> {
		const res = await this.makeRequest("GET", "providers/credentials");
		return (await res.json()) as Record<string, string>;
	}

	async setApiKey(
		providerId: string,
		key: string,
	): Promise<Record<string, string>> {
		const res = await this.makeRequest(
			"PUT",
			`providers/${providerId}/credential`,
			{ key },
		);
		return (await res.json()) as Record<string, string>;
	}

	async deleteApiKey(providerId: string): Promise<Record<string, string>> {
		const res = await this.makeRequest(
			"DELETE",
			`providers/${providerId}/credential`,
		);
		return (await res.json()) as Record<string, string>;
	}
}
