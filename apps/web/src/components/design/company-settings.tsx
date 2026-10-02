'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import {
  parseSettings,
  readSetting,
  settingsText,
  writeSetting,
  type Settings,
} from '@/components/design/company-settings-model';
import { useOutcomeText } from '@/components/design/note-text';
import { LOAD_KINDS } from '@/components/design/schedule';
import { getCompanySettings, saveCompanySettings, type CompanySettings } from '@/lib/design';

/** A setting the form edits: its path, label key, unit, and the default it shows. */
type Setting = { path: string[]; label: string; unit?: string; placeholder?: string };

const COMPANY: Setting[] = [
  { path: ['key'], label: 'key', placeholder: 'my-company' },
  { path: ['name'], label: 'name' },
  { path: ['rules_confirmed_by'], label: 'confirmedBy' },
];

const RULES: Setting[] = [
  { path: ['discrimination_ratio'], label: 'discrimination', placeholder: '1.6' },
  { path: ['max_circuits_per_rcd'], label: 'circuitsPerRcd', placeholder: '6' },
  { path: ['spare_ways_percent'], label: 'spareWays', unit: '%', placeholder: '20' },
  { path: ['max_phase_imbalance_percent'], label: 'imbalance', unit: '%', placeholder: '10' },
  { path: ['default_max_voltage_drop_percent'], label: 'drop', unit: '%', placeholder: '5' },
  {
    path: ['max_voltage_drop_percent', 'lighting'],
    label: 'dropLighting',
    unit: '%',
    placeholder: '3',
  },
  {
    path: ['max_starting_voltage_drop_percent'],
    label: 'dropStarting',
    unit: '%',
    placeholder: '15',
  },
  { path: ['usable_rail_mm'], label: 'rail', unit: 'mm' },
];

/** The default profile's rule per kind of load, shown as each cell's placeholder. */
const DEFAULT_RULES: Partial<Record<string, Record<string, string>>> = {
  lighting: { breaker_a: '10', residual_current_ma: '30', cable_mm2: '1.5', curve: 'C' },
  socket: { breaker_a: '16', residual_current_ma: '30', cable_mm2: '2.5', curve: 'C' },
  air_conditioning: { breaker_a: '16', residual_current_ma: '30', cable_mm2: '2.5', curve: 'C' },
  water_heater: { residual_current_ma: '30', curve: 'C' },
  kitchen: { residual_current_ma: '30', curve: 'C' },
  fan: { residual_current_ma: '30', curve: 'C' },
  motor: { curve: 'D' },
  lift: { curve: 'D' },
  control: { breaker_a: '6', cable_mm2: '1.5', curve: 'C' },
  data: { breaker_a: '16', cable_mm2: '2.5', curve: 'C' },
};

const COLUMNS = ['breaker_a', 'curve', 'residual_current_ma', 'cable_mm2', 'demand'] as const;
const CURVES = ['B', 'C', 'D'] as const;

/**
 * The company's settings as a form rather than JSON: who the company is, its
 * design rules, and per kind of load its breaker, curve, RCD, minimum cable
 * and demand factor. A blank field keeps the default, shown greyed. The
 * settings are saved for the company, so every later project starts from
 * them; the JSON stays open underneath for anything the form does not hold.
 */
export function CompanySettingsPanel({
  token,
  text,
  onText,
  onLoaded,
  language,
  impls = {},
}: {
  token: string | null;
  /** The settings as JSON text, which this form and the design share. */
  text: string;
  onText: (text: string) => void;
  /** The company's saved settings, once read, for a form still blank. */
  onLoaded: (settings: Record<string, unknown>) => void;
  /** The drawing language, saved with the settings. */
  language: string;
  impls?: Partial<{ load: typeof getCompanySettings; save: typeof saveCompanySettings }>;
}) {
  const t = useTranslations('design.settings');
  const kindName = useTranslations('design.kind');
  const say = useOutcomeText();
  const id = useId();
  const { load = getCompanySettings, save = saveCompanySettings } = impls;
  const [saved, setSaved] = useState<CompanySettings | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const parsed = parseSettings(text);
  // Read once per session: the latest callback, without re-reading when it changes.
  const loaded = useRef(onLoaded);
  loaded.current = onLoaded;

  useEffect(() => {
    if (token === null) return;
    let live = true;
    void load({ token }).then((outcome) => {
      if (!live || outcome.kind !== 'settings') return;
      setSaved(outcome.saved);
      if (outcome.saved.settings) loaded.current(outcome.saved.settings);
    });
    return () => {
      live = false;
    };
  }, [token, load]);

  function change(path: string[], value: string) {
    if (parsed === 'invalid') return;
    onText(settingsText(writeSetting(parsed, path, value)));
  }

  async function store() {
    if (token === null || parsed === 'invalid') return;
    setWorking(true);
    setMessage(null);
    const settings: Settings = { ...parsed, ...(language === 'en' ? {} : { language }) };
    const outcome = await save({ token, settings });
    setWorking(false);
    if (outcome.kind === 'settings') {
      setSaved(outcome.saved);
      setMessage(t('saved'));
    } else {
      setMessage(say(outcome));
    }
  }

  function input(setting: Setting, key: string) {
    const fieldId = `${id}-${key}`;
    return (
      <Field key={key} id={fieldId} label={t(`field.${setting.label}`)} unit={setting.unit ?? ''}>
        <input
          id={fieldId}
          dir="auto"
          value={parsed === 'invalid' ? '' : readSetting(parsed, setting.path)}
          placeholder={setting.placeholder}
          disabled={parsed === 'invalid'}
          onChange={(event) => {
            change(setting.path, event.target.value);
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  const settings = parsed === 'invalid' ? {} : parsed;
  return (
    <div className="flex flex-col gap-4" data-testid="company-settings">
      <p className="text-sm text-text-muted">{t('help')}</p>
      {parsed === 'invalid' && (
        <p role="alert" className="text-sm text-danger">
          {t('invalid')}
        </p>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        {COMPANY.map((setting) => input(setting, setting.path.join('.')))}
      </div>
      <h3 className="text-sm font-semibold">{t('rules')}</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {RULES.map((setting) => input(setting, setting.path.join('.')))}
      </div>
      <h3 className="text-sm font-semibold">{t('perKind')}</h3>
      <div className="flex flex-col gap-2">
        <div className="grid grid-cols-5 gap-1 text-xs text-text-muted" aria-hidden="true">
          {COLUMNS.map((column) => (
            <span key={column}>{t(`col.${column}`)}</span>
          ))}
        </div>
        {LOAD_KINDS.map((kind) => {
          const defaults = DEFAULT_RULES[kind] ?? {};
          const label = kindName(kind);
          const cell = (column: string, path: string[], placeholder: string) => (
            <input
              key={column}
              aria-label={`${label}: ${t(`col.${column}`)}`}
              dir="ltr"
              inputMode="decimal"
              value={readSetting(settings, path)}
              placeholder={placeholder}
              disabled={parsed === 'invalid'}
              onChange={(event) => {
                change(path, event.target.value);
              }}
              className="input w-full min-w-0 px-1"
            />
          );
          return (
            <div
              key={kind}
              className="flex flex-col gap-1 border-b border-border-subtle pb-2 last:border-b-0"
            >
              <span className="text-sm font-medium">{label}</span>
              <div className="grid grid-cols-5 gap-1">
                {cell('breaker_a', ['circuit_rules', kind, 'breaker_a'], defaults.breaker_a ?? '')}
                <select
                  aria-label={`${label}: ${t('col.curve')}`}
                  value={readSetting(settings, ['circuit_rules', kind, 'curve'])}
                  disabled={parsed === 'invalid'}
                  onChange={(event) => {
                    change(['circuit_rules', kind, 'curve'], event.target.value);
                  }}
                  className="input w-full min-w-0 px-1"
                >
                  <option value="">{defaults.curve ?? 'C'}</option>
                  {CURVES.map((curve) => (
                    <option key={curve} value={curve}>
                      {curve}
                    </option>
                  ))}
                </select>
                {cell(
                  'residual_current_ma',
                  ['circuit_rules', kind, 'residual_current_ma'],
                  defaults.residual_current_ma ?? '',
                )}
                {cell('cable_mm2', ['circuit_rules', kind, 'cable_mm2'], defaults.cable_mm2 ?? '')}
                {cell('demand', ['demand_factors', kind], '1')}
              </div>
            </div>
          );
        })}
      </div>
      <details>
        <summary className="cursor-pointer text-sm font-semibold">{t('json')}</summary>
        <p className="my-2 text-sm text-text-muted">{t('jsonHelp')}</p>
        <label htmlFor={`${id}-json`} className="sr-only">
          {t('json')}
        </label>
        <textarea
          id={`${id}-json`}
          dir="ltr"
          rows={6}
          value={text}
          placeholder='{"key": "my-company", "name": "My Company", "max_circuits_per_rcd": 8}'
          onChange={(event) => {
            onText(event.target.value);
          }}
          className="input w-full font-mono text-sm"
        />
      </details>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={token === null || parsed === 'invalid' || working}
          onClick={() => {
            void store();
          }}
          className="btn btn-secondary btn-sm"
        >
          {t('save')}
        </button>
        {saved?.updated_at && (
          <span className="text-sm text-text-muted">
            {saved.updated_by
              ? t('savedBy', { name: saved.updated_by, date: saved.updated_at.slice(0, 10) })
              : t('savedOn', { date: saved.updated_at.slice(0, 10) })}
          </span>
        )}
      </div>
      {message && (
        <p role="status" className="text-sm" data-testid="settings-message">
          {message}
        </p>
      )}
    </div>
  );
}
