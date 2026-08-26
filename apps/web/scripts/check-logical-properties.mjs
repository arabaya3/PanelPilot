/**
 * Fail the build on a physical direction property.
 *
 * `margin-left` does not flip when `dir="rtl"`; `margin-inline-start` does.
 * One physical property in one component is a component that lays out wrong in
 * Arabic and Hebrew, and the bug surfaces as "the spacing looks odd" long
 * after whoever wrote it moved on.
 *
 * This is added now, while the codebase contains none, because retrofitting
 * RTL is what breaks half a component library: by the time nine more tasks
 * have shipped, the fix is forty overrides rather than one rule.
 *
 * Tailwind's logical utilities are the drop-in replacements:
 *   ml-4  -> ms-4        mr-4  -> me-4
 *   pl-4  -> ps-4        pr-4  -> pe-4
 *   left-0 -> start-0    right-0 -> end-0
 *   text-left -> text-start   text-right -> text-end
 *   border-l -> border-s      border-r -> border-e
 *   rounded-l -> rounded-s    rounded-r -> rounded-e
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(fileURLToPath(new URL('.', import.meta.url)));
const root = resolve(here, '..');

const SEARCHED_EXTENSIONS = ['.tsx', '.ts', '.css'];
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', 'dist', 'coverage']);
const SKIPPED_FILES = new Set(['check-logical-properties.mjs']);

/**
 * Physical properties and their logical replacements.
 *
 * Class names are matched with a boundary on both sides so `ml-4` is a hit and
 * `html-4` is not. CSS declarations are matched at a property position, so the
 * word "left" in prose does not fire.
 */
const FORBIDDEN = [
  { pattern: /\b(?:ml|mr)-(?:\d+|px|auto)\b/g, use: 'ms-* / me-*' },
  { pattern: /\b(?:pl|pr)-(?:\d+|px)\b/g, use: 'ps-* / pe-*' },
  { pattern: /\btext-(?:left|right)\b/g, use: 'text-start / text-end' },
  { pattern: /\b(?:border|rounded)-[lr]\b/g, use: 'border-s|e / rounded-s|e' },
  {
    pattern: /(?:^|[;{])\s*(?:margin|padding)-(?:left|right)\s*:/g,
    use: '-inline-start / -inline-end',
  },
  { pattern: /(?:^|[;{])\s*border-(?:left|right)(?:-[a-z]+)?\s*:/g, use: 'border-inline-*' },
  { pattern: /(?:^|[;{])\s*text-align\s*:\s*(?:left|right)\b/g, use: 'text-align: start / end' },
];

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

const violations = [];

for (const file of walk(join(root, 'src'))) {
  const relativePath = relative(root, file);
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, index) => {
    // The same escape hatch as the colour check: a genuine one-off leaves a
    // marker a reviewer can question, rather than the rule being deleted.
    if (line.includes('allow-physical-property')) return;
    for (const { pattern, use } of FORBIDDEN) {
      for (const match of line.matchAll(pattern)) {
        violations.push(`${relativePath}:${index + 1}  ${match[0].trim()}  → use ${use}`);
      }
    }
  });
}

if (violations.length > 0) {
  console.error(
    'Physical direction properties found:\n\n' +
      violations.map((v) => `  ${v}`).join('\n') +
      '\n\nThese do not flip under dir="rtl", so the component lays out wrong in' +
      '\nArabic and Hebrew. Use the logical equivalent, which flips for free.' +
      '\n\nIf a property genuinely must not flip — an icon that points at a' +
      '\nphysical button, say — add `allow-physical-property` in a comment on' +
      '\nthat line.\n',
  );
  process.exit(1);
}

console.log('No physical direction properties outside the documented exceptions.');
