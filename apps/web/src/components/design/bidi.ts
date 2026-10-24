'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useCallback, useMemo } from 'react';

import { directionOf, toLocale } from '@/i18n/config';

/**
 * Bidirectional isolation for values put into translated sentences.
 *
 * In Arabic or Hebrew, a value such as a revision number, a cable `3G1.5`, a
 * formula `12 × 60 W = 0.72 kW`, an email address or an English name is laid
 * out by the Unicode bidi algorithm together with the sentence around it, so
 * its digits and punctuation can jump to the wrong side of the words next to
 * them. Wrapping each value in FIRST STRONG ISOLATE … POP DIRECTIONAL
 * ISOLATE lays it out on its own, in its own direction, and then places it
 * as one unit. The marks are invisible.
 *
 * Done where sentences are formatted (here), not at each call site, so a
 * sentence cannot be added without it. Only in a right-to-left locale: in
 * English the values already read in the sentence's direction.
 */
export const FSI = '⁨';
export const PDI = '⁩';

/**
 * LEFT-TO-RIGHT ISOLATE: opens a run that is laid out left to right whatever
 * it starts with, for text that is always English (a catalogue citation).
 */
export const LRI = '⁦';

/** Always-English text isolated as a left-to-right run, its characters unchanged. */
export function isolateLtr(value: string): string {
  return value === '' ? value : `${LRI}${value}${PDI}`;
}

/** One piece of a sentence: plain text, or a run `isolateLtr` marked. */
export type TextPart = { text: string; ltr: boolean };

/**
 * A sentence split at each run `isolateLtr` marked, the runs without their
 * marks and otherwise unchanged, so a page can lay each one out as a unit
 * (`<bdi dir="ltr">`) that a line break cannot scatter across the sentence.
 */
export function splitLtrRuns(sentence: string): TextPart[] {
  const parts: TextPart[] = [];
  let rest = sentence;
  for (;;) {
    const start = rest.indexOf(LRI);
    const end = start === -1 ? -1 : rest.indexOf(PDI, start + 1);
    if (start === -1 || end === -1) break;
    if (start > 0) parts.push({ text: rest.slice(0, start), ltr: false });
    parts.push({ text: rest.slice(start + LRI.length, end), ltr: true });
    rest = rest.slice(end + PDI.length);
  }
  if (rest !== '') parts.push({ text: rest, ltr: false });
  return parts;
}

/** A value isolated from the text around it. */
export function isolate(value: string): string {
  return value === '' ? value : `${FSI}${value}${PDI}`;
}

type Values = Record<string, unknown>;

/**
 * The values with each one a sentence places as text isolated. An argument
 * the message formats itself (`{count, plural, …}`, `{source, select, …}`,
 * `{n, number}`) is left as it is, since ICU needs the raw value for it.
 */
export function isolateValues<V extends Values | undefined>(message: string, values: V): V {
  if (values === undefined) return values;
  const isolated: Values = {};
  for (const [name, value] of Object.entries(values)) {
    const formatted = new RegExp(`\\{\\s*${escapeRegExp(name)}\\s*,`).test(message);
    isolated[name] =
      !formatted && (typeof value === 'string' || typeof value === 'number')
        ? isolate(String(value))
        : value;
  }
  return isolated as V;
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

type Namespace = Parameters<typeof useTranslations>[0];

/**
 * `useTranslations`, with every interpolated value isolated in a
 * right-to-left locale (see above). The same function otherwise, including
 * `has`, `raw` and `rich`.
 */
export function useIsolatingTranslations(namespace: Namespace) {
  const t = useTranslations(namespace);
  const rtl = useRightToLeft();
  return useMemo(() => {
    if (!rtl) return t;
    // The translator's own signature is generic over every key; values are
    // only ever rewrapped here, so it is called through a plain view of it.
    type Plain = (key: string, values?: Values, formats?: unknown) => string;
    const plain = t as unknown as Plain & {
      has: (key: string) => boolean;
      raw: (key: string) => unknown;
      rich: Plain;
      markup: Plain;
    };
    const messageOf = (key: string): string => {
      if (!plain.has(key)) return '';
      const raw = plain.raw(key);
      return typeof raw === 'string' ? raw : '';
    };
    const wrapped = ((key: string, values?: Values, formats?: unknown) =>
      plain(key, isolateValues(messageOf(key), values), formats)) as Plain;
    return Object.assign(wrapped, {
      has: plain.has,
      raw: plain.raw,
      rich: (key: string, values?: Values, formats?: unknown) =>
        plain.rich(key, isolateValues(messageOf(key), values), formats),
      markup: (key: string, values?: Values, formats?: unknown) =>
        plain.markup(key, isolateValues(messageOf(key), values), formats),
    });
  }, [t, rtl]);
}

/** Whether the page reads right to left. */
export function useRightToLeft(): boolean {
  return directionOf(toLocale(useLocale())) === 'rtl';
}

/** `isolate` in a right-to-left locale; the value as it is otherwise. */
export function useIsolate(): (value: string) => string {
  const rtl = useRightToLeft();
  return useCallback((value: string) => (rtl ? isolate(value) : value), [rtl]);
}
