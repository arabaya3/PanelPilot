import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import type { Config } from 'tailwindcss';
import { describe, expect, it } from 'vitest';

/**
 * Every size and spacing class in the source names a key the config defines.
 *
 * The config replaces Tailwind's `spacing` scale with the design tokens, and
 * width, height, padding, margin and gap all inherit from it. A class naming
 * a key that is not there does not fail anywhere: Tailwind generates nothing
 * for it and the element falls back to its content size. `w-64` on the
 * history sidebar went that way, and the sidebar grew to its longest title
 * and squeezed the conversation beside it to a sliver; `max-h-40` left photo
 * previews uncapped. Nothing but a browser showed it.
 */

const SRC = resolve(process.cwd(), 'src');

// The config the build uses, loaded rather than copied: a copy here would
// agree with itself after the real one changed.
const config = ((await import(resolve(process.cwd(), 'tailwind.config.ts'))) as { default: Config })
  .default;

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((name) => {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) return name === '__tests__' ? [] : sourceFiles(path);
    return path.endsWith('.tsx') || path.endsWith('.ts') ? [path] : [];
  });
}

type Scale = Record<string, unknown>;

const theme = config.theme as { spacing: Scale; extend?: Record<string, Scale> };
const spacing = Object.keys(theme.spacing);
const extended = (name: string): string[] => Object.keys(theme.extend?.[name] ?? {});

// Utility prefix -> the keys it accepts. Longest prefixes first, so `min-w`
// is not read as `m` with a value of `in-w-…`.
const SCALES: [string, string[]][] = [
  ['min-w', [...spacing, ...extended('minWidth')]],
  ['min-h', [...spacing, ...extended('minHeight')]],
  ['max-h', [...spacing, ...extended('maxHeight')]],
  ['gap-x', spacing],
  ['gap-y', spacing],
  ['space-x', spacing],
  ['space-y', spacing],
  ['gap', spacing],
  ['size', spacing],
  ['w', [...spacing, ...extended('width')]],
  ['h', [...spacing, ...extended('height')]],
  ...['p', 'px', 'py', 'pt', 'pb', 'ps', 'pe', 'm', 'mx', 'my', 'mt', 'mb', 'ms', 'me'].map(
    (prefix): [string, string[]] => [prefix, spacing],
  ),
];

// A numeric key only: `w-full`, `h-screen` and `p-[3px]` are not on a scale.
const UTILITY =
  /(?<![\w-])-?(min-w|min-h|max-h|gap-x|gap-y|space-x|space-y|gap|size|w|h|px|py|pt|pb|ps|pe|p|mx|my|mt|mb|ms|me|m)-(\d+(?:\.\d+)?)(?![\w.-])/g;

describe('tailwind classes', () => {
  it('finds classes to check', () => {
    // A scanner that matches nothing would pass every file.
    const all = sourceFiles(SRC).flatMap((file) => [
      ...readFileSync(file, 'utf8').matchAll(UTILITY),
    ]);
    expect(all.length).toBeGreaterThan(50);
  });

  it('uses only keys the config defines', () => {
    const missing: string[] = [];
    for (const file of sourceFiles(SRC)) {
      for (const match of readFileSync(file, 'utf8').matchAll(UTILITY)) {
        const [whole, prefix = '', key = ''] = match;
        const keys = SCALES.find(([name]) => name === prefix)?.[1] ?? [];
        if (!keys.includes(key)) missing.push(`${relative(SRC, file)}: ${whole.trim()}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it('never uses a bare `rounded`', () => {
    // The radius scale is replaced too and has no DEFAULT, so `rounded` alone
    // generates nothing; name the token (`rounded-sm`, `rounded-md`).
    const bare = sourceFiles(SRC).filter((file) =>
      /(?<![\w-])rounded(?![\w-])/.test(
        readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''),
      ),
    );
    expect(bare.map((file) => relative(SRC, file))).toEqual([]);
  });
});
