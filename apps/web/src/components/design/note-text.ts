'use client';

import { useTranslations } from 'next-intl';

import type { DesignNote, Refusal } from '@/lib/design';

/** Params a sentence counts with, so its plural form agrees. */
const NUMERIC_PARAMS = new Set(['count']);

/** Params whose value is itself a code the page translates. */
const TRANSLATED_PARAMS = { kind: 'kind', starter: 'starter' } as const;

/**
 * Render design notes in the reader's language.
 *
 * Each note carries a code and its values; the code picks the sentence from
 * `design.note`, and a load kind or starter named in it is translated too.
 * A code the page has no sentence for (a newer API) falls back to the
 * note's English text rather than showing a key.
 */
export function useNoteText(): (note: DesignNote) => string {
  const t = useTranslations('design');
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
    for (const param of NUMERIC_PARAMS) {
      const value = Number(values[param]);
      if (Number.isFinite(value)) values[param] = value;
    }
    return t(key, values);
  };
}

/**
 * Say why a design call did not succeed, in the reader's language.
 *
 * A refusal with a code the page knows is said from `design.errors`, prefixed
 * with what it is about (`Pump: ...`) when the server named it; one without
 * falls back to the server's English reason, and anything else to the
 * generic error.
 */
export function useOutcomeText(): (outcome: { kind: string }) => string {
  const t = useTranslations('design');
  return (outcome) => {
    if (outcome.kind !== 'refused') return t('error');
    const refusal = outcome as Refusal;
    const key = `errors.${refusal.code ?? ''}`;
    if (refusal.code && t.has(key)) {
      const { subject, ...values } = refusal.params ?? {};
      const sentence = t(key, values);
      return subject ? `${subject}: ${sentence}` : sentence;
    }
    return refusal.detail || t('error');
  };
}
