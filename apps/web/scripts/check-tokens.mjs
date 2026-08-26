/**
 * Fail the build if a colour is hardcoded outside the token file.
 *
 * A design system that relies on people remembering is one that half the
 * components ignore, and the drift is invisible until a redesign. This makes
 * the rule mechanical: one file owns colour, and CI enforces it.
 *
 * Deliberately a script rather than an ESLint rule. ESLint sees `.tsx` but not
 * `.css`, and the failure mode being prevented — someone adding a stylesheet
 * or a config with its own palette — lives mostly outside TypeScript.
 *
 * Scope note: a review found the first version walked only `src/`, which left
 * `tailwind.config.ts` unchecked — the one file where a hardcoded colour would
 * un-token the entire system. It now walks the whole workspace.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(fileURLToPath(new URL('.', import.meta.url)));
const root = resolve(here, '..');

/** The one file permitted to contain raw colour values. */
const TOKEN_FILE = join('src', 'styles', 'tokens.css');

// Config and data formats included: a palette in a `.json` or a `.js` config
// walks straight past a check that only reads TypeScript.
const SEARCHED_EXTENSIONS = ['.ts', '.tsx', '.mts', '.js', '.mjs', '.cjs', '.css', '.json'];
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', 'dist', 'coverage', '.turbo']);

// Files that describe the toolchain rather than the product. A version string
// in a lockfile is not a colour, and scanning them is pure noise.
const SKIPPED_FILES = new Set([
  'package-lock.json',
  'tsconfig.tsbuildinfo',
  // This file documents the colour forms it looks for, so scanning it flags
  // its own patterns. Excluding it is not a loophole: it contains no styling
  // and is never imported by the app.
  'check-tokens.mjs',
]);

/**
 * Colour literals, in every form someone reaches for.
 *
 * `oklch` and `lab` are here because they are what modern design tooling
 * emits, and a check that only knows about hex is a check that stops working
 * the moment anyone opens a current colour picker.
 */
const COLOUR_PATTERNS = [
  // #fff, #ffffff, #ffffffff — bounded so a longer hex-ish token is not a hit.
  { name: 'hex', pattern: /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b/g },
  { name: 'rgb()', pattern: /\brgba?\s*\([^)]*\)/g },
  { name: 'hsl()', pattern: /\bhsla?\s*\([^)]*\)/g },
  { name: 'oklch()/oklab()', pattern: /\bokl(?:ch|ab)\s*\([^)]*\)/g },
  { name: 'lab()/lch()', pattern: /\bl(?:ab|ch)\s*\([^)]*\)/g },
  { name: 'colour keyword', pattern: namedColourPattern() },
];

/**
 * A pattern matching CSS colour keywords where they are used as a colour.
 *
 * Restricted to the positions a colour actually appears in — a CSS value, a
 * JSX style object, a Tailwind arbitrary value — because these are ordinary
 * English words. Matching `red` anywhere would flag the word "red" in a
 * comment, and a check that cries wolf gets deleted.
 */
function namedColourPattern() {
  const keywords = [
    'red',
    'blue',
    'green',
    'yellow',
    'orange',
    'purple',
    'pink',
    'brown',
    'black',
    'white',
    'grey',
    'gray',
    'cyan',
    'magenta',
    'teal',
    'navy',
    'olive',
    'maroon',
    'silver',
    'gold',
    'crimson',
    'indigo',
    'violet',
    'rebeccapurple',
  ].join('|');
  // `color: red`, `backgroundColor: 'red'`, `border: 1px solid red`.
  return new RegExp(
    String.raw`(?:^|[;{,])\s*(?:-{2})?[a-zA-Z-]*(?:color|Color|background|Background|fill|stroke|border|outline|shadow)[a-zA-Z-]*\s*:\s*['"\s]*(?:${keywords})\b`,
    'g',
  );
}

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
    if (SKIPPED_FILES.has(entry)) continue;
    if (SEARCHED_EXTENSIONS.some((extension) => entry.endsWith(extension))) {
      found.push(full);
    }
  }
  return found;
}

/**
 * Report whether a line opts out.
 *
 * An escape hatch exists because false positives are inevitable — a git SHA,
 * an element id, a URL fragment all look like a hex colour. Without one, the
 * first person to hit a false positive mangles their string or deletes the
 * check; with one, they leave a marker a reviewer can see and question.
 *
 * @param {string} line - The line being checked.
 * @returns {boolean} Whether it is exempt.
 */
function isExempt(line) {
  return line.includes('allow-hardcoded-colour');
}

const violations = [];

for (const file of walk(root)) {
  const relativePath = relative(root, file);
  if (relativePath === TOKEN_FILE) continue;

  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, index) => {
    if (isExempt(line)) return;
    for (const { name, pattern } of COLOUR_PATTERNS) {
      // `var(--...)` references are the correct form and often share a line
      // with prose about colour, so only literal forms count.
      for (const match of line.matchAll(pattern)) {
        violations.push(`${relativePath}:${index + 1}  ${name}  ${match[0].trim()}`);
      }
    }
  });
}

if (violations.length > 0) {
  console.error(
    `Hardcoded colours found outside ${TOKEN_FILE}:\n\n` +
      violations.map((v) => `  ${v}`).join('\n') +
      `\n\nEvery colour belongs in ${TOKEN_FILE} as a token, referenced by name.` +
      '\nA colour written inline is invisible to a theme switch and to the next redesign.' +
      '\n\nIf this is a false positive — a git SHA, an id, a URL fragment — add' +
      '\n`allow-hardcoded-colour` in a comment on that line.\n',
  );
  process.exit(1);
}

console.log(`No hardcoded colours outside ${TOKEN_FILE.split(sep).join('/')}.`);
