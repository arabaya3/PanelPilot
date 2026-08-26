'use client';

import { useTranslations } from 'next-intl';

import { TechnicalToken } from '@/components/technical-token';

/**
 * A translated sentence with technical tokens embedded in it.
 *
 * This is the shape every diagnostic card takes: prose in the engineer's
 * language, wrapped around identifiers that are the same in every language.
 * It exists as its own component mainly so the bidirectional behaviour has
 * something concrete to be tested against — the acceptance criterion is about
 * how these render inside RTL prose, and a test needs a real mixed string to
 * check rather than a synthetic one.
 *
 * Note the tokens are passed as elements, not interpolated as text. next-intl
 * renders `{code}` wherever the translator put it — including at the start of
 * an Arabic sentence, where the reordering risk is highest — and passing an
 * element means the `<bdi>` wrapper travels with it.
 */
export function DiagnosisSample() {
  const t = useTranslations('diagnosis');

  return (
    <div className="rounded-md border border-border bg-surface p-4">
      <p className="mb-2">
        {t.rich('faultDetected', {
          code: (chunks) => <TechnicalToken>{chunks}</TechnicalToken>,
          model: (chunks) => <TechnicalToken>{chunks}</TechnicalToken>,
        })}
      </p>
      <p className="text-text-muted">
        {t.rich('checkParameter', {
          param: (chunks) => <TechnicalToken>{chunks}</TechnicalToken>,
          volts: (chunks) => <TechnicalToken>{chunks}</TechnicalToken>,
        })}
      </p>
    </div>
  );
}
