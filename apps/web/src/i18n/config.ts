/**
 * The locales the product speaks, and which way each one reads.
 *
 * Direction is derived here rather than asked for at each call site: a
 * component that has to know whether it is in RTL is a component that will get
 * it wrong somewhere, and the fix for the somewhere is another special case.
 *
 * Mirrors `app/models/schemas/locale.py` on the API side. The two lists must
 * agree — a locale the UI offers and the backend refuses is a language the
 * user can select and then get English in.
 */

export const LOCALES = ['en', 'ar', 'he'] as const;

export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = 'en';

/** Locales written right to left. */
const RTL_LOCALES = new Set<Locale>(['ar', 'he']);

/**
 * The `dir` attribute for a locale.
 *
 * Set once on `<html>`, which is what makes every logical property in the
 * stylesheet flip. Setting it per-component would leave whichever component
 * nobody remembered pointing the wrong way.
 */
export function directionOf(locale: Locale): 'ltr' | 'rtl' {
  return RTL_LOCALES.has(locale) ? 'rtl' : 'ltr';
}

/**
 * Narrow an arbitrary string to a supported locale.
 *
 * @param value - A candidate from a cookie, a header, or a URL.
 * @returns The locale, or the default when it is not one we support.
 *
 * Falls back rather than throwing: an unrecognised `Accept-Language` is
 * ordinary, and refusing the request over it would turn a browser preference
 * into an error page.
 */
export function toLocale(value: string | undefined | null): Locale {
  return LOCALES.includes(value as Locale) ? (value as Locale) : DEFAULT_LOCALE;
}
