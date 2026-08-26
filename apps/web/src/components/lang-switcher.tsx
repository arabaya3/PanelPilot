'use client';

import { useTranslations } from 'next-intl';

import { useLocale } from '@/components/locale-provider';
import { LOCALES, type Locale } from '@/i18n/config';

/**
 * Switches the interface language.
 *
 * Each option is labelled in **its own** language — "العربية", not "Arabic".
 * Someone who cannot read the current interface is exactly the person using
 * this control, and a list of language names in a language they do not read
 * is no help at all.
 */
export function LangSwitcher() {
  const { locale, setLocale } = useLocale();
  const t = useTranslations('language');

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-text-muted">{t('label')}</span>
      <select
        value={locale}
        onChange={(event) => {
          setLocale(event.target.value as Locale);
        }}
        className="rounded-md border border-border bg-surface px-2 py-1 text-text"
      >
        {LOCALES.map((option) => (
          // `lang` on each option so a screen reader pronounces the name in
          // the language it is written in rather than in the page's language.
          <option key={option} value={option} lang={option}>
            {t(option)}
          </option>
        ))}
      </select>
    </label>
  );
}
