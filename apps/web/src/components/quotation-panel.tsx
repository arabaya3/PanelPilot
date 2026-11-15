'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { FilePicker } from '@/components/file-picker';
import { useOutcomeText } from '@/components/design/note-text';
import { DEFAULT_PROFILE_KEY } from '@/components/design/schedule';
import { useFindingText } from '@/components/design/revision-status';
import {
  exportDesign,
  importPriceList,
  priceDesign,
  type DesignProject,
  type PriceListEntry,
  type PricingSettings,
  type Quotation,
} from '@/lib/design';

/** The rates as typed. */
export type QuoteRates = {
  currency: string;
  cableLength: string;
  labourCircuit: string;
  labourBoard: string;
  enclosure: string;
  markup: string;
  vat: string;
};

/**
 * The settings an estimate is priced with, in the page's language: the
 * server words the lines it writes ("Wiring and testing, per circuit") in
 * the profile's language. Pricing does not design again, so the language
 * changes the wording only, never the design priced.
 */
export function pricingProfile(
  profile: Record<string, unknown> | null,
  language: string,
): Record<string, unknown> {
  return { ...(profile ?? { key: DEFAULT_PROFILE_KEY }), language };
}

const INITIAL_RATES: QuoteRates = {
  currency: 'JOD',
  cableLength: '',
  labourCircuit: '',
  labourBoard: '',
  enclosure: '',
  markup: '0',
  vat: '16',
};

function amount(value: string | number): string {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : String(value);
}

/**
 * The quotation under a designed board: a price list and rates in, every line
 * priced or named as unpriced, and the quotation as a PDF or CSV.
 *
 * Nothing is priced at a guess: a line whose key the price list does not hold
 * shows "not priced", is left out of the total, and the quotation says it is
 * incomplete.
 */
export function QuotationPanel({
  token,
  project,
  profile,
  priceImpl = priceDesign,
  importImpl = importPriceList,
  exportImpl = exportDesign,
  saveImpl,
  currency,
  rates: heldRates,
  onRatesChange,
}: {
  token: string;
  project: DesignProject;
  profile: Record<string, unknown> | null;
  priceImpl?: typeof priceDesign;
  importImpl?: typeof importPriceList;
  exportImpl?: typeof exportDesign;
  saveImpl: (blob: Blob, filename: string) => void;
  /** The market's currency, which the rates start from. */
  currency?: string;
  /**
   * The rates, when the page keeps them: the panel is not shown while the
   * design is out of date or being redesigned, and what was typed outlives
   * that. Kept here otherwise.
   */
  rates?: QuoteRates;
  onRatesChange?: (rates: QuoteRates) => void;
}) {
  const t = useTranslations('design.quote');
  const say = useOutcomeText();
  const id = useId();
  const locale = useLocale();
  const tDesign = useTranslations('design');
  const tEstimate = useTranslations('design.quotation');
  const tFindings = useTranslations('design.findings');
  const findingText = useFindingText();
  const [ownRates, setOwnRates] = useState<QuoteRates>(
    currency ? { ...INITIAL_RATES, currency } : INITIAL_RATES,
  );
  const rates = heldRates ?? ownRates;
  function setRate(key: keyof QuoteRates, value: string) {
    const next = { ...rates, [key]: value };
    setOwnRates(next);
    onRatesChange?.(next);
  }
  const [entries, setEntries] = useState<PriceListEntry[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [quotation, setQuotation] = useState<Quotation | null>(null);
  const [working, setWorking] = useState(false);

  function pricing(): PricingSettings {
    const number = (text: string) => (text.trim() === '' ? '0' : text.trim());
    return {
      currency: rates.currency.trim().toUpperCase() || 'JOD',
      price_list: entries,
      cable_length_m: rates.cableLength.trim() === '' ? null : rates.cableLength.trim(),
      labour_per_circuit: number(rates.labourCircuit),
      labour_per_board: number(rates.labourBoard),
      enclosure_price: number(rates.enclosure),
      markup_percent: number(rates.markup),
      vat_percent: number(rates.vat),
    };
  }

  async function loadPrices(file: File) {
    setMessage(null);
    const outcome = await importImpl({ token, file, filename: file.name });
    if (outcome.kind === 'imported') {
      setEntries(outcome.entries);
      setMessage(t('listLoaded', { count: outcome.entries.length }));
    } else {
      setMessage(say(outcome));
    }
  }

  async function price() {
    setWorking(true);
    setMessage(null);
    const outcome = await priceImpl({
      token,
      project,
      pricing: pricing(),
      profile: pricingProfile(profile, locale),
    });
    setWorking(false);
    if (outcome.kind === 'priced') {
      setQuotation(outcome.quotation);
    } else {
      setMessage(say(outcome));
    }
  }

  async function download(format: 'quotation_pdf') {
    const outcome = await exportImpl({ token, project, format, profile, pricing: pricing() });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      setMessage(say(outcome));
    }
  }

  function rateField(key: keyof QuoteRates, unit = '') {
    return (
      <Field id={`${id}-${key}`} label={t(`field.${key}`)} unit={unit}>
        <input
          id={`${id}-${key}`}
          dir="ltr"
          inputMode={key === 'currency' ? 'text' : 'decimal'}
          value={rates[key]}
          onChange={(event) => {
            setRate(key, event.target.value);
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="quotation-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <p className="text-sm text-text-muted">
        {t.rich('intro', {
          // A key as typed in a price list: code, laid out left to right in any language.
          key: (chunks) => (
            <code dir="ltr" className="font-mono">
              {chunks}
            </code>
          ),
        })}
      </p>

      <FilePicker
        id={`${id}-prices`}
        label={t('priceList')}
        help={t('priceListHelp')}
        accept=".xlsx,.csv,text/csv"
        onFile={(file) => {
          void loadPrices(file);
        }}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {rateField('currency')}
        {rateField('cableLength', t('unit.m'))}
        {rateField('labourCircuit')}
        {rateField('labourBoard')}
        {rateField('enclosure')}
        {rateField('markup', '%')}
        {rateField('vat', '%')}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={working}
          onClick={() => {
            void price();
          }}
          className="btn btn-primary btn-sm"
        >
          {working ? t('working') : t('submit')}
        </button>
        <button
          type="button"
          data-testid="quote-export-pdf"
          onClick={() => {
            void download('quotation_pdf');
          }}
          className="btn btn-sm btn-secondary"
        >
          {t('pdf')}
        </button>
        <button
          type="button"
          disabled
          aria-describedby={`${id}-unavailable`}
          data-testid="quote-export-csv"
          data-unavailable="true"
          className="btn btn-sm btn-secondary"
        >
          {t('csv')}
        </button>
      </div>
      <p
        id={`${id}-unavailable`}
        className="text-sm text-text-muted"
        data-testid="quote-unavailable"
      >
        {tDesign('errors.export_format_unprotected')}
      </p>

      {message && (
        <p role="status" className="text-sm" data-testid="quote-message">
          {message}
        </p>
      )}

      {quotation && (
        <div data-testid="quote-result" className="flex flex-col gap-3">
          <div
            role="note"
            data-testid="quote-provisional"
            data-status={quotation.status}
            className="rounded-lg border border-s-8 border-severity-warning bg-severity-warning-surface p-3 text-sm"
          >
            <p className="font-semibold">{tEstimate('provisionalBanner')}</p>
            <p className="text-text-muted">{tEstimate('provisionalDetail')}</p>
          </div>
          {quotation.blocking && (
            <p role="alert" className="text-sm font-semibold text-severity-critical">
              {tEstimate('blocking', {
                count: quotation.findings.filter((f) => f.severity === 'blocking').length,
              })}
            </p>
          )}
          {quotation.findings.length > 0 && (
            <details data-testid="quote-findings" open={quotation.blocking}>
              <summary className="cursor-pointer text-sm">{tEstimate('findingsTitle')}</summary>
              <ul className="mt-2 flex flex-col gap-1 text-sm">
                {quotation.findings.map((finding, index) => (
                  <li
                    key={`${finding.code}-${String(index)}`}
                    data-testid={`quote-finding-${finding.severity}`}
                  >
                    {`${tFindings(`severity.${finding.severity}`)}: ${findingText(finding)}`}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {!quotation.complete && (
            <p role="alert" className="text-sm font-semibold text-severity-critical">
              {t('incomplete', { count: quotation.unpriced.length })}
            </p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm" dir="ltr">
              <thead>
                <tr className="border-b border-border-subtle">
                  <th className="p-2 text-start">{t('col.item')}</th>
                  <th className="p-2 text-end">{t('col.qty')}</th>
                  <th className="p-2 text-end">{t('col.unitPrice')}</th>
                  <th className="p-2 text-end">{t('col.total')}</th>
                </tr>
              </thead>
              <tbody>
                {quotation.lines.map((line, index) => (
                  <tr key={index} className="border-b border-border-subtle last:border-b-0">
                    <td className="p-2">{line.description}</td>
                    <td className="p-2 text-end">{`${line.quantity} ${t.has(`unit.${line.unit}`) ? t(`unit.${line.unit}`) : line.unit}`}</td>
                    <td className="p-2 text-end">
                      {line.missing === 'length'
                        ? t('noLength')
                        : line.unit_price == null
                          ? t('notPriced')
                          : amount(line.unit_price)}
                    </td>
                    <td className="p-2 text-end">
                      {line.total == null ? '—' : amount(line.total)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1 self-end text-sm" dir="ltr">
            <dt>{t('materials')}</dt>
            <dd className="text-end">{amount(quotation.materials)}</dd>
            <dt>{t('labour')}</dt>
            <dd className="text-end">{amount(quotation.labour)}</dd>
            <dt>{t('markup')}</dt>
            <dd className="text-end">{amount(quotation.markup)}</dd>
            <dt>{t('vat')}</dt>
            <dd className="text-end">{amount(quotation.vat)}</dd>
            <dt className="font-semibold">{`${t('total')} (${quotation.currency})`}</dt>
            <dd className="text-end font-semibold" data-testid="quote-total">
              {amount(quotation.total)}
            </dd>
          </dl>
        </div>
      )}
    </section>
  );
}
