# @panelpilot/shared-types

The single source of truth for the API contract on the TypeScript side.

- `src/api.generated.ts` — generated from the backend's OpenAPI schema. Never
  edit by hand; run `npm run generate` against a running API.
- `src/index.ts` — re-exports the generated types, plus any hand-written type
  that has no server counterpart.

The backend's Pydantic schemas in `apps/api/app/models/schemas/` are the
upstream source. A contract change starts there, not here.
