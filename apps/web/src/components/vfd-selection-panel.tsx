'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import {
  CalcErrorNotice,
  checkInputs,
  clearedFor,
  Factors,
  Field,
  fieldErrorsFrom,
  Figure,
  focusFirst,
  round,
  Sources,
  useCalcText,
  type FieldError,
} from '@/components/cable-sizing-panel';
import {
  listDriveRanges,
  selectVfd,
  type DriveRangeSummary,
  type VfdSelectionOutcome,
  type VfdSelectionResponse,
} from '@/lib/calculations';
import { acquireTrial } from '@/lib/session';
import { EmptyState, fieldDescribedBy, Notice } from '@/components/ui';

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
  /** A drive range key; empty for the default (ABB ACS880-01). */
  range: string;
};

const INITIAL: Form = {
  power: '',
  voltage: '400',
  efficiency: '0.93',
  powerFactor: '0.85',
  duty: 'normal',
  altitude: '0',
  ambient: '40',
  range: '',
};

type NumberKey = Exclude<keyof Form, 'duty' | 'range'>;

/** In the order the form shows them, for focusing the first in error. */
const NUMBER_KEYS: readonly NumberKey[] = [
  'power',
  'voltage',
  'efficiency',
  'powerFactor',
  'altitude',
  'ambient',
];

/** The request fields a schema check can reject, by the input that holds them. */
const API_FIELDS: Partial<Record<string, NumberKey>> = {
  motor_power_kw: 'power',
  supply_voltage_v: 'voltage',
  motor_efficiency: 'efficiency',
  motor_power_factor: 'powerFactor',
  altitude_m: 'altitude',
  ambient_temp_c: 'ambient',
};

/** The coded refusals that are about one input. */
const CODE_FIELDS: Partial<Record<string, NumberKey>> = {
  drive_voltage: 'voltage',
  drive_altitude: 'altitude',
  drive_ambient: 'ambient',
  no_drive_type: 'power',
};

const UNITS: Record<NumberKey, string> = {
  power: 'kW',
  voltage: 'V',
  efficiency: '',
  powerFactor: '',
  altitude: 'm',
  ambient: '°C',
};

/**
 * The drive tab of `/calc`: pick a drive for a motor, from the series chosen.
 *
 * The motor current is worked out from its rating, and the smallest drive
 * whose rating for the duty -- derated for the site by its own manual -- carries
 * it is chosen. The series list comes from the API; until it arrives, or if it
 * cannot be read, only the default (ABB ACS880-01) is offered.
 */
export function VfdSelectionPanel({
  acquireImpl = acquireTrial,
  selectImpl = selectVfd,
  listImpl = listDriveRanges,
}: {
  acquireImpl?: typeof acquireTrial;
  selectImpl?: typeof selectVfd;
  listImpl?: typeof listDriveRanges;
}) {
  const t = useTranslations('calc');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [form, setForm] = useState<Form>(INITIAL);
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const [ranges, setRanges] = useState<DriveRangeSummary[]>([]);
  const [errors, setErrors] = useState<Partial<Record<NumberKey, FieldError>>>({});
  const text = useCalcText();
  const id = useId();

  function set<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => clearedFor(current, key, !NUMBER_KEYS.includes(key as NumberKey)));
  }

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
  }, [acquireImpl]);

  useEffect(() => {
    void connect();
  }, [connect]);

  const token = session.kind === 'ready' ? session.token : null;
  useEffect(() => {
    if (token === null) return;
    let live = true;
    void listImpl({ token }).then((outcome) => {
      if (live && outcome.kind === 'listed') setRanges(outcome.ranges);
    });
    return () => {
      live = false;
    };
  }, [token, listImpl]);

  async function run() {
    if (session.kind !== 'ready') return;
    // Altitude: every range's rule starts at sea level (0 m); its ceiling
    // differs by range, so that is the server's to refuse.
    const invalid = checkInputs<NumberKey>([
      ['power', form.power, 'positive'],
      ['voltage', form.voltage, 'positive'],
      ['efficiency', form.efficiency, 'fraction'],
      ['powerFactor', form.powerFactor, 'fraction'],
      ['altitude', form.altitude, 'nonNegative'],
      ['ambient', form.ambient, 'finite'],
    ]);
    setErrors(invalid);
    if (Object.keys(invalid).length > 0) {
      setResult({ kind: 'idle' });
      focusFirst(id, NUMBER_KEYS, invalid);
      return;
    }
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
        ...(form.range !== '' ? { drive_range: form.range } : {}),
      },
    });
    if (outcome.kind === 'selected') {
      setResult({ kind: 'selected', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'refused') {
        const refused = fieldErrorsFrom(outcome, { codes: CODE_FIELDS, fields: API_FIELDS });
        setErrors(refused);
        focusFirst(id, NUMBER_KEYS, refused);
      }
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  function field(key: NumberKey) {
    const error = text.fieldError(errors[key]);
    return (
      <Field id={`${id}-${key}`} label={t(`vfd.field.${key}`)} unit={UNITS[key]} error={error}>
        <input
          id={`${id}-${key}`}
          inputMode="decimal"
          dir="ltr"
          {...fieldDescribedBy(`${id}-${key}`, { error })}
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
    <>
      <p className="mb-5 max-w-3xl text-text-muted">{t('vfd.intro')}</p>

      {/* Inputs beside results from `lg` up; stacked, inputs first, below. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 lg:items-start">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
          className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5"
        >
          <Field id={`${id}-range`} label={t('vfd.field.range')}>
            <select
              id={`${id}-range`}
              value={form.range}
              onChange={(event) => {
                set('range', event.target.value);
              }}
              className="input w-full"
            >
              <option value="">{t('vfd.range.default')}</option>
              {ranges
                .filter((range) => range.key !== 'abb-acs880-01')
                .map((range) => (
                  <option key={range.key} value={range.key}>
                    {`${range.manufacturer} ${range.series}`}
                  </option>
                ))}
            </select>
          </Field>
          {field('power')}
          {field('voltage')}
          {field('efficiency')}
          {field('powerFactor')}
          <Field id={`${id}-duty`} label={t('vfd.field.duty')}>
            <select
              id={`${id}-duty`}
              value={form.duty}
              onChange={(event) => {
                set('duty', event.target.value as Form['duty']);
              }}
              className="input w-full"
            >
              <option value="normal">{t('vfd.duty.normal')}</option>
              <option value="heavy">{t('vfd.duty.heavy')}</option>
            </select>
          </Field>
          {field('altitude')}
          {field('ambient')}
          <div className="sm:col-span-2">
            <button
              type="submit"
              disabled={session.kind !== 'ready' || result.kind === 'working'}
              className="btn btn-primary"
            >
              {result.kind === 'working' ? t('working') : t('vfd.submit')}
            </button>
          </div>
        </form>

        <div className="flex min-w-0 flex-col gap-4">
          {result.kind === 'idle' && session.kind === 'ready' && (
            <EmptyState title={t('result.idle')} />
          )}

          {session.kind !== 'ready' && session.kind !== 'starting' && (
            <Notice
              tone="warning"
              action={
                <button
                  type="button"
                  onClick={() => void connect()}
                  className="btn btn-sm btn-primary"
                >
                  {t('retry')}
                </button>
              }
            >
              {t(`session.${session.kind}`)}
            </Notice>
          )}

          {result.kind === 'error' && (
            <CalcErrorNotice
              outcome={result.outcome}
              hasFieldErrors={Object.keys(errors).length > 0}
              testId="vfd-error"
            />
          )}

          {result.kind === 'selected' && (
            <section className="card flex flex-col gap-5 p-4 md:p-5" data-testid="vfd-result">
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                <Figure
                  label={t('vfd.result.drive')}
                  value={`${result.response.result.manufacturer} ${result.response.result.frame_reference}`}
                />
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
        </div>
      </div>
    </>
  );
}
