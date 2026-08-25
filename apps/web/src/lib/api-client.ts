/**
 * Typed client for the PanelPilot API.
 *
 * The only module in the frontend that knows the API's base URL or wire
 * format. Components call the exported functions; they never call `fetch`
 * directly, so a route change or auth-header change is a one-file edit.
 *
 * Request and response types come from `@panelpilot/shared-types` and are
 * generated from the backend's OpenAPI schema — do not hand-write them here.
 */
import type { paths } from '@panelpilot/shared-types';

/** Base URL of the API, from `NEXT_PUBLIC_API_BASE_URL`. */
export function getApiBaseUrl(): string {
  throw new Error('not implemented');
}

/**
 * Issue a typed request against a documented API path.
 *
 * `paths` is an empty placeholder until `npm run generate` populates
 * `api.generated.ts` from the backend's OpenAPI schema, so `keyof paths` is
 * currently `never` and this function cannot yet be called — deliberately.
 * Once the schema is real, the return type narrows from `unknown` to the
 * response body declared for that path, and the path parameter becomes a
 * generic so the two stay tied together.
 *
 * @param _path - An API path present in the generated schema.
 * @param _init - Standard fetch options; the auth header is added here.
 * @returns The parsed response body.
 * @throws If the response status is not 2xx.
 */
export function apiFetch(_path: keyof paths, _init?: RequestInit): Promise<unknown> {
  throw new Error('not implemented');
}
