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
 * @param _path - An API path present in the generated schema.
 * @param _init - Standard fetch options; the auth header is added here.
 * @returns The parsed response body.
 * @throws If the response status is not 2xx.
 */
export async function apiFetch<P extends keyof paths>(
  _path: P,
  _init?: RequestInit,
): Promise<unknown> {
  throw new Error('not implemented');
}
