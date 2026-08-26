/**
 * Types shared between the API and any TypeScript consumer.
 *
 * `api.generated.ts` is produced from the backend's OpenAPI schema by
 * `npm run generate` and must never be hand-edited — regenerate it instead.
 * The `shared types drift` CI job regenerates it and fails if the checked-in
 * copy differs, so a stale file cannot reach main.
 * Types that have no server counterpart (UI-only view models) go in this file.
 */
export type * from './api.generated';
