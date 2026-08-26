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
 * A Tailwind scale value: `4`, `0.5`, `px`, `auto`, `full`, or an arbitrary
 * `[3px]`. Spelled out once because getting it wrong is quiet — the earlier
 * `\d+` matched only the `ml-0` of `ml-0.5`, so the rule fired but the error
 * named a class that did not appear in the file.
 */
const SCALE = String.raw`(?:\d+(?:\.\d+)?|px|auto|full|\[[^\]]+\])`;

/**
 * Physical properties and their logical replacements.
 *
 * Class names are matched with a boundary on both sides so `ml-4` is a hit and
 * `html-4` is not. CSS declarations are matched at a property position, so the
 * word "left" in prose does not fire.
 */
const FORBIDDEN = [
  { pattern: new RegExp(String.raw`\b(?:ml|mr)-${SCALE}`, 'g'), use: 'ms-* / me-*' },
  { pattern: new RegExp(String.raw`\b(?:pl|pr)-${SCALE}`, 'g'), use: 'ps-* / pe-*' },
  // Positioning. `left-0` on an RTL layout pins an element to the wrong edge
  // of the screen — one of the two failures that actually bites in practice.
  { pattern: new RegExp(String.raw`\b(?:left|right)-${SCALE}`, 'g'), use: 'start-* / end-*' },
  { pattern: /\btext-(?:left|right)\b/g, use: 'text-start / text-end' },
  // The other one that bites: a floated element flips side with direction.
  { pattern: /\b(?:float|clear)-(?:left|right)\b/g, use: 'float-start / float-end' },
  { pattern: /\b(?:border|rounded)-[lr]\b/g, use: 'border-s|e / rounded-s|e' },
  // Corner radii. These matter for the chat bubbles this UI is heading toward:
  // a bubble with one square corner marks the speaker, and in RTL it marks the
  // wrong one.
  { pattern: /\brounded-(?:tl|tr|bl|br)\b/g, use: 'rounded-ss|se|es|ee' },
  { pattern: /\borigin-(?:top-|bottom-)?(?:left|right)\b/g, use: 'a logical origin' },
  {
    pattern: /(?:^|[;{])\s*(?:margin|padding)-(?:left|right)\s*:/g,
    use: '-inline-start / -inline-end',
  },
  { pattern: /(?:^|[;{])\s*border-(?:left|right)(?:-[a-z]+)?\s*:/g, use: 'border-inline-*' },
  { pattern: /(?:^|[;{])\s*(?:left|right)\s*:/g, use: 'inset-inline-start / -end' },
  { pattern: /(?:^|[;{])\s*float\s*:\s*(?:left|right)\b/g, use: 'float: inline-start / end' },
  {
    pattern: /(?:^|[;{])\s*border-(?:top|bottom)-(?:left|right)-radius\s*:/g,
    use: 'border-*-*-radius logical form',
  },
  {
    pattern: /(?:^|[;{])\s*scroll-(?:margin|padding)-(?:left|right)\s*:/g,
    use: '-inline-start / -inline-end',
  },
  { pattern: /(?:^|[;{])\s*text-align\s*:\s*(?:left|right)\b/g, use: 'text-align: start / end' },
  // Inline styles bypass every class-name rule above, and a camelCase
  // `marginLeft` in a style object is the easiest way to reintroduce exactly
  // what this script exists to prevent.
  {
    pattern: /\b(?:margin|padding|border)(?:Left|Right)[A-Za-z]*\s*:/g,
    use: 'the Inline-start/end form',
  },
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
