'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { Factors, Field, Figure, round, Sources } from '@/components/cable-sizing-panel';
import { selectVfd, type VfdSelectionOutcome, type VfdSelectionResponse } from '@/lib/calculations';
import { acquireTrial } from '@/lib/session';

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'selected'; response: VfdSelectionResponse }
  | { kind: 'error'; outcome: Exclude<VfdSelectionOutcome, { kind: 'selected' }> };

type Form = {
  power: string;
  voltage: string;
  efficiency: string;
  powerFactor: string;
  duty: 'normal' | 'heavy';
  altitude: string;
  ambient: string;
};

const INITIAL: Form = {
  power: '',
  voltage: '400',
  efficiency: '0.93',
  powerFactor: '0.85',
  duty: 'normal',
  altitude: '0',
  ambient: '40',
};

type NumberKey = Exclude<keyof Form, 'duty'>;

const UNITS: Record<NumberKey, string> = {
  power: 'kW',
  voltage: 'V',
  efficiency: '',
  powerFactor: '',
  altitude: 'm',
  ambient: '°C',
};

/**
 * The drive tab of `/calc`: pick an ACS880-01 for a motor.
 *
 * The motor current is worked out from its rating, and the smallest drive
 * whose rating for the duty -- derated for the site -- carries it is chosen.
 */
export function VfdSelectionPanel({
  acquireImpl = acquireTrial,
  selectImpl = selectVfd,
}: {
  acquireImpl?: typeof acquireTrial;
  selectImpl?: typeof selectVfd;
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

  const complete = form.power.trim() !== '';

  async function run() {
    if (session.kind !== 'ready' || !complete) return;
    setResult({ kind: 'working' });
    const outcome = await selectImpl({
      token: session.token,
      request: {
        motor_power_kw: form.power.trim(),
        supply_voltage_v: form.voltage.trim(),
        motor_efficiency: form.efficiency.trim(),
        motor_power_factor: form.powerFactor.trim(),
        duty_class: form.duty,
        altitude_m: form.altitude.trim(),
        ambient_temp_c: form.ambient.trim(),
      },
    });
    if (outcome.kind === 'selected') {
      setResult({ kind: 'selected', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  function field(key: NumberKey) {
    return (
      <Field id={`${id}-${key}`} label={t(`vfd.field.${key}`)} unit={UNITS[key]}>
        <input
          id={`${id}-${key}`}
          inputMode="decimal"
          dir="ltr"
          value={form[key]}
          onChange={(event) => {
            setForm((current) => ({ ...current, [key]: event.target.value }));
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <>
      <p className="mb-5 max-w-3xl text-text-muted">{t('vfd.intro')}</p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="card mb-6 grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-4"
      >
        {field('power')}
        {field('voltage')}
        {field('efficiency')}
        {field('powerFactor')}
        <Field id={`${id}-duty`} label={t('vfd.field.duty')}>
          <select
            id={`${id}-duty`}
            value={form.duty}
            onChange={(event) => {
              setForm((current) => ({ ...current, duty: event.target.value as Form['duty'] }));
            }}
            className="input w-full"
          >
            <option value="normal">{t('vfd.duty.normal')}</option>
            <option value="heavy">{t('vfd.duty.heavy')}</option>
          </select>
        </Field>
        {field('altitude')}
        {field('ambient')}
        <div className="sm:col-span-2 lg:col-span-4">
          <button
            type="submit"
            disabled={session.kind !== 'ready' || !complete || result.kind === 'working'}
            className="btn btn-primary"
          >
            {result.kind === 'working' ? t('working') : t('vfd.submit')}
          </button>
        </div>
      </form>

      {session.kind !== 'ready' && session.kind !== 'starting' && (
        <div
          role="alert"
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
          data-testid="vfd-error"
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

      {result.kind === 'selected' && (
        <section className="card flex flex-col gap-5 p-4 md:p-5" data-testid="vfd-result">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Figure label={t('vfd.result.drive')} value={result.response.result.frame_reference} />
            <Figure
              label={t('vfd.result.motorCurrent')}
              value={`${round(result.response.motor_current_a, 1)} A`}
            />
            <Figure
              label={t('vfd.result.driveCurrent')}
              value={`${round(result.response.result.rated_output_current_a, 1)} A`}
            />
          </div>
          <Factors factors={result.response.result.applied_factors} />
          <Sources sources={result.response.sources} />
        </section>
      )}
    </>
  );
}
