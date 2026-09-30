'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { Field, round, Sources } from '@/components/cable-sizing-panel';
import {
  buildBom,
  type PanelBomOutcome,
  type PanelBomRequest,
  type PanelBomResponse,
} from '@/lib/calculations';
import { acquireTrial } from '@/lib/session';

type Constraints = PanelBomRequest['constraints'];
type Placement = Constraints['placement'];
type Method = Constraints['cable_installation_method'];

const PLACEMENTS: Placement[] = [
  'single_wall',
  'single_free_standing',
  'suite_end_wall',
  'suite_end_free_standing',
  'suite_middle_wall',
  'suite_middle_free_standing',
  'suite_middle_wall_covered_roof',
];
const METHODS: Method[] = ['A1', 'A2', 'B1', 'B2', 'C', 'E', 'F'];

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'built'; response: PanelBomResponse }
  | { kind: 'error'; outcome: Exclude<PanelBomOutcome, { kind: 'built' }> };

type Load = {
  key: number;
  tag: string;
  description: string;
  current: string;
  dissipation: string;
  variableSpeed: boolean;
};

type Enclosure = {
  width: string;
  height: string;
  depth: string;
  ingress: string;
  placement: Placement;
  method: Method;
  ambient: string;
  maxInternal: string;
};

const INITIAL_ENCLOSURE: Enclosure = {
  width: '800',
  height: '2000',
  depth: '600',
  ingress: 'IP54',
  placement: 'single_wall',
  method: 'C',
  ambient: '35',
  maxInternal: '50',
};

function blankLoad(key: number): Load {
  return { key, tag: '', description: '', current: '', dissipation: '', variableSpeed: false };
}

/**
 * The BOM tab of `/calc`: a load schedule in, the sourced lines out.
 *
 * Only what a sourced table sizes is listed -- drives, outgoing cables, the
 * enclosure and its cooling -- and the result says what it leaves out.
 */
export function PanelBomPanel({
  acquireImpl = acquireTrial,
  buildImpl = buildBom,
}: {
  acquireImpl?: typeof acquireTrial;
  buildImpl?: typeof buildBom;
}) {
  const t = useTranslations('calc');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [enclosure, setEnclosure] = useState<Enclosure>(INITIAL_ENCLOSURE);
  const [loads, setLoads] = useState<Load[]>([blankLoad(0)]);
  const [nextKey, setNextKey] = useState(1);
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

  function updateLoad(key: number, patch: Partial<Load>) {
    setLoads((current) => current.map((load) => (load.key === key ? { ...load, ...patch } : load)));
  }

  const complete = loads.every((load) => load.tag.trim() !== '');

  async function run() {
    if (session.kind !== 'ready' || !complete) return;
    setResult({ kind: 'working' });
    const outcome = await buildImpl({
      token: session.token,
      request: {
        loads: loads.map((load) => ({
          tag: load.tag.trim(),
          description: load.description.trim(),
          power_kw: null,
          current_a: load.current.trim() === '' ? null : load.current.trim(),
          dissipation_w: load.dissipation.trim() === '' ? null : load.dissipation.trim(),
          variable_speed: load.variableSpeed,
        })),
        constraints: {
          width_mm: Number(enclosure.width),
          height_mm: Number(enclosure.height),
          depth_mm: Number(enclosure.depth),
          ingress_rating: enclosure.ingress.trim(),
          placement: enclosure.placement,
          cable_installation_method: enclosure.method,
          supply_voltage_v: '400',
          preferred_vendors: [],
          ambient_temp_c: enclosure.ambient.trim(),
          max_internal_temp_c: enclosure.maxInternal.trim(),
        },
      },
    });
    if (outcome.kind === 'built') {
      setResult({ kind: 'built', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  function enclosureField(
    key: 'width' | 'height' | 'depth' | 'ingress' | 'ambient' | 'maxInternal',
    unit: string,
  ) {
    return (
      <Field id={`${id}-${key}`} label={t(`bom.field.${key}`)} unit={unit}>
        <input
          id={`${id}-${key}`}
          inputMode={key === 'ingress' ? 'text' : 'decimal'}
          dir="ltr"
          value={enclosure[key]}
          onChange={(event) => {
            setEnclosure((current) => ({ ...current, [key]: event.target.value }));
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <>
      <p className="mb-5 max-w-3xl text-text-muted">{t('bom.intro')}</p>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="mb-6 flex flex-col gap-4"
      >
        <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-4">
          <legend className="px-1 text-sm font-semibold">{t('bom.enclosure')}</legend>
          {enclosureField('width', 'mm')}
          {enclosureField('height', 'mm')}
          {enclosureField('depth', 'mm')}
          {enclosureField('ingress', '')}
          <Field id={`${id}-placement`} label={t('bom.field.placement')}>
            <select
              id={`${id}-placement`}
              value={enclosure.placement}
              onChange={(event) => {
                setEnclosure((c) => ({ ...c, placement: event.target.value as Placement }));
              }}
              className="input w-full"
            >
              {PLACEMENTS.map((placement) => (
                <option key={placement} value={placement}>
                  {t(`bom.placement.${placement}`)}
                </option>
              ))}
            </select>
          </Field>
          <Field id={`${id}-method`} label={t('bom.field.method')}>
            <select
              id={`${id}-method`}
              value={enclosure.method}
              onChange={(event) => {
                setEnclosure((c) => ({ ...c, method: event.target.value as Method }));
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
          {enclosureField('ambient', '°C')}
          {enclosureField('maxInternal', '°C')}
        </fieldset>

        <fieldset className="card flex flex-col gap-4 p-4 md:p-5">
          <legend className="px-1 text-sm font-semibold">{t('bom.loads')}</legend>
          {loads.map((load, index) => (
            <div
              key={load.key}
              data-testid={`bom-load-${String(index)}`}
              className="grid grid-cols-1 gap-3 border-b border-border-subtle pb-4 last:border-b-0 last:pb-0 sm:grid-cols-2 lg:grid-cols-6 lg:items-end"
            >
              <Field id={`${id}-tag-${String(load.key)}`} label={t('bom.field.tag')}>
                <input
                  id={`${id}-tag-${String(load.key)}`}
                  dir="ltr"
                  value={load.tag}
                  onChange={(event) => {
                    updateLoad(load.key, { tag: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <Field id={`${id}-desc-${String(load.key)}`} label={t('bom.field.description')}>
                <input
                  id={`${id}-desc-${String(load.key)}`}
                  value={load.description}
                  onChange={(event) => {
                    updateLoad(load.key, { description: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <Field id={`${id}-cur-${String(load.key)}`} label={t('bom.field.current')} unit="A">
                <input
                  id={`${id}-cur-${String(load.key)}`}
                  inputMode="decimal"
                  dir="ltr"
                  value={load.current}
                  onChange={(event) => {
                    updateLoad(load.key, { current: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <Field
                id={`${id}-heat-${String(load.key)}`}
                label={t('bom.field.dissipation')}
                unit="W"
              >
                <input
                  id={`${id}-heat-${String(load.key)}`}
                  inputMode="decimal"
                  dir="ltr"
                  value={load.dissipation}
                  onChange={(event) => {
                    updateLoad(load.key, { dissipation: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <label className="flex items-center gap-2 py-2 text-sm">
                <input
                  type="checkbox"
                  checked={load.variableSpeed}
                  onChange={(event) => {
                    updateLoad(load.key, { variableSpeed: event.target.checked });
                  }}
                />
                {t('bom.field.variableSpeed')}
              </label>
              <button
                type="button"
                disabled={loads.length === 1}
                onClick={() => {
                  setLoads((current) => current.filter((l) => l.key !== load.key));
                }}
                className="btn btn-sm self-start lg:self-end"
              >
                {t('bom.remove')}
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => {
              setLoads((current) => [...current, blankLoad(nextKey)]);
              setNextKey((key) => key + 1);
            }}
            className="btn btn-sm self-start"
          >
            {t('bom.add')}
          </button>
        </fieldset>

        <div>
          <button
            type="submit"
            disabled={session.kind !== 'ready' || !complete || result.kind === 'working'}
            className="btn btn-primary"
          >
            {result.kind === 'working' ? t('working') : t('bom.submit')}
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
          data-testid="bom-error"
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

      {result.kind === 'built' && <BomResult response={result.response} />}
    </>
  );
}

function BomResult({ response }: { response: PanelBomResponse }) {
  const t = useTranslations('calc');
  const { result } = response;
  return (
    <section className="card flex flex-col gap-5 p-4 md:p-5" data-testid="bom-result">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">{t('bom.result.heat')}</span>
          <span dir="ltr" className="text-start text-xl font-bold tabular-nums">
            {round(result.heat_load_w, 0)} W
          </span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs text-text-muted">{t('bom.result.cooling')}</span>
          <span dir="ltr" className="text-start text-xl font-bold tabular-nums">
            {round(result.cooling_required_w, 0)} W
          </span>
        </div>
      </div>

      {/* A list of cards rather than a table: it reads the same at 360px. */}
      <ol className="flex flex-col gap-2" data-testid="bom-lines">
        {result.lines.map((line, index) => (
          <li key={index} className="rounded-md border border-border-subtle p-3 text-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span dir="ltr" className="font-mono font-semibold">
                {line.part_reference}
              </span>
              <span className="text-text-muted">×{line.quantity}</span>
            </div>
            <p dir="auto" className="mt-1">
              {line.description}
            </p>
          </li>
        ))}
      </ol>

      {result.notes.length > 0 && (
        <ul className="flex list-disc flex-col gap-1 ps-5 text-sm text-text-muted" dir="ltr">
          {result.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}

      <Sources sources={response.sources} />
    </section>
  );
}
