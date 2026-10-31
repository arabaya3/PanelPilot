'use client';

import { useLocale, useTranslations } from 'next-intl';

/** Locales whose users must be told that exported documents are not in their language. */
const NOT_ENGLISH = new Set(['ar', 'he']);

/**
 * Says, next to the export buttons, which exports are in English: the drawing
 * set and calculations follow the company profile's drawing language (a
 * draft takes it from the page language, a revision from its saved profile),
 * but their status marks and findings are English; the quotation, the
 * terminal and wire schedules and the DXF are always English; the JSON holds
 * the data as entered. Shown in Arabic and Hebrew only: an English user
 * needs no note.
 */
export function EnglishExportsNote({ testId = 'english-exports-note' }: { testId?: string }) {
  const locale = useLocale();
  const t = useTranslations('design.exports');
  if (!NOT_ENGLISH.has(locale)) return null;
  return (
    <p className="text-sm text-text-muted" data-testid={testId}>
      {t('englishOnly')}
    </p>
  );
}
