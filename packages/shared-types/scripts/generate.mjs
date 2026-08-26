/**
 * Regenerate `src/api.generated.ts` from the API's OpenAPI schema.
 *
 * The schema comes from the application object, not from a running server, so
 * refreshing types needs no backend, no database, and no ports. CI runs the
 * same two steps and fails if the result differs from what is checked in — so
 * a local run and the drift check cannot disagree.
 *
 * Requires the API's Python environment on PATH (or PANELPILOT_PYTHON set).
 */

import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(fileURLToPath(new URL('.', import.meta.url)));
const apiDir = resolve(here, '../../../apps/api');
const outFile = resolve(here, '../src/api.generated.ts');

const python = process.env.PANELPILOT_PYTHON ?? 'python';

const DUMP = [
  'import json',
  'from app.main import create_app',
  'print(json.dumps(create_app().openapi()))',
].join('; ');

// Settings refuses to load without these. Nothing here is contacted — the
// schema is built from the route signatures alone — so they are placeholders,
// deliberately not real values.
const env = {
  ...process.env,
  DATABASE_URL:
    process.env.DATABASE_URL ??
    'postgresql+psycopg://placeholder:placeholder@localhost:5432/placeholder',
  OPENSEARCH_URL: process.env.OPENSEARCH_URL ?? 'http://localhost:9200',
  REDIS_URL: process.env.REDIS_URL ?? 'redis://localhost:6379/0',
  ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY ?? 'local-placeholder',
  JWT_SECRET: process.env.JWT_SECRET ?? 'local-placeholder-secret-at-least-32-bytes',
};

const schema = execFileSync(python, ['-c', DUMP], {
  cwd: apiDir,
  env,
  encoding: 'utf8',
  maxBuffer: 32 * 1024 * 1024,
});

const schemaFile = join(mkdtempSync(join(tmpdir(), 'panelpilot-openapi-')), 'openapi.json');
writeFileSync(schemaFile, schema, 'utf8');

// Run the CLI's JS entrypoint under node rather than the `.bin` shim.
//
// Two Windows traps this avoids. `shell: true` re-splits arguments on spaces,
// and this repo can live under a path containing one — which silently wrote
// the output to a truncated filename instead of failing, leaving a stale file
// that looked freshly generated. Without a shell, spawning the `.cmd` shim
// fails outright with EINVAL. Invoking `bin/cli.js` sidesteps both.
// Resolved from the package's own manifest rather than hardcoded, so an
// upgrade that moves the entrypoint fails loudly here instead of silently.
const pkgDir = resolve(
  fileURLToPath(import.meta.resolve('openapi-typescript/package.json')),
  '..',
);
const cli = resolve(
  pkgDir,
  JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf8')).bin['openapi-typescript'],
);

execFileSync(process.execPath, [cli, schemaFile, '-o', outFile], {
  stdio: 'inherit',
  shell: false,
});
