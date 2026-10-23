'use client';

import { useLocale } from 'next-intl';
import { useCallback } from 'react';

import { useIsolate, useIsolatingTranslations, useRightToLeft } from '@/components/design/bidi';
import type { DesignNote, Refusal } from '@/lib/design';

/** Params a sentence counts with, so its plural form agrees. */
const NUMERIC_PARAMS = new Set(['count']);

/** Params whose value is itself a code the page translates. */
const TRANSLATED_PARAMS = { kind: 'kind', starter: 'starter' } as const;

/** Params named in English whose sibling param keys their translation. */
const KEYED_PARAMS = {
  market: { code: 'market_code', namespace: 'markets' },
  regulation: { code: 'regulation_code', namespace: 'regulation' },
} as const;

/**
 * Render design notes in the reader's language.
 *
 * Each note carries a code and its values; the code picks the sentence from
 * `design.note`, and a load kind or starter named in it is translated too.
 * A code the page has no sentence for (a newer API) falls back to the
 * note's English text rather than showing a key. In Arabic and Hebrew each
 * value is isolated from the sentence around it (see `bidi.ts`).
 */
export function useNoteText(): (note: DesignNote) => string {
  const t = useIsolatingTranslations('design');
  return (note) => {
    const key = `note.${note.code}`;
    if (!t.has(key)) return note.text;
    const values: Record<string, string | number> = { ...(note.params ?? {}) };
    for (const [param, namespace] of Object.entries(TRANSLATED_PARAMS)) {
      const value = values[param];
      if (typeof value === 'string' && t.has(`${namespace}.${value}`)) {
        values[param] = t(`${namespace}.${value}`);
      }
    }
    // A market and its code are named in English (the fallback) and keyed
    // by code; the key says them in the reader's language.
    for (const [param, { code, namespace }] of Object.entries(KEYED_PARAMS)) {
      const key = values[code];
      if (typeof key === 'string' && t.has(`${namespace}.${key}`)) {
        values[param] = t(`${namespace}.${key}`);
      }
    }
    for (const param of NUMERIC_PARAMS) {
      const value = Number(values[param]);
      if (Number.isFinite(value)) values[param] = value;
    }
    return t(key, values);
  };
}

/** A request's project fields, each entered under `design.field.<name>`. */
const INFO_FIELDS = new Set(['name', 'number', 'customer', 'consultant', 'contractor']);

/** A board's fields (its supply's included), by their form label in `design.field`. */
const BOARD_FIELDS: Readonly<Record<string, string>> = {
  name: 'board',
  location: 'location',
  fed_from: 'fedFrom',
  feeder_length_m: 'feederLength',
  voltage_v: 'voltage',
  phases: 'phases',
  fault_level_ka: 'faultLevel',
  earthing: 'earthing',
  earth_loop_ohm: 'earthLoop',
};

/** A load row's fields, by their form label in `design.field`. */
const LOAD_FIELDS: Readonly<Record<string, string>> = {
  description: 'description',
  load: 'load',
  power_kw: 'power',
  phases: 'loadPhases',
  power_factor: 'powerFactor',
  controlled: 'controlled',
  starter: 'starter',
  length_m: 'length',
};

/** What `fieldLabelOf` needs of a `design` translator. */
type Labels = {
  (key: string, values?: Record<string, string | number>): string;
  has: (key: string) => boolean;
};

/**
 * Name the field at an API path by the labels the form shows it under:
 * `request.boards[0].loads[1].description` is "Board 1 › Load 2 › Circuit".
 * A part the page has no label for is shown as the API names it, so a field
 * the page does not know is still findable.
 */
export function fieldLabelOf(path: string, t: Labels): string {
  const parts = path
    .replace(/\[(\d+)\]/g, '.$1')
    .split('.')
    .filter((part) => part !== '');
  // The envelope a save or export wraps the project in means nothing on the form.
  while (parts[0] === 'request' || parts[0] === 'project' || parts[0] === 'body') parts.shift();
  const crumbs: string[] = [];
  let scope: 'top' | 'info' | 'board' | 'load' = 'top';
  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index] ?? '';
    const next = parts[index + 1];
    const position = next !== undefined && /^\d+$/.test(next) ? Number(next) + 1 : null;
    if (scope === 'top' && part === 'info') {
      scope = 'info';
      continue;
    }
    if (scope === 'top' && part === 'boards' && position !== null) {
      crumbs.push(t('fieldPath.board', { n: position }));
      scope = 'board';
      index += 1;
      continue;
    }
    if (scope === 'board' && part === 'loads' && position !== null) {
      crumbs.push(t('fieldPath.load', { n: position }));
      scope = 'load';
      index += 1;
      continue;
    }
    // A board's supply is entered on the board itself.
    if (scope === 'board' && part === 'supply') continue;
    const field =
      scope === 'info' || scope === 'top'
        ? INFO_FIELDS.has(part)
          ? part
          : undefined
        : scope === 'board'
          ? BOARD_FIELDS[part]
          : LOAD_FIELDS[part];
    const label =
      field !== undefined && t.has(`field.${field}`)
        ? t(`field.${field}`)
        : scope === 'top' && part === 'note'
          ? t('projects.note')
          : parts.slice(index).join('.');
    crumbs.push(label);
    break;
  }
  return crumbs.length > 0 ? crumbs.join(' › ') : path;
}

/**
 * Say why a design call did not succeed, in the reader's language.
 *
 * A 422 naming fields the server does not accept says which. A refusal with a code the page knows is said from `design.errors`, prefixed
 * with what it is about (`Pump: ...`) when the server named it; one without
 * falls back to the server's English reason, and anything else to the
 * generic error. A field at fault is named by its label on the form.
 *
 * The function keeps its identity across renders (it changes only with the
 * language). Callers list it as an effect dependency -- the projects panel
 * refetches whenever its `refresh` changes -- and a fresh closure on every
 * render made that effect fire on every render: a failed fetch set a message,
 * the message re-rendered, the new closure refetched, and the panel looped
 * until the per-address design limit cut the whole page off.
 */
export function useOutcomeText(): (outcome: { kind: string }) => string {
  const t = useIsolatingTranslations('design');
  const rtl = useRightToLeft();
  const isolate = useIsolate();
  const listSeparator = useLocale() === 'ar' ? '، ' : ', ';
  return useCallback(
    (outcome) => {
      if (outcome.kind !== 'refused') return t('error');
      const refusal = outcome as Refusal;
      const labelOf = (path: string) => fieldLabelOf(path, t);
      // A field the server does not accept is named, so it can be found and removed.
      if (refusal.unknownFields && refusal.unknownFields.length > 0) {
        return t('errors.unknown_fields', {
          fields: refusal.unknownFields.join(', '),
          count: refusal.unknownFields.length,
        });
      }
      // A strict 422 (`request_invalid`) names each field at fault and its limit.
      const limits = [
        ...(refusal.tooLong ?? []).map(({ path, max }) =>
          t('errors.too_long', { field: labelOf(path), max }),
        ),
        ...(refusal.tooLarge ?? []).map(({ path, max }) =>
          t('errors.too_large', { field: labelOf(path), max }),
        ),
      ];
      if (limits.length > 0) return limits.join(' ');
      // Any other strict 422: the general sentence, then what it named. The
      // server's own reason is English, so it follows only an English sentence.
      if (refusal.code === 'request_invalid') {
        const sentence = t('errors.request_invalid');
        if (!rtl) return `${sentence} ${refusal.detail}`.trim();
        const fields = (refusal.invalidFields ?? []).map(labelOf);
        return fields.length > 0
          ? `${sentence} ${t('errors.request_invalid_fields', { fields: fields.join(listSeparator) })}`
          : sentence;
      }
      const key = `errors.${refusal.code ?? ''}`;
      if (refusal.code && t.has(key)) {
        const { subject, ...values } = refusal.params ?? {};
        const sentence = t(key, values);
        return subject ? `${isolate(subject)}: ${sentence}` : sentence;
      }
      return refusal.detail || t('error');
    },
    [t, rtl, isolate, listSeparator],
  );
}
