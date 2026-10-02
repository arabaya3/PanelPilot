'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { Field, round } from '@/components/cable-sizing-panel';
import {
  designBoard,
  exportDesign,
  type BoardDesignResponse,
  type DesignOutcome,
  type ExportFormat,
  type LoadKind,
} from '@/lib/design';
import { acquireTrial } from '@/lib/session';

const LOAD_KINDS: LoadKind[] = [
  'lighting',
  'socket',
  'air_conditioning',
  'water_heater',
  'kitchen',
  'fan',
  'motor',
  'lift',
  'sub_board',
  'control',
  'data',
  'other',
];

const FORMATS: ExportFormat[] = [
  'pdf',
  'dxf',
  'qet',
  'aml',
  'devices_csv',
  'parts_csv',
  'cables_csv',
  'circuits_csv',
  'json',
];

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'designed'; response: BoardDesignResponse }
  | { kind: 'error'; outcome: Exclude<DesignOutcome, { kind: 'designed' }> };

type Load = {
  key: number;
  description: string;
  load: LoadKind;
  power: string;
  phases: '1' | '3';
  powerFactor: string;
};

type Info = {
  name: string;
  number: string;
  customer: string;
  consultant: string;
  contractor: string;
  board: string;
  location: string;
  voltage: string;
  phases: '1' | '3';
  faultLevel: string;
};

const INITIAL_INFO: Info = {
  name: '',
  number: '',
  customer: '',
  consultant: '',
  contractor: '',
  board: 'DB1',
  location: '',
  voltage: '400',
  phases: '3',
  faultLevel: '',
};

function blankLoad(key: number): Load {
  return { key, description: '', load: 'socket', power: '', phases: '1', powerFactor: '' };
}

/** Save a blob under a name, the way a download link does. */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/**
 * `/design`: a distribution board from its load schedule to its drawing set.
 *
 * The board is designed under the company's settings (pasted as JSON, only
 * what differs from the default), and every output -- the PDF drawing set,
 * DXF, QElectroTech, AutomationML and the CSV lists -- is generated from the
 * one designed project.
 */
export function DesignScreen({
  acquireImpl = acquireTrial,
  designImpl = designBoard,
  exportImpl = exportDesign,
  saveImpl = saveBlob,
}: {
  acquireImpl?: typeof acquireTrial;
  designImpl?: typeof designBoard;
  exportImpl?: typeof exportDesign;
  saveImpl?: typeof saveBlob;
}) {
  const t = useTranslations('design');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [info, setInfo] = useState<Info>(INITIAL_INFO);
  const [loads, setLoads] = useState<Load[]>([blankLoad(0)]);
  const [nextKey, setNextKey] = useState(1);
  const [profileText, setProfileText] = useState('');
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const [exportError, setExportError] = useState<string | null>(null);
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

  function parsedProfile(): Record<string, unknown> | null | 'invalid' {
    if (profileText.trim() === '') return null;
    try {
      const value: unknown = JSON.parse(profileText);
      return typeof value === 'object' && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : 'invalid';
    } catch {
      return 'invalid';
    }
  }

  const complete =
    info.name.trim() !== '' &&
    info.board.trim() !== '' &&
    loads.every((load) => load.description.trim() !== '' && load.power.trim() !== '');

  async function run() {
    if (session.kind !== 'ready' || !complete) return;
    const profile = parsedProfile();
    if (profile === 'invalid') {
      setResult({ kind: 'error', outcome: { kind: 'refused', detail: t('profile.invalid') } });
      return;
    }
    setResult({ kind: 'working' });
    setExportError(null);
    const outcome = await designImpl({
      token: session.token,
      request: {
        info: {
          name: info.name.trim(),
          number: info.number.trim(),
          customer: info.customer.trim(),
          consultant: info.consultant.trim(),
          contractor: info.contractor.trim(),
        },
        board: {
          name: info.board.trim(),
          location: info.location.trim() === '' ? null : info.location.trim(),
          supply: {
            voltage_v: info.voltage.trim(),
            phases: Number(info.phases),
            frequency_hz: '50',
            earthing: 'TN-S',
            fault_level_ka: info.faultLevel.trim() === '' ? null : info.faultLevel.trim(),
          },
          loads: loads.map((load) => ({
            description: load.description.trim(),
            load: load.load,
            power_kw: load.power.trim(),
            phases: Number(load.phases),
            power_factor: load.powerFactor.trim() === '' ? null : load.powerFactor.trim(),
          })),
        },
        profile,
      },
    });
    if (outcome.kind === 'designed') {
      setResult({ kind: 'designed', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  async function download(format: ExportFormat) {
    if (session.kind !== 'ready' || result.kind !== 'designed') return;
    const profile = parsedProfile();
    setExportError(null);
    const outcome = await exportImpl({
      token: session.token,
      project: result.response.project,
      format,
      profile: profile === 'invalid' ? null : profile,
    });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      setExportError(outcome.kind === 'refused' && outcome.detail ? outcome.detail : t('error'));
    }
  }

  function infoField(key: keyof Omit<Info, 'phases'>, unit = '', ltr = false) {
    return (
      <Field id={`${id}-${key}`} label={t(`field.${key}`)} unit={unit}>
        <input
          id={`${id}-${key}`}
          dir={ltr ? 'ltr' : undefined}
          inputMode={unit ? 'decimal' : 'text'}
          value={info[key]}
          onChange={(event) => {
            setInfo((current) => ({ ...current, [key]: event.target.value }));
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <AppShell>
      <h1 className="mb-2 text-2xl font-bold tracking-tight">{t('title')}</h1>
      <p className="mb-5 max-w-3xl text-text-muted">{t('intro')}</p>

      {session.kind !== 'ready' && session.kind !== 'starting' && (
        <p role="alert" className="mb-4 text-sm text-danger">
          {t('unavailable')}
        </p>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="mb-6 flex flex-col gap-4"
      >
        <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-5">
          <legend className="px-1 text-sm font-semibold">{t('project')}</legend>
          {infoField('name')}
          {infoField('number', '', true)}
          {infoField('customer')}
          {infoField('consultant')}
          {infoField('contractor')}
        </fieldset>

        <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-5">
          <legend className="px-1 text-sm font-semibold">{t('board')}</legend>
          {infoField('board', '', true)}
          {infoField('location', '', true)}
          {infoField('voltage', 'V', true)}
          <Field id={`${id}-phases`} label={t('field.phases')}>
            <select
              id={`${id}-phases`}
              value={info.phases}
              onChange={(event) => {
                setInfo((c) => ({ ...c, phases: event.target.value as '1' | '3' }));
              }}
              className="input w-full"
            >
              <option value="3">3</option>
              <option value="1">1</option>
            </select>
          </Field>
          {infoField('faultLevel', 'kA', true)}
        </fieldset>

        <fieldset className="card flex flex-col gap-4 p-4 md:p-5">
          <legend className="px-1 text-sm font-semibold">{t('loads')}</legend>
          {loads.map((load, index) => (
            <div
              key={load.key}
              data-testid={`design-load-${String(index)}`}
              className="grid grid-cols-1 gap-3 border-b border-border-subtle pb-4 last:border-b-0 last:pb-0 sm:grid-cols-2 lg:grid-cols-6 lg:items-end"
            >
              <Field id={`${id}-desc-${String(load.key)}`} label={t('field.description')}>
                <input
                  id={`${id}-desc-${String(load.key)}`}
                  value={load.description}
                  onChange={(event) => {
                    updateLoad(load.key, { description: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <Field id={`${id}-kind-${String(load.key)}`} label={t('field.load')}>
                <select
                  id={`${id}-kind-${String(load.key)}`}
                  value={load.load}
                  onChange={(event) => {
                    updateLoad(load.key, { load: event.target.value as LoadKind });
                  }}
                  className="input w-full"
                >
                  {LOAD_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`kind.${kind}`)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field id={`${id}-pow-${String(load.key)}`} label={t('field.power')} unit="kW">
                <input
                  id={`${id}-pow-${String(load.key)}`}
                  inputMode="decimal"
                  dir="ltr"
                  value={load.power}
                  onChange={(event) => {
                    updateLoad(load.key, { power: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <Field id={`${id}-ph-${String(load.key)}`} label={t('field.loadPhases')}>
                <select
                  id={`${id}-ph-${String(load.key)}`}
                  value={load.phases}
                  onChange={(event) => {
                    updateLoad(load.key, { phases: event.target.value as '1' | '3' });
                  }}
                  className="input w-full"
                >
                  <option value="1">1</option>
                  <option value="3">3</option>
                </select>
              </Field>
              <Field id={`${id}-pf-${String(load.key)}`} label={t('field.powerFactor')}>
                <input
                  id={`${id}-pf-${String(load.key)}`}
                  inputMode="decimal"
                  dir="ltr"
                  placeholder="0.9"
                  value={load.powerFactor}
                  onChange={(event) => {
                    updateLoad(load.key, { powerFactor: event.target.value });
                  }}
                  className="input w-full"
                />
              </Field>
              <button
                type="button"
                disabled={loads.length === 1}
                onClick={() => {
                  setLoads((current) => current.filter((l) => l.key !== load.key));
                }}
                className="btn btn-sm self-start lg:self-end"
              >
                {t('remove')}
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
            {t('add')}
          </button>
        </fieldset>

        <details className="card p-4 md:p-5">
          <summary className="cursor-pointer text-sm font-semibold">{t('profile.title')}</summary>
          <p className="my-2 text-sm text-text-muted">{t('profile.help')}</p>
          <label htmlFor={`${id}-profile`} className="sr-only">
            {t('profile.title')}
          </label>
          <textarea
            id={`${id}-profile`}
            dir="ltr"
            rows={6}
            value={profileText}
            placeholder='{"key": "my-company", "name": "My Company", "max_circuits_per_rcd": 8}'
            onChange={(event) => {
              setProfileText(event.target.value);
            }}
            className="input w-full font-mono text-sm"
          />
        </details>

        <div>
          <button
            type="submit"
            disabled={session.kind !== 'ready' || !complete || result.kind === 'working'}
            className="btn btn-primary"
          >
            {result.kind === 'working' ? t('working') : t('submit')}
          </button>
        </div>
      </form>

      {result.kind === 'error' && (
        <p role="alert" className="mb-4 text-sm text-danger" data-testid="design-error">
          {result.outcome.kind === 'refused' && result.outcome.detail
            ? result.outcome.detail
            : t('error')}
        </p>
      )}

      {result.kind === 'designed' && (
        <DesignResult response={result.response} onExport={download} exportError={exportError} />
      )}
    </AppShell>
  );
}

function DesignResult({
  response,
  onExport,
  exportError,
}: {
  response: BoardDesignResponse;
  onExport: (format: ExportFormat) => Promise<void>;
  exportError: string | null;
}) {
  const t = useTranslations('design');
  const board = (response.project.boards ?? [])[0];
  if (!board) return null;
  const devices = new Map((board.devices ?? []).map((device) => [device.id, device]));
  const cables = new Map((board.cables ?? []).map((cable) => [cable.id, cable]));

  return (
    <section className="card flex flex-col gap-5 p-4 md:p-5" data-testid="design-result">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" dir="ltr">
          <thead>
            <tr className="border-b border-border-subtle text-start">
              <th className="p-2 text-start">{t('col.circuit')}</th>
              <th className="p-2 text-start">{t('col.phase')}</th>
              <th className="p-2 text-start">{t('col.current')}</th>
              <th className="p-2 text-start">{t('col.breaker')}</th>
              <th className="p-2 text-start">{t('col.rcd')}</th>
              <th className="p-2 text-start">{t('col.cable')}</th>
            </tr>
          </thead>
          <tbody>
            {(board.circuits ?? []).map((circuit) => {
              const breaker = devices.get(circuit.device_ids?.[0] ?? '');
              const rcd = circuit.upstream_id ? devices.get(circuit.upstream_id) : undefined;
              const cable = circuit.cable_id ? cables.get(circuit.cable_id) : undefined;
              return (
                <tr key={circuit.id} className="border-b border-border-subtle last:border-b-0">
                  <td className="p-2">{circuit.description}</td>
                  <td className="p-2">{circuit.phase}</td>
                  <td className="p-2">{`${round(circuit.design_current_a)} A`}</td>
                  <td className="p-2">
                    {breaker
                      ? `-${breaker.designation?.product ?? ''} ${breaker.curve ?? ''}${round(
                          breaker.rated_current_a ?? '',
                        )}`
                      : ''}
                  </td>
                  <td className="p-2">
                    {rcd
                      ? `-${rcd.designation?.product ?? ''} ${round(
                          rcd.residual_current_ma ?? '',
                        )} mA`
                      : '—'}
                  </td>
                  <td className="p-2">
                    {cable
                      ? `${String(cable.cores)}G${round(cable.cross_section_mm2)} ${cable.material}`
                      : ''}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {(board.notes ?? []).length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-text-muted">{t('notes')}</h2>
          <ul className="list-disc ps-5 text-sm" dir="ltr" data-testid="design-notes">
            {(board.notes ?? []).map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">{t('downloads')}</h2>
        <div className="flex flex-wrap gap-2">
          {FORMATS.map((format) => (
            <button
              key={format}
              type="button"
              data-testid={`design-export-${format}`}
              onClick={() => {
                void onExport(format);
              }}
              className="btn btn-sm"
            >
              {t(`format.${format}`)}
            </button>
          ))}
        </div>
        {exportError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {exportError}
          </p>
        )}
      </div>
    </section>
  );
}
