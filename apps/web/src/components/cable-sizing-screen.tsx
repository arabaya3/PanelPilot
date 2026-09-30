'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState, type ReactNode } from 'react';

import { AppShell } from '@/components/app-shell';
import {
  sizeCable,
  type CableSizingOutcome,
  type CableSizingRequest,
  type CableSizingResponse,
} from '@/lib/calculations';
import { acquireTrial } from '@/lib/session';

/** The methods the tables are transcribed for; the API refuses the rest. */
const METHODS = ['A1', 'A2', 'B1', 'B2', 'C', 'E', 'F'] as const;

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'sized'; response: CableSizingResponse }
  | { kind: 'error'; outcome: Exclude<CableSizingOutcome, { kind: 'sized' }> };

type Form = {
  current: string;
  length: string;
  voltage: string;
  method: (typeof METHODS)[number];
  ambient: string;
  grouped: string;
  insulation: '70' | '90';
  phases: '3' | '1';
};

const INITIAL: Form = {
  current: '',
  length: '',
  voltage: '400',
  method: 'C',
  ambient: '30',
  grouped: '1',
  insulation: '90',
  phases: '3',
};

/**
 * `/calc`: size a copper feeder cable.
 *
 * Every number the result shows names the table it came from and the page,
 * so an engineer can check it against the handbook before it goes on a
 * drawing. An input the tables do not cover is refused with the reason, not
 * rounded into a guess.
 */
export function CableSizingScreen({
  acquireImpl = acquireTrial,
  sizeImpl = sizeCable,
}: {
  acquireImpl?: typeof acquireTrial;
  sizeImpl?: typeof sizeCable;
}) {
  const t = useTranslations('calc');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [form, setForm] = useState<Form>(INITIAL);
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const id = useId();

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
  }, [acquireImpl]);

  useEffect(() => {
    void connect();
  }, [connect]);

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  const complete = form.current.trim() !== '' && form.length.trim() !== '';

  async function run() {
    if (session.kind !== 'ready' || !complete) return;
    setResult({ kind: 'working' });
    const request: CableSizingRequest = {
      design_current_a: form.current.trim(),
      length_m: form.length.trim(),
      supply_voltage_v: form.voltage.trim(),
      installation_method: form.method,
      ambient_temp_c: form.ambient.trim(),
      grouped_circuits: Number(form.grouped),
      conductor_material: 'copper',
      insulation_rating_c: Number(form.insulation),
      // The normal-service motor column: the drop table tabulates 0.8 and
      // 0.35 only.
      power_factor: '0.8',
      three_phase: form.phases === '3',
    };
    const outcome = await sizeImpl({ token: session.token, request });
    if (outcome.kind === 'sized') {
      setResult({ kind: 'sized', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  function field(key: 'current' | 'length' | 'voltage' | 'ambient' | 'grouped', unit: string) {
    return (
      <Field id={`${id}-${key}`} label={t(`field.${key}`)} unit={unit}>
        <input
          id={`${id}-${key}`}
          inputMode="decimal"
          dir="ltr"
          value={form[key]}
          onChange={(event) => {
            set(key, event.target.value);
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <AppShell>
      <div className="mb-6 flex flex-col gap-2">
        <h1 className="text-2xl font-bold tracking-tight">{t('heading')}</h1>
        <p className="max-w-3xl text-text-muted">{t('intro')}</p>
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="card mb-6 grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-4"
      >
        {field('current', 'A')}
        {field('length', 'm')}
        {field('voltage', 'V')}
        {field('ambient', '°C')}
        <Field id={`${id}-method`} label={t('field.method')}>
          <select
            id={`${id}-method`}
            value={form.method}
            onChange={(event) => {
              set('method', event.target.value as Form['method']);
            }}
            className="input w-full"
          >
            {METHODS.map((method) => (
              <option key={method} value={method}>
                {t(`method.${method}`)}
              </option>
            ))}
          </select>
        </Field>
        {field('grouped', '')}
        <Field id={`${id}-insulation`} label={t('field.insulation')}>
          <select
            id={`${id}-insulation`}
            value={form.insulation}
            onChange={(event) => {
              set('insulation', event.target.value as Form['insulation']);
            }}
            className="input w-full"
          >
            <option value="90">{t('insulation.90')}</option>
            <option value="70">{t('insulation.70')}</option>
          </select>
        </Field>
        <Field id={`${id}-phases`} label={t('field.phases')}>
          <select
            id={`${id}-phases`}
            value={form.phases}
            onChange={(event) => {
              set('phases', event.target.value as Form['phases']);
            }}
            className="input w-full"
          >
            <option value="3">{t('phases.3')}</option>
            <option value="1">{t('phases.1')}</option>
          </select>
        </Field>
        <div className="sm:col-span-2 lg:col-span-4">
          <button
            type="submit"
            disabled={session.kind !== 'ready' || !complete || result.kind === 'working'}
            className="btn btn-primary"
          >
            {result.kind === 'working' ? t('working') : t('submit')}
          </button>
        </div>
      </form>

      {session.kind !== 'ready' && session.kind !== 'starting' && (
        <div
          role="alert"
          data-testid="calc-session-failed"
          className="mb-6 flex flex-col items-start gap-3 rounded-lg border border-severity-warning bg-severity-warning-surface p-4 text-sm text-severity-warning"
        >
          <p>{t(`session.${session.kind}`)}</p>
          <button type="button" onClick={() => void connect()} className="btn btn-sm btn-primary">
            {t('retry')}
          </button>
        </div>
      )}

      {result.kind === 'error' && (
        <div
          role="alert"
          data-testid="calc-error"
          className="rounded-lg border border-severity-critical bg-severity-critical-surface p-4 text-sm text-severity-critical"
        >
          <p>{t(`error.${result.outcome.kind}`)}</p>
          {result.outcome.kind === 'refused' && result.outcome.detail !== '' && (
            <p dir="ltr" className="mt-2 font-mono text-xs">
              {result.outcome.detail}
            </p>
          )}
        </div>
      )}

      {result.kind === 'sized' && <CableResult response={result.response} />}
    </AppShell>
  );
}

function Field({
  id,
  label,
  unit,
  children,
}: {
  id: string;
  label: string;
  unit?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-sm font-semibold">
        {label}
        {unit ? <span className="ms-1 font-normal text-text-muted">({unit})</span> : null}
      </label>
      {children}
    </div>
  );
}

/** Trim a decimal string for display: "111.8124" -> "111.81", "95" -> "95". */
function round(value: string | number, places = 2): string {
  const number = Number(value);
  return Number.isFinite(number) ? String(Number(number.toFixed(places))) : String(value);
}

function CableResult({ response }: { response: CableSizingResponse }) {
  const t = useTranslations('calc');
  const { result } = response;

  return (
    <section className="card flex flex-col gap-5 p-4 md:p-5" data-testid="calc-result">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Figure label={t('result.section')} value={`${round(result.cross_section_mm2)} mm²`} />
        <Figure label={t('result.ampacity')} value={`${round(result.derated_ampacity_a)} A`} />
        <Figure
          label={t('result.drop')}
          value={`${round(response.voltage_drop_v)} V (${round(response.voltage_drop_percent)}%)`}
        />
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">{t('result.factors')}</h2>
        <ul className="flex flex-col gap-1 text-sm" dir="ltr">
          {result.applied_factors.map((factor) => (
            <li key={factor.name}>
              <span className="font-mono">{factor.name}</span> = {round(factor.value, 3)}
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">{t('result.sources')}</h2>
        <ul className="flex flex-col gap-1 text-sm" data-testid="calc-sources">
          {response.sources.map((source, index) => (
            <li key={index} dir="ltr">
              {source.manufacturer} — <cite>{source.document_title}</cite>
              {source.section ? `, ${source.section}` : ''}
              {typeof source.page === 'number' ? `, p. ${String(source.page)}` : ''}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-text-muted">{label}</span>
      <span dir="ltr" className="text-start text-xl font-bold tabular-nums">
        {value}
      </span>
    </div>
  );
}
