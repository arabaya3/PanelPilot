/**
 * Fail the build on a pattern that overflows a phone.
 *
 * The primary usage context is a tablet or phone browser next to a live panel,
 * not the 1440px desktop the team builds on. An engineer who has to scroll
 * sideways to read a fault code is an engineer reading half of it.
 *
 * A static check rather than a rendered one, deliberately. jsdom performs no
 * layout — every element reports zero width and a zeroed bounding box — so a
 * test asserting "nothing exceeds 360px" there would pass whatever the markup
 * said, which is worse than no test. Real measurement belongs in a browser
 * run; what belongs here is the class of markup that causes the overflow in
 * the first place, which is mechanically checkable and catches it before it is
 * ever rendered.
 *
 * The rules below are each a pattern that cannot fit a 360px viewport, not a
 * matter of taste:
 *
 *   - A fixed width at or above the small-phone breakpoint. `w-[400px]` is
 *     wider than the screen before any padding.
 *   - A minimum width at or above it. `min-w-[500px]` cannot shrink, which is
 *     the whole problem with a wide table on a phone.
 *   - An unwrapped table. A multi-column table does not shrink to fit; it must
 *     either sit in a horizontally scrollable container or have a stacked
 *     fallback below the threshold.
 *
 * Deliberately not flagged: `whitespace-nowrap`, which is correct on a short
 * token like a part number and would produce mostly false alarms; and
 * `overflow-x-auto`, which is the fix rather than the problem.
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = resolve(fileURLToPath(new URL('.', import.meta.url)));
const root = resolve(here, '..');

/** The narrowest viewport the product supports. A small phone. */
const SMALL_PHONE = 360;

const SEARCHED_EXTENSIONS = ['.tsx', '.ts', '.css'];
const SKIPPED_DIRECTORIES = new Set(['node_modules', '.next', 'dist', 'coverage']);
const SKIPPED_FILES = new Set(['check-responsive.mjs']);

/**
 * Walk a directory for source files.
 *
 * @param {string} directory - Where to start.
 * @returns {string[]} Absolute paths of every file to check.
 */
function walk(directory) {
  const found = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) {
      if (SKIPPED_DIRECTORIES.has(entry)) continue;
      found.push(...walk(path));
      continue;
    }
    if (SKIPPED_FILES.has(entry)) continue;
    if (SEARCHED_EXTENSIONS.some((extension) => entry.endsWith(extension))) {
      found.push(path);
    }
  }
  return found;
}

/**
 * Find fixed or minimum widths that cannot fit a small phone.
 *
 * @param {string} source - The file's contents.
 * @returns {{ line: number, text: string, why: string }[]} What was found.
 *
 * Only `px` values are judged. A `w-[50%]` or `w-[20rem]` is either relative
 * or small enough that flagging it would be guessing, and a check that guesses
 * is one people start suppressing.
 */
function findWideBoxes(source) {
  const problems = [];
  const pattern = /\b(min-w|w)-\[(\d+(?:\.\d+)?)px\]/g;

  source.split('\n').forEach((line, index) => {
    for (const match of line.matchAll(pattern)) {
      const width = Number(match[2]);
      if (width < SMALL_PHONE) continue;
      problems.push({
        line: index + 1,
        text: match[0],
        why:
          match[1] === 'min-w'
            ? `min-width ${width}px cannot shrink below a ${SMALL_PHONE}px viewport`
            : `width ${width}px exceeds a ${SMALL_PHONE}px viewport`,
      });
    }
  });

  return problems;
}

/**
 * Find tables that can neither scroll nor stack.
 *
 * @param {string} source - The file's contents.
 * @returns {{ line: number, text: string, why: string }[]} What was found.
 *
 * A table is accepted when an ancestor within the same file scrolls
 * horizontally, or when the file provides a stacked fallback — signalled by a
 * responsive `hidden`/`block` pair, which is how a stacked card view is
 * normally gated. Both are real fixes; neither is assumed.
 */
function findUnwrappedTables(source) {
  const problems = [];
  if (!source.includes('<table')) return problems;

  const scrolls = source.includes('overflow-x-auto') || source.includes('overflow-x-scroll');
  const stacks = /\b(hidden|block|flex)\s+(sm|md|lg):(table|block|hidden|flex)\b/.test(source);
  if (scrolls || stacks) return problems;

  source.split('\n').forEach((line, index) => {
    if (line.includes('<table')) {
      problems.push({
        line: index + 1,
        text: '<table>',
        why:
          'a multi-column table cannot shrink to a phone. Wrap it in an ' +
          'overflow-x-auto container, or provide a stacked card view below the ' +
          'tablet breakpoint',
      });
    }
  });

  return problems;
}

const failures = [];
for (const file of walk(root)) {
  const source = readFileSync(file, 'utf8');
  const relativePath = relative(root, file);
  for (const problem of [...findWideBoxes(source), ...findUnwrappedTables(source)]) {
    failures.push(`${relativePath}:${problem.line}  ${problem.text}  — ${problem.why}`);
  }
}

if (failures.length > 0) {
  console.error('Markup that cannot fit a small phone:\n');
  for (const failure of failures) console.error(`  ${failure}`);
  console.error(
    `\nThe product is used on a tablet or phone next to a panel. Horizontal ` +
      `scrolling to read a fault code means reading half of it.`,
  );
  process.exit(1);
}

console.log(`No markup that overflows a ${SMALL_PHONE}px viewport.`);
