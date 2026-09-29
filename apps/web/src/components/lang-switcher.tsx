'use client';

import { useTranslations } from 'next-intl';

import { GlobeIcon } from '@/components/icons';
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
    // The globe carries the meaning for sight; the label is still there for a
    // screen reader, which announces the select as "Language".
    <label className="relative flex items-center text-sm">
      <span className="sr-only">{t('label')}</span>
      <GlobeIcon
        width="16"
        height="16"
        className="pointer-events-none absolute start-0 ms-2 hidden text-text-muted sm:block"
      />
      <select
        value={locale}
        onChange={(event) => {
          setLocale(event.target.value as Locale);
        }}
        className="h-[2.25rem] cursor-pointer rounded-md border border-border-subtle bg-surface py-0 pe-2 ps-2 text-sm sm:ps-6 font-medium text-text transition-colors hover:bg-surface-raised"
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
