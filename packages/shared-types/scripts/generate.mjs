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

/**
 * Resolve a package's CLI entrypoint to an absolute path.
 *
 * Read from the package's own manifest rather than hardcoded, so an upgrade
 * that moves the entrypoint fails loudly here instead of silently running the
 * wrong thing. `bin` is a string for single-command packages and an object
 * keyed by command name for the rest; both shapes occur in this repo's own
 * dependencies.
 *
 * @param {string} name Package name.
 * @returns {string} Absolute path to the CLI's JS entrypoint.
 */
function resolveCli(name) {
  const dir = resolve(fileURLToPath(import.meta.resolve(`${name}/package.json`)), '..');
  const { bin } = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const entry = typeof bin === 'string' ? bin : bin[name];
  if (!entry) {
    throw new Error(`${name} declares no CLI entrypoint for '${name}'`);
  }
  return resolve(dir, entry);
}

/**
 * Run a CLI under this node process.
 *
 * Two Windows traps this avoids. `shell: true` re-splits arguments on spaces,
 * and this repo can live under a path containing one — which silently wrote
 * the output to a truncated filename instead of failing, leaving a stale file
 * that looked freshly generated. Without a shell, spawning the `.cmd` shim
 * fails outright with EINVAL. Invoking the JS entrypoint sidesteps both.
 *
 * @param {string} name Package name.
 * @param {string[]} argv Arguments to pass.
 */
function runCli(name, argv) {
  execFileSync(process.execPath, [resolveCli(name), ...argv], {
    stdio: 'inherit',
    shell: false,
  });
}

runCli('openapi-typescript', [schemaFile, '-o', outFile]);

// Format the result, for the same reason the rest of the repo is formatted:
// the checked-in file is read and diffed by people. Doing it here rather than
// anywhere else keeps the two CI jobs from contradicting each other —
// `web (format)` rejects an unformatted generated file, while
// `shared types drift` would reject a formatted one if the generator emitted
// it raw.
runCli('prettier', ['--write', outFile]);
