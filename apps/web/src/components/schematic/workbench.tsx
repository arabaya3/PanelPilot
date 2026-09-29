'use client';

/**
 * Schedule in, drawing out: the page body for the single-line schematic.
 *
 * The schedule is edited as JSON for now — the same shape the API documents —
 * because the product has no panel-design form yet and a form built ahead of
 * the calc tools would have to be redesigned when they land. The example it
 * starts with is a real small motor-control panel, so the drawing shows what
 * a design looks like, uncalculated quantities included.
 */
import { useTranslations } from 'next-intl';
import { useCallback, useRef, useState } from 'react';

import { download, sheetFilename, sheetToPng } from './export';
import { SingleLineDiagram } from './single-line';

import { buildSchematic, type SchematicRequest, type SchematicSpec } from '@/lib/schematics';

/** A representative panel: incomer, busbar, two motor circuits, controls, terminals. */
export const EXAMPLE_SCHEDULE: SchematicRequest = {
  title: 'Pump station MCC-1',
  supply: '400 V 3~ 50 Hz',
  usable_rail_mm: '465',
  lines: [
    { designator: 'Q0', kind: 'isolator', rating: '63 A', group: 'incoming', mounting: 'door' },
    { designator: 'W1', kind: 'busbar', group: 'incoming', feeds_from: 'Q0', mounting: 'field' },
    {
      designator: 'Q1',
      kind: 'circuit-breaker',
      rating: 'C16',
      group: 'pump 1',
      feeds_from: 'W1',
      din: { category: 'mcb', series: 'ABB S200', poles: 3 },
      mounting: 'rail',
    },
    {
      designator: 'K1',
      kind: 'contactor',
      rating: 'AC-3 9 A',
      group: 'pump 1',
      feeds_from: 'Q1',
      mounting: 'rail',
    },
    {
      designator: 'F1',
      kind: 'overload-relay',
      rating: '6–9 A',
      group: 'pump 1',
      feeds_from: 'K1',
      mounting: 'rail',
    },
    {
      designator: 'M1',
      kind: 'motor',
      rating: '4 kW',
      group: 'pump 1',
      feeds_from: 'F1',
      mounting: 'field',
    },
    {
      designator: 'Q2',
      kind: 'circuit-breaker',
      rating: 'C32',
      group: 'pump 2',
      feeds_from: 'W1',
      din: { category: 'mcb', series: 'ABB S200', poles: 3 },
      mounting: 'rail',
    },
    {
      designator: 'U2',
      kind: 'vfd',
      rating: '7.5 kW',
      group: 'pump 2',
      feeds_from: 'Q2',
      mounting: 'field',
    },
    {
      designator: 'M2',
      kind: 'motor',
      rating: '7.5 kW',
      group: 'pump 2',
      feeds_from: 'U2',
      mounting: 'field',
    },
    {
      designator: 'Q3',
      kind: 'circuit-breaker',
      rating: 'C6',
      group: 'control',
      feeds_from: 'W1',
      din: { category: 'mcb', series: 'ABB S200', poles: 1 },
      mounting: 'rail',
    },
    {
      designator: 'H1',
      kind: 'indicator-lamp',
      rating: '24 V',
      group: 'control',
      feeds_from: 'Q3',
      mounting: 'door',
    },
  ],
};

type Outcome =
  | { kind: 'idle' }
  | { kind: 'drawing' }
  | { kind: 'drawn'; spec: SchematicSpec }
  | { kind: 'error'; message: string };

export interface SchematicWorkbenchProps {
  token: string;
  fetchImpl?: typeof fetch;
}

/** The schedule editor, the drawing, and its exports. */
export function SchematicWorkbench({ token, fetchImpl }: SchematicWorkbenchProps) {
  const t = useTranslations('schematic');
  const [text, setText] = useState(() => JSON.stringify(EXAMPLE_SCHEDULE, null, 2));
  const [outcome, setOutcome] = useState<Outcome>({ kind: 'idle' });
  const [exportError, setExportError] = useState<number | null>(null);
  const sheets = useRef(new Map<number, SVGSVGElement>());

  const draw = useCallback(async () => {
    let schedule: SchematicRequest;
    try {
      schedule = JSON.parse(text) as SchematicRequest;
    } catch (error) {
      setOutcome({
        kind: 'error',
        message: t('badJson', { error: error instanceof Error ? error.message : '' }),
      });
      return;
    }
    setOutcome({ kind: 'drawing' });
    const result = await buildSchematic({ token, schedule, ...(fetchImpl ? { fetchImpl } : {}) });
    switch (result.kind) {
      case 'built':
        setOutcome({ kind: 'drawn', spec: result.spec });
        return;
      case 'invalid':
        setOutcome({ kind: 'error', message: t('invalid', { message: result.message }) });
        return;
      case 'unavailable':
        setOutcome({ kind: 'error', message: t('unavailable') });
        return;
      case 'failed':
        setOutcome({ kind: 'error', message: t('failed') });
    }
  }, [fetchImpl, t, text, token]);

  const exportSheet = useCallback(async (spec: SchematicSpec, number: number) => {
    const sheet = sheets.current.get(number);
    if (sheet === undefined) return;
    setExportError(null);
    try {
      const background = window.getComputedStyle(sheet).backgroundColor || 'white';
      download(await sheetToPng(sheet, background), sheetFilename(spec.title, number));
    } catch {
      setExportError(number);
    }
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <label className="flex flex-col gap-2 print:hidden">
        <span className="text-sm font-semibold">{t('scheduleLabel')}</span>
        <textarea
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          rows={14}
          spellCheck={false}
          dir="ltr"
          data-testid="schedule-input"
          className="w-full rounded-md border border-border bg-surface p-3 font-mono text-xs text-text"
        />
      </label>

      <div className="flex flex-wrap gap-2 print:hidden">
        <button
          type="button"
          onClick={() => void draw()}
          disabled={outcome.kind === 'drawing'}
          className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-accent-contrast disabled:opacity-50"
        >
          {outcome.kind === 'drawing' ? t('drawing') : t('draw')}
        </button>
        {outcome.kind === 'drawn' && (
          <button
            type="button"
            onClick={() => {
              window.print();
            }}
            className="rounded-md border border-border px-3 py-1.5 text-sm"
          >
            {t('print')}
          </button>
        )}
      </div>

      {outcome.kind === 'error' && (
        <p
          role="alert"
          data-testid="schematic-error"
          className="rounded-md border border-severity-critical bg-severity-critical-surface p-3 text-sm text-severity-critical"
        >
          {outcome.message}
        </p>
      )}

      {outcome.kind === 'drawn' && (
        <>
          <SingleLineDiagram
            spec={outcome.spec}
            sheetRef={(number, element) => {
              if (element === null) sheets.current.delete(number);
              else sheets.current.set(number, element);
            }}
          />
          <div className="flex flex-wrap gap-2 print:hidden">
            {Array.from({ length: Math.max(1, sheets.current.size) }, (_, index) => index + 1).map(
              (number) => (
                <button
                  key={number}
                  type="button"
                  onClick={() => void exportSheet(outcome.spec, number)}
                  className="rounded-md border border-border px-3 py-1.5 text-sm"
                >
                  {t('exportPng', { number })}
                </button>
              ),
            )}
          </div>
          {exportError !== null && (
            <p role="alert" className="text-sm text-severity-critical">
              {t('exportFailed', { number: exportError })}
            </p>
          )}
        </>
      )}
    </div>
  );
}
