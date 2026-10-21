'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import {
  CalcErrorNotice,
  checkInputs,
  clearedFor,
  Field,
  fieldErrorsFrom,
  focusFirst,
  round,
  Sources,
  useCalcText,
  type FieldError,
} from '@/components/cable-sizing-panel';
import {
  buildBom,
  listDriveRanges,
  type DriveRangeSummary,
  type PanelBomOutcome,
  type PanelBomRequest,
  type PanelBomResponse,
} from '@/lib/calculations';
import { acquireTrial } from '@/lib/session';
import { EmptyState, fieldDescribedBy, Notice, TableFrame } from '@/components/ui';

type Constraints = PanelBomRequest['constraints'];
type Placement = Constraints['placement'];
type Method = Constraints['cable_installation_method'];
type Material = NonNullable<Constraints['cable_material']>;

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

type Start = '' | 'dol' | 'star_delta' | 'dol_heavy';

type Load = {
  key: number;
  tag: string;
  description: string;
  power: string;
  current: string;
  dissipation: string;
  variableSpeed: boolean;
  start: Start;
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
  voltage: string;
  material: Material;
  /** A drive range key; empty for the default (ABB ACS880-01). */
  range: string;
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
  voltage: '400',
  material: 'copper',
  range: '',
};

type EnclosureKey =
  'width' | 'height' | 'depth' | 'ingress' | 'ambient' | 'maxInternal' | 'voltage';

/**
 * An input's error key: the enclosure's by name, a load's as `<field>-<row key>`
 * (`tag-0`, `cur-2`), which is also the suffix of its element id.
 */
type ErrorKey = EnclosureKey | `${'tag' | 'pow' | 'cur' | 'heat'}-${string}`;

/** The enclosure fields a schema check can reject, by the input that holds them. */
const API_FIELDS: Partial<Record<string, ErrorKey>> = {
  width_mm: 'width',
  height_mm: 'height',
  depth_mm: 'depth',
  supply_voltage_v: 'voltage',
  ambient_temp_c: 'ambient',
  max_internal_temp_c: 'maxInternal',
};

/** The coded refusals that are about one enclosure input. */
const CODE_FIELDS: Partial<Record<string, ErrorKey>> = {
  drive_voltage: 'voltage',
  starter_voltage: 'voltage',
};

/** Every input the panel checks, in the order the form shows them. */
function checkPanel(enclosure: Enclosure, loads: Load[]): Partial<Record<ErrorKey, FieldError>> {
  const errors = checkInputs<ErrorKey>([
    ['width', enclosure.width, 'wholePositive'],
    ['height', enclosure.height, 'wholePositive'],
    ['depth', enclosure.depth, 'wholePositive'],
    ['ambient', enclosure.ambient, 'finite'],
    ['maxInternal', enclosure.maxInternal, 'finite'],
    ['voltage', enclosure.voltage, 'positive'],
  ]);
  // With no permitted rise the surface sheds nothing and the cooling balance
  // is undefined; the server refuses it, so say so at the field.
  if (
    !errors.ambient &&
    !errors.maxInternal &&
    Number(enclosure.maxInternal) <= Number(enclosure.ambient)
  ) {
    errors.maxInternal = { problem: 'notAboveAmbient' };
  }
  for (const load of loads) {
    if (load.tag.trim() === '') errors[`tag-${String(load.key)}`] = { problem: 'tagRequired' };
    Object.assign(
      errors,
      checkInputs<ErrorKey>([
        [`pow-${String(load.key)}`, load.power, 'positive', { optional: true }],
        [`cur-${String(load.key)}`, load.current, 'positive', { optional: true }],
        [`heat-${String(load.key)}`, load.dissipation, 'nonNegative', { optional: true }],
      ]),
    );
  }
  return errors;
}

function errorOrder(loads: Load[]): ErrorKey[] {
  return [
    'width',
    'height',
    'depth',
    'ingress',
    'ambient',
    'maxInternal',
    'voltage',
    ...loads.flatMap((load): ErrorKey[] => [
      `tag-${String(load.key)}`,
      `pow-${String(load.key)}`,
      `cur-${String(load.key)}`,
      `heat-${String(load.key)}`,
    ]),
  ];
}

function blankLoad(key: number): Load {
  return {
    key,
    tag: '',
    description: '',
    power: '',
    current: '',
    dissipation: '',
    variableSpeed: false,
    start: '',
  };
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
  listImpl = listDriveRanges,
}: {
  acquireImpl?: typeof acquireTrial;
  buildImpl?: typeof buildBom;
  listImpl?: typeof listDriveRanges;
}) {
  const t = useTranslations('calc');
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [enclosure, setEnclosure] = useState<Enclosure>(INITIAL_ENCLOSURE);
  const [loads, setLoads] = useState<Load[]>([blankLoad(0)]);
  const [nextKey, setNextKey] = useState(1);
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const [ranges, setRanges] = useState<DriveRangeSummary[]>([]);
  const [errors, setErrors] = useState<Partial<Record<ErrorKey, FieldError>>>({});
  const text = useCalcText();
  const id = useId();

  /** Clear an input's error once it is edited; a changed choice clears the server's. */
  function edited(key: string, tableChanged = false) {
    setErrors((current) => clearedFor(current, key, tableChanged));
  }

  function setChoice<K extends keyof Enclosure>(key: K, value: Enclosure[K]) {
    setEnclosure((current) => ({ ...current, [key]: value }));
    edited(key, true);
  }

  /** A text or number input's id, error and its accessible wiring. */
  function wired(key: ErrorKey) {
    const inputId = `${id}-${key}`;
    const error = text.fieldError(errors[key]);
    return { inputId, error, describedBy: fieldDescribedBy(inputId, { error }) };
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

  function updateLoad(key: number, patch: Partial<Load>, errorKey?: ErrorKey) {
    setLoads((current) => current.map((load) => (load.key === key ? { ...load, ...patch } : load)));
    if (errorKey) edited(errorKey);
  }

  async function run() {
    if (session.kind !== 'ready') return;
    const invalid = checkPanel(enclosure, loads);
    setErrors(invalid);
    if (Object.keys(invalid).length > 0) {
      setResult({ kind: 'idle' });
      focusFirst(id, errorOrder(loads), invalid);
      return;
    }
    setResult({ kind: 'working' });
    const outcome = await buildImpl({
      token: session.token,
      request: {
        loads: loads.map((load) => ({
          tag: load.tag.trim(),
          description: load.description.trim(),
          power_kw: load.power.trim() === '' ? null : load.power.trim(),
          current_a: load.current.trim() === '' ? null : load.current.trim(),
          dissipation_w: load.dissipation.trim() === '' ? null : load.dissipation.trim(),
          variable_speed: load.variableSpeed,
          start: load.start === '' ? null : load.start,
        })),
        constraints: {
          width_mm: Number(enclosure.width),
          height_mm: Number(enclosure.height),
          depth_mm: Number(enclosure.depth),
          ingress_rating: enclosure.ingress.trim(),
          placement: enclosure.placement,
          cable_installation_method: enclosure.method,
          supply_voltage_v: enclosure.voltage.trim(),
          cable_material: enclosure.material,
          fault_level_ka: null,
          preferred_vendors: [],
          ...(enclosure.range !== '' ? { drive_range: enclosure.range } : {}),
          ambient_temp_c: enclosure.ambient.trim(),
          max_internal_temp_c: enclosure.maxInternal.trim(),
        },
      },
    });
    if (outcome.kind === 'built') {
      setResult({ kind: 'built', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'refused') {
        const refused = fieldErrorsFrom(outcome, { codes: CODE_FIELDS, fields: API_FIELDS });
        setErrors(refused);
        focusFirst(id, errorOrder(loads), refused);
      }
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  function enclosureField(key: EnclosureKey, unit: string) {
    const { inputId, error, describedBy } = wired(key);
    return (
      <Field id={inputId} label={t(`bom.field.${key}`)} unit={unit} error={error}>
        <input
          id={inputId}
          inputMode={key === 'ingress' ? 'text' : 'decimal'}
          dir="ltr"
          {...describedBy}
          value={enclosure[key]}
          onChange={(event) => {
            setEnclosure((current) => ({ ...current, [key]: event.target.value }));
            edited(key);
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  return (
    <>
      <p className="mb-5 max-w-3xl text-text-muted">{t('bom.intro')}</p>

      {/* Inputs beside results from `lg` up; stacked, inputs first, below. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2 lg:items-start">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run();
          }}
          className="flex min-w-0 flex-col gap-4"
        >
          <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5">
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
                  setChoice('placement', event.target.value as Placement);
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
                  setChoice('method', event.target.value as Method);
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
            {enclosureField('voltage', 'V')}
            <Field id={`${id}-material`} label={t('bom.field.material')}>
              <select
                id={`${id}-material`}
                value={enclosure.material}
                onChange={(event) => {
                  setChoice('material', event.target.value as Material);
                }}
                className="input w-full"
              >
                <option value="copper">{t('material.copper')}</option>
                <option value="aluminium">{t('material.aluminium')}</option>
              </select>
            </Field>
            <Field id={`${id}-range`} label={t('vfd.field.range')}>
              <select
                id={`${id}-range`}
                value={enclosure.range}
                onChange={(event) => {
                  setChoice('range', event.target.value);
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
          </fieldset>

          <fieldset className="card flex flex-col gap-4 p-4 md:p-5">
            <legend className="px-1 text-sm font-semibold">{t('bom.loads')}</legend>
            {loads.map((load, index) => (
              <div
                key={load.key}
                data-testid={`bom-load-${String(index)}`}
                className="grid grid-cols-1 gap-3 border-b border-border-subtle pb-4 last:border-b-0 last:pb-0 sm:grid-cols-2 sm:items-end"
              >
                <Field
                  id={`${id}-tag-${String(load.key)}`}
                  error={text.fieldError(errors[`tag-${String(load.key)}`])}
                  label={t('bom.field.tag')}
                >
                  <input
                    id={`${id}-tag-${String(load.key)}`}
                    {...wired(`tag-${String(load.key)}`).describedBy}
                    dir="ltr"
                    value={load.tag}
                    onChange={(event) => {
                      updateLoad(load.key, { tag: event.target.value }, `tag-${String(load.key)}`);
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
                <Field
                  id={`${id}-pow-${String(load.key)}`}
                  error={text.fieldError(errors[`pow-${String(load.key)}`])}
                  label={t('bom.field.power')}
                  unit="kW"
                >
                  <input
                    id={`${id}-pow-${String(load.key)}`}
                    {...wired(`pow-${String(load.key)}`).describedBy}
                    inputMode="decimal"
                    dir="ltr"
                    value={load.power}
                    onChange={(event) => {
                      updateLoad(
                        load.key,
                        { power: event.target.value },
                        `pow-${String(load.key)}`,
                      );
                    }}
                    className="input w-full"
                  />
                </Field>
                <Field
                  id={`${id}-cur-${String(load.key)}`}
                  error={text.fieldError(errors[`cur-${String(load.key)}`])}
                  label={t('bom.field.current')}
                  unit="A"
                >
                  <input
                    id={`${id}-cur-${String(load.key)}`}
                    {...wired(`cur-${String(load.key)}`).describedBy}
                    inputMode="decimal"
                    dir="ltr"
                    value={load.current}
                    onChange={(event) => {
                      updateLoad(
                        load.key,
                        { current: event.target.value },
                        `cur-${String(load.key)}`,
                      );
                    }}
                    className="input w-full"
                  />
                </Field>
                <Field
                  id={`${id}-heat-${String(load.key)}`}
                  error={text.fieldError(errors[`heat-${String(load.key)}`])}
                  label={t('bom.field.dissipation')}
                  unit="W"
                >
                  <input
                    id={`${id}-heat-${String(load.key)}`}
                    {...wired(`heat-${String(load.key)}`).describedBy}
                    inputMode="decimal"
                    dir="ltr"
                    value={load.dissipation}
                    onChange={(event) => {
                      updateLoad(
                        load.key,
                        { dissipation: event.target.value },
                        `heat-${String(load.key)}`,
                      );
                    }}
                    className="input w-full"
                  />
                </Field>
                <Field id={`${id}-start-${String(load.key)}`} label={t('bom.field.start')}>
                  <select
                    id={`${id}-start-${String(load.key)}`}
                    value={load.start}
                    onChange={(event) => {
                      updateLoad(load.key, { start: event.target.value as Start });
                    }}
                    className="input w-full"
                  >
                    {(['', 'dol', 'star_delta', 'dol_heavy'] as const).map((start) => (
                      <option key={start} value={start}>
                        {t(`bom.start.${start === '' ? 'none' : start}`)}
                      </option>
                    ))}
                  </select>
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
                  className="btn btn-sm btn-secondary self-start lg:self-end"
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
              className="btn btn-sm btn-secondary self-start"
            >
              {t('bom.add')}
            </button>
          </fieldset>

          <div>
            <button
              type="submit"
              disabled={session.kind !== 'ready' || result.kind === 'working'}
              className="btn btn-primary"
            >
              {result.kind === 'working' ? t('working') : t('bom.submit')}
            </button>
          </div>
        </form>

        <div className="flex min-w-0 flex-col gap-4">
          {result.kind === 'idle' && session.kind === 'ready' && (
            <EmptyState title={t('result.idle')} />
          )}

          {session.kind !== 'ready' && session.kind !== 'starting' && (
            <div>
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
            </div>
          )}

          {result.kind === 'error' && (
            <CalcErrorNotice
              outcome={result.outcome}
              hasFieldErrors={Object.keys(errors).length > 0}
              testId="bom-error"
            />
          )}

          {result.kind === 'built' && <BomResult response={result.response} />}
        </div>
      </div>
    </>
  );
}

type BomLine = PanelBomResponse['result']['lines'][number];

/**
 * A line in the page's language, from what the line is and its values. The
 * server's English description is the fallback for a kind this page does
 * not know yet.
 */
function lineText(t: ReturnType<typeof useTranslations<'calc'>>, line: BomLine): string {
  const d = line.details;
  switch (line.kind) {
    case 'drive':
      return t('bom.line.drive', { tag: d.tag ?? '', load: d.load ?? '' });
    case 'cable':
      return t('bom.line.cable', {
        tag: d.tag ?? '',
        load: d.load ?? '',
        method: d.method ?? '',
        grouped: d.grouped ?? '',
      });
    case 'breaker':
      return d.rated_a !== undefined
        ? t('bom.line.feeder', {
            tag: d.tag ?? '',
            load: d.load ?? '',
            rated: d.rated_a,
            curve: d.curve ?? '',
          })
        : t('bom.line.breaker', { tag: d.tag ?? '', load: d.load ?? '', trip: d.trip_a ?? '' });
    case 'contactor':
      return t('bom.line.contactor', {
        tag: d.tag ?? '',
        role: t(`bom.role.${d.role ?? 'line'}` as 'bom.role.line'),
      });
    case 'overload':
      return d.ct !== undefined
        ? t('bom.line.overloadCt', {
            tag: d.tag ?? '',
            min: d.min_a ?? '',
            max: d.max_a ?? '',
            ct: d.ct,
          })
        : t('bom.line.overload', { tag: d.tag ?? '', min: d.min_a ?? '', max: d.max_a ?? '' });
    case 'fuse':
      return t('bom.line.fuse', { tag: d.tag ?? '', amps: d.amps ?? '', minSc: d.min_sc_a ?? '' });
    case 'terminal':
      return t(d.role === 'pe' ? 'bom.line.terminalPe' : 'bom.line.terminal', {
        tag: d.tag ?? '',
        size: d.size ?? '',
        max: d.max_a ?? '',
      });
    case 'enclosure':
      return t('bom.line.enclosure', {
        placement: d.placement
          ? t(`bom.placement.${d.placement}` as 'bom.placement.single_wall')
          : '',
      });
    case 'cooling':
      return t('bom.line.cooling', {
        cooling: d.cooling_w ?? '',
        rise: d.rise_k ?? '',
        qw: d.qw ?? '',
      });
    default:
      return line.description;
  }
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

      {/* A table in its own scrolling frame: the rows are wide, and on a
          phone the frame scrolls rather than the page. */}
      <TableFrame label={t('bom.linesLabel')}>
        <table className="data-table" data-testid="bom-lines">
          <thead>
            <tr>
              <th scope="col">{t('bom.column.part')}</th>
              <th scope="col">{t('bom.column.quantity')}</th>
              <th scope="col">{t('bom.column.description')}</th>
            </tr>
          </thead>
          <tbody>
            {result.lines.map((line, index) => (
              <tr key={index}>
                <td>
                  <bdi dir="ltr" className="whitespace-nowrap font-mono font-semibold">
                    {line.part_reference}
                  </bdi>
                </td>
                <td dir="ltr" className="text-end tabular-nums">
                  ×{line.quantity}
                </td>
                <td dir="auto" className="min-w-64">
                  {lineText(t, line)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableFrame>

      {result.note_keys.length > 0 && (
        <ul className="flex list-disc flex-col gap-1 ps-5 text-sm text-text-muted">
          {result.note_keys.map((key) => (
            <li key={key}>{t(`bom.note.${key}`)}</li>
          ))}
        </ul>
      )}

      <Sources sources={response.sources} />
    </section>
  );
}
