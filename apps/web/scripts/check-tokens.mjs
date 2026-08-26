/**
 * Fail the build if a colour is hardcoded outside the token file.
 *
 * A design system that relies on people remembering is one that half the
 * components ignore, and the drift is invisible until a redesign. This makes
 * the rule mechanical: one file owns colour, and CI enforces it.
 *
 * Deliberately a script rather than an ESLint rule. ESLint sees `.tsx` but
 * not `.css`, and the failure mode being prevented — someone adding a
 * stylesheet with its own palette — lives mostly in CSS.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(fileURLToPath(new URL('.', import.meta.url)));
const root = resolve(here, '..');

/** The one file permitted to contain raw colour values. */
const TOKEN_FILE = join('src', 'styles', 'tokens.css');

const SEARCHED_EXTENSIONS = ['.ts', '.tsx', '.css'];
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', 'dist', 'coverage']);

// #fff, #ffffff, #ffffffff — and the rgb()/hsl() forms, which are the obvious
// way around a check that only looks for hashes.
const HARDCODED_COLOUR = /(#[0-9a-fA-F]{3,8}\b)|(\brgba?\s*\([^)]*\))|(\bhsla?\s*\([^)]*\))/g;

/**
 * List every file worth checking.
 *
 * @param {string} directory - Where to start.
 * @returns {string[]} Absolute paths.
 */
function walk(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const full = join(directory, entry);
    if (statSync(full).isDirectory()) {
      if (SKIPPED_DIRECTORIES.has(entry)) continue;
      found.push(...walk(full));
      continue;
    }
    if (SEARCHED_EXTENSIONS.some((extension) => entry.endsWith(extension))) {
      found.push(full);
    }
  }
  return found;
}

const violations = [];

for (const file of walk(join(root, 'src'))) {
  const relativePath = relative(root, file);
  if (relativePath === TOKEN_FILE) continue;

  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, index) => {
    // `var(--...)` references are the correct form and often sit on the same
    // line as prose mentioning a colour, so only the literal forms count.
    for (const match of line.matchAll(HARDCODED_COLOUR)) {
      violations.push(`${relativePath}:${index + 1}  ${match[0]}`);
    }
  });
}

if (violations.length > 0) {
  console.error(
    `Hardcoded colours found outside ${TOKEN_FILE}:\n\n` +
      violations.map((v) => `  ${v}`).join('\n') +
      `\n\nEvery colour belongs in ${TOKEN_FILE} as a token, referenced by name.` +
      '\nA colour written inline is invisible to a theme switch and to the next redesign.\n',
  );
  process.exit(1);
}

console.log(`No hardcoded colours outside ${TOKEN_FILE.split(sep).join('/')}.`);
