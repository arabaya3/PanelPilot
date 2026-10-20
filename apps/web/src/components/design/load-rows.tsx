'use client';

import { useTranslations } from 'next-intl';

import { LOAD_KINDS, MOTOR_KINDS, STARTERS, type Load } from '@/components/design/schedule';
import { PlusIcon } from '@/components/icons';
import { TableFrame } from '@/components/ui';
import type { LoadKind, MotorStarter } from '@/lib/design';

type RowProblem = 'positive' | 'powerFactor';

/**
 * What is wrong with a number as typed, or null. An empty optional field is
 * not a problem, and an empty power is left to the form's own "incomplete"
 * hint; this only catches a value the API would refuse.
 */
export function numberProblem(text: string, kind: 'positive' | 'powerFactor'): RowProblem | null {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value <= 0)
    return kind === 'powerFactor' ? 'powerFactor' : 'positive';
  if (kind === 'powerFactor' && value > 1) return 'powerFactor';
  return null;
}

/**
 * One board's load schedule as a dense table: what each circuit feeds, its
 * power, phases, power factor, cable length, starter and whether the PLC
 * switches it. Units sit in the column headers; a value the API would refuse
 * is flagged beside its input. The table scrolls in its own frame on a phone.
 */
export function LoadRows({
  idPrefix,
  loads,
  onChange,
  onRemove,
  onAdd,
}: {
  idPrefix: string;
  loads: Load[];
  onChange: (key: number, patch: Partial<Load>) => void;
  onRemove: (key: number) => void;
  onAdd: () => void;
}) {
  const t = useTranslations('design');
  const tw = useTranslations('workspace.schedule');
  const powerLabel = `${t('field.power')} (kW)`;
  const lengthLabel = `${t('field.length')} (m)`;

  function problem(id: string, which: RowProblem | null) {
    if (which === null) return null;
    return (
      <p id={`${id}-error`} className="field-error mt-1 text-xs">
        {tw(`problem.${which}`)}
      </p>
    );
  }

  return (
    <>
      <TableFrame label={tw('tableLabel')}>
        <table className="data-table w-full min-w-[60rem] text-sm">
          <thead>
            <tr>
              <th className="p-2 text-start" scope="col">
                #
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.description')}
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.load')}
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.power')} <bdi dir="ltr">(kW)</bdi>
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.loadPhases')}
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.powerFactor')} <bdi dir="ltr">(cos φ)</bdi>
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.length')} <bdi dir="ltr">(m)</bdi>
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.starter')}
              </th>
              <th className="p-2 text-start" scope="col">
                {t('field.controlled')}
              </th>
              <th className="p-2" scope="col">
                <span className="sr-only">{t('remove')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {loads.map((load, index) => {
              const field = (name: string) => `${idPrefix}-${name}-${String(load.key)}`;
              const powerProblem = numberProblem(load.power, 'positive');
              const pfProblem = numberProblem(load.powerFactor, 'powerFactor');
              const lengthProblem = numberProblem(load.length, 'positive');
              return (
                <tr key={load.key} data-testid={`design-load-${String(index)}`}>
                  <td className="p-2 font-mono text-text-muted" dir="ltr">
                    {index + 1}
                  </td>
                  <td className="p-2">
                    <input
                      id={field('desc')}
                      aria-label={t('field.description')}
                      value={load.description}
                      onChange={(event) => {
                        onChange(load.key, { description: event.target.value });
                      }}
                      className="input w-full min-w-40"
                    />
                  </td>
                  <td className="p-2">
                    <select
                      id={field('kind')}
                      aria-label={t('field.load')}
                      value={load.load}
                      onChange={(event) => {
                        const kind = event.target.value as LoadKind;
                        // A starter belongs to a motor; another kind of load has none.
                        onChange(load.key, {
                          load: kind,
                          starter: MOTOR_KINDS.includes(kind) ? load.starter : '',
                        });
                      }}
                      className="input w-full min-w-32"
                    >
                      {LOAD_KINDS.map((kind) => (
                        <option key={kind} value={kind}>
                          {t(`kind.${kind}`)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-2">
                    <input
                      id={field('pow')}
                      aria-label={powerLabel}
                      inputMode="decimal"
                      dir="ltr"
                      value={load.power}
                      aria-invalid={powerProblem ? true : undefined}
                      aria-describedby={powerProblem ? `${field('pow')}-error` : undefined}
                      onChange={(event) => {
                        onChange(load.key, { power: event.target.value });
                      }}
                      className="input w-24"
                    />
                    {problem(field('pow'), powerProblem)}
                  </td>
                  <td className="p-2">
                    <select
                      id={field('ph')}
                      aria-label={t('field.loadPhases')}
                      value={load.phases}
                      onChange={(event) => {
                        onChange(load.key, { phases: event.target.value as '1' | '3' });
                      }}
                      className="input w-20"
                    >
                      <option value="1">1</option>
                      <option value="3">3</option>
                    </select>
                  </td>
                  <td className="p-2">
                    <input
                      id={field('pf')}
                      aria-label={t('field.powerFactor')}
                      inputMode="decimal"
                      dir="ltr"
                      placeholder="0.9"
                      value={load.powerFactor}
                      aria-invalid={pfProblem ? true : undefined}
                      aria-describedby={pfProblem ? `${field('pf')}-error` : undefined}
                      onChange={(event) => {
                        onChange(load.key, { powerFactor: event.target.value });
                      }}
                      className="input w-20"
                    />
                    {problem(field('pf'), pfProblem)}
                  </td>
                  <td className="p-2">
                    <input
                      id={field('len')}
                      aria-label={lengthLabel}
                      inputMode="decimal"
                      dir="ltr"
                      value={load.length}
                      aria-invalid={lengthProblem ? true : undefined}
                      aria-describedby={lengthProblem ? `${field('len')}-error` : undefined}
                      onChange={(event) => {
                        onChange(load.key, { length: event.target.value });
                      }}
                      className="input w-20"
                    />
                    {problem(field('len'), lengthProblem)}
                  </td>
                  <td className="p-2">
                    <select
                      id={field('starter')}
                      aria-label={t('field.starter')}
                      value={load.starter}
                      disabled={!MOTOR_KINDS.includes(load.load)}
                      onChange={(event) => {
                        const starter = event.target.value as MotorStarter | '';
                        // Every starter here is for a three-phase motor.
                        onChange(load.key, starter ? { starter, phases: '3' } : { starter });
                      }}
                      className="input w-full min-w-32"
                    >
                      <option value="">{t('starter.none')}</option>
                      {STARTERS.map((starter) => (
                        <option key={starter} value={starter}>
                          {t(`starter.${starter}`)}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="p-2 text-center">
                    <input
                      type="checkbox"
                      aria-label={t('field.controlled')}
                      data-testid={`design-controlled-${String(index)}`}
                      checked={load.controlled}
                      onChange={(event) => {
                        onChange(load.key, { controlled: event.target.checked });
                      }}
                    />
                  </td>
                  <td className="p-2">
                    <button
                      type="button"
                      disabled={loads.length === 1}
                      onClick={() => {
                        onRemove(load.key);
                      }}
                      className="btn btn-sm btn-ghost"
                    >
                      {t('remove')}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableFrame>
      <button type="button" onClick={onAdd} className="btn btn-sm btn-secondary self-start">
        <PlusIcon width="16" height="16" aria-hidden="true" />
        {t('add')}
      </button>
    </>
  );
}
