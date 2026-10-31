'use client';

import { useLocale, useTranslations } from 'next-intl';

/** Locales whose users must be told that exported documents are not in their language. */
const NOT_ENGLISH = new Set(['ar', 'he']);

/**
 * Says, next to the export buttons, that exported documents (drawing set,
 * calculations, schedules, quotation) are in English. Shown in Arabic and
 * Hebrew only: the PDFs are rendered in English whatever the interface
 * language, and an English user needs no note.
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
