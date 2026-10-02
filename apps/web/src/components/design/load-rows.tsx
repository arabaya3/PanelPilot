'use client';

import { useTranslations } from 'next-intl';

import { Field } from '@/components/cable-sizing-panel';
import { LOAD_KINDS, MOTOR_KINDS, STARTERS, type Load } from '@/components/design/schedule';
import type { LoadKind, MotorStarter } from '@/lib/design';

/**
 * One board's load schedule as editable rows: what each circuit feeds, its
 * power, phases, power factor and whether the PLC switches it.
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
  return (
    <>
      {loads.map((load, index) => {
        const field = (name: string) => `${idPrefix}-${name}-${String(load.key)}`;
        return (
          <div
            key={load.key}
            data-testid={`design-load-${String(index)}`}
            className="grid grid-cols-1 gap-3 border-b border-border-subtle pb-4 last:border-b-0 last:pb-0 sm:grid-cols-2 lg:grid-cols-9 lg:items-end"
          >
            <Field id={field('desc')} label={t('field.description')}>
              <input
                id={field('desc')}
                value={load.description}
                onChange={(event) => {
                  onChange(load.key, { description: event.target.value });
                }}
                className="input w-full"
              />
            </Field>
            <Field id={field('kind')} label={t('field.load')}>
              <select
                id={field('kind')}
                value={load.load}
                onChange={(event) => {
                  const kind = event.target.value as LoadKind;
                  // A starter belongs to a motor; another kind of load has none.
                  onChange(load.key, {
                    load: kind,
                    starter: MOTOR_KINDS.includes(kind) ? load.starter : '',
                  });
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
            <Field id={field('pow')} label={t('field.power')} unit="kW">
              <input
                id={field('pow')}
                inputMode="decimal"
                dir="ltr"
                value={load.power}
                onChange={(event) => {
                  onChange(load.key, { power: event.target.value });
                }}
                className="input w-full"
              />
            </Field>
            <Field id={field('ph')} label={t('field.loadPhases')}>
              <select
                id={field('ph')}
                value={load.phases}
                onChange={(event) => {
                  onChange(load.key, { phases: event.target.value as '1' | '3' });
                }}
                className="input w-full"
              >
                <option value="1">1</option>
                <option value="3">3</option>
              </select>
            </Field>
            <Field id={field('pf')} label={t('field.powerFactor')}>
              <input
                id={field('pf')}
                inputMode="decimal"
                dir="ltr"
                placeholder="0.9"
                value={load.powerFactor}
                onChange={(event) => {
                  onChange(load.key, { powerFactor: event.target.value });
                }}
                className="input w-full"
              />
            </Field>
            <Field id={field('len')} label={t('field.length')} unit="m">
              <input
                id={field('len')}
                inputMode="decimal"
                dir="ltr"
                value={load.length}
                onChange={(event) => {
                  onChange(load.key, { length: event.target.value });
                }}
                className="input w-full"
              />
            </Field>
            <Field id={field('starter')} label={t('field.starter')}>
              <select
                id={field('starter')}
                value={load.starter}
                disabled={!MOTOR_KINDS.includes(load.load)}
                onChange={(event) => {
                  const starter = event.target.value as MotorStarter | '';
                  // Every starter here is for a three-phase motor.
                  onChange(load.key, starter ? { starter, phases: '3' } : { starter });
                }}
                className="input w-full"
              >
                <option value="">{t('starter.none')}</option>
                {STARTERS.map((starter) => (
                  <option key={starter} value={starter}>
                    {t(`starter.${starter}`)}
                  </option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 text-sm lg:pb-2">
              <input
                type="checkbox"
                data-testid={`design-controlled-${String(index)}`}
                checked={load.controlled}
                onChange={(event) => {
                  onChange(load.key, { controlled: event.target.checked });
                }}
              />
              {t('field.controlled')}
            </label>
            <button
              type="button"
              disabled={loads.length === 1}
              onClick={() => {
                onRemove(load.key);
              }}
              className="btn btn-sm btn-secondary self-start lg:self-end"
            >
              {t('remove')}
            </button>
          </div>
        );
      })}
      <button type="button" onClick={onAdd} className="btn btn-sm btn-secondary self-start">
        {t('add')}
      </button>
    </>
  );
}
