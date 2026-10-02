'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { FilePicker } from '@/components/file-picker';
import { useOutcomeText } from '@/components/design/note-text';
import {
  exportDesign,
  importPriceList,
  priceDesign,
  type DesignProject,
  type PriceListEntry,
  type PricingSettings,
  type Quotation,
} from '@/lib/design';

type Rates = {
  currency: string;
  cableLength: string;
  labourCircuit: string;
  labourBoard: string;
  enclosure: string;
  markup: string;
  vat: string;
};

const INITIAL_RATES: Rates = {
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
}: {
  token: string;
  project: DesignProject;
  profile: Record<string, unknown> | null;
  priceImpl?: typeof priceDesign;
  importImpl?: typeof importPriceList;
  exportImpl?: typeof exportDesign;
  saveImpl: (blob: Blob, filename: string) => void;
}) {
  const t = useTranslations('design.quote');
  const say = useOutcomeText();
  const id = useId();
  const [rates, setRates] = useState<Rates>(INITIAL_RATES);
  const [entries, setEntries] = useState<PriceListEntry[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [quotation, setQuotation] = useState<Quotation | null>(null);
  const [working, setWorking] = useState(false);

  function pricing(): PricingSettings {
    const number = (text: string) => (text.trim() === '' ? '0' : text.trim());
    return {
      currency: rates.currency.trim() || 'JOD',
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
    const outcome = await priceImpl({ token, project, pricing: pricing(), profile });
    setWorking(false);
    if (outcome.kind === 'priced') {
      setQuotation(outcome.quotation);
    } else {
      setMessage(say(outcome));
    }
  }

  async function download(format: 'quotation_pdf' | 'quotation_csv') {
    const outcome = await exportImpl({ token, project, format, profile, pricing: pricing() });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      setMessage(say(outcome));
    }
  }

  function rateField(key: keyof Rates, unit = '') {
    return (
      <Field id={`${id}-${key}`} label={t(`field.${key}`)} unit={unit}>
        <input
          id={`${id}-${key}`}
          dir="ltr"
          inputMode={key === 'currency' ? 'text' : 'decimal'}
          value={rates[key]}
          onChange={(event) => {
            setRates((current) => ({ ...current, [key]: event.target.value }));
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="quotation-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <p className="text-sm text-text-muted">{t('intro')}</p>

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
        {rateField('cableLength', 'm')}
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
          data-testid="quote-export-csv"
          onClick={() => {
            void download('quotation_csv');
          }}
          className="btn btn-sm btn-secondary"
        >
          {t('csv')}
        </button>
      </div>

      {message && (
        <p role="status" className="text-sm" data-testid="quote-message">
          {message}
        </p>
      )}

      {quotation && (
        <div data-testid="quote-result" className="flex flex-col gap-3">
          {!quotation.complete && (
            <p role="alert" className="text-sm font-semibold text-danger">
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
                    <td className="p-2 text-end">{`${line.quantity} ${line.unit}`}</td>
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
