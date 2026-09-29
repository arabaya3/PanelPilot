import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import { inlinedClone, sheetFilename } from '@/components/schematic/export';
import type { SchematicSpec } from '@/components/schematic/layout';
import { SingleLineDiagram } from '@/components/schematic/single-line';
import { EXAMPLE_SCHEDULE, SchematicWorkbench } from '@/components/schematic/workbench';
import { buildSchematic } from '@/lib/schematics';

const NOT_CALCULATED = {
  status: 'not_calculated' as const,
  reason: 'conductor sizing is not sourced',
  blocked_by: 'AI-005, PD-005',
};

/** What the API returns for a small panel (the shape PD-007 defines). */
function specFor(
  components: Array<[string, string, string | null]>,
  overrides: Partial<SchematicSpec> = {},
): SchematicSpec {
  return {
    title: 'Pump station MCC-1',
    supply: '400 V 3~ 50 Hz',
    incomer: components[0]?.[0] ?? 'Q0',
    components: components.map(([designator, kind]) => ({
      designator,
      kind,
      rating: null,
      group: 'main',
      mounting: 'rail' as const,
    })),
    connections: components
      .filter(([, , upstream]) => upstream !== null)
      .map(([designator, , upstream]) => ({
        upstream: upstream as string,
        downstream: designator,
        conductor: NOT_CALCULATED,
      })),
    groups: [
      {
        name: 'main',
        order: 0,
        designators: components.map(([d]) => d),
        rail_rows: { status: 'calculated', display: '1 row (52.5 mm of rail)', source: 'PD-003' },
      },
    ],
    enclosure: {
      status: 'not_calculated',
      reason: 'catalogue not loaded',
      blocked_by: 'PD-001, PD-003',
    },
    trunking: { status: 'not_calculated', reason: 'no fill ratio to cite', blocked_by: 'PD-004' },
    unknown_kinds: [],
    ...overrides,
  };
}

const SMALL = specFor([
  ['Q0', 'isolator', null],
  ['Q1', 'circuit-breaker', 'Q0'],
  ['K1', 'contactor', 'Q1'],
  ['M1', 'motor', 'K1'],
]);

describe('SingleLineDiagram', () => {
  it('draws every device with its symbol', () => {
    renderApp(<SingleLineDiagram spec={SMALL} />, { locale: 'en' });

    for (const designator of ['Q0', 'Q1', 'K1', 'M1']) {
      expect(screen.getByTestId(`symbol-${designator}`)).toBeTruthy();
    }
    expect(screen.getByTestId('schematic-supply').textContent).toBe('400 V 3~ 50 Hz');
  });

  it('marks an uncalculated conductor instead of leaving it blank', () => {
    renderApp(<SingleLineDiagram spec={SMALL} />, { locale: 'en' });

    const label = within(screen.getByTestId('conductor-K1')).getByText('n/c');
    expect(label.getAttribute('data-status')).toBe('not_calculated');
  });

  it('states why each quantity was not calculated, in full', () => {
    renderApp(<SingleLineDiagram spec={SMALL} />, { locale: 'en' });

    const notes = screen.getByTestId('schematic-notes');
    expect(notes.textContent).toContain(
      'Trunking: not calculated [PD-004] — no fill ratio to cite',
    );
    expect(notes.textContent).toContain('Conductor sizes: not calculated [AI-005, PD-005]');
    expect(notes.textContent).toContain('Rail rows, main: 1 row (52.5 mm of rail) (PD-003)');
  });

  it('draws an unknown device type as a marked placeholder and warns first', () => {
    const spec = specFor(
      [
        ['Q0', 'isolator', null],
        ['S1', 'soft-starter', 'Q0'],
      ],
      { unknown_kinds: ['soft-starter'] },
    );
    renderApp(<SingleLineDiagram spec={spec} />, { locale: 'en' });

    expect(screen.getByRole('alert').textContent).toContain('soft-starter');
    expect(screen.getByTestId('symbol-S1').getAttribute('data-known')).toBe('false');
  });

  it('continues a large design across numbered sheets at full size', () => {
    const branches: Array<[string, string, string | null]> = Array.from({ length: 12 }, (_, i) => [
      `Q${String(i + 1)}`,
      'circuit-breaker',
      'W1',
    ]);
    const spec = specFor([['Q0', 'isolator', null], ['W1', 'busbar', 'Q0'], ...branches]);

    renderApp(<SingleLineDiagram spec={spec} />, { locale: 'en' });

    const first = screen.getByTestId('schematic-sheet-1');
    const second = screen.getByTestId('schematic-sheet-2');
    // Real size, not scaled into the container.
    expect(first.getAttribute('width')).toBe(first.getAttribute('viewBox')?.split(' ')[2]);
    expect(within(first).getByText('Sheet 1 of 2')).toBeTruthy();
    expect(within(second).getByText('from W1, sheet 1')).toBeTruthy();
    expect(within(first).getByText('to sheet 2')).toBeTruthy();
  });

  it('keeps the drawing left to right in a right-to-left locale', () => {
    renderApp(<SingleLineDiagram spec={SMALL} />, { locale: 'ar' });

    expect(screen.getByTestId('schematic-sheet-1').getAttribute('direction')).toBe('ltr');
    expect(screen.getByText('الورقة 1 من 1')).toBeTruthy();
  });
});

describe('export', () => {
  it('inlines each element’s computed paint so the image keeps its strokes', () => {
    renderApp(<SingleLineDiagram spec={SMALL} />, { locale: 'en' });
    const sheet = screen.getByTestId('schematic-sheet-1') as unknown as SVGSVGElement;

    const clone = inlinedClone(sheet, () => {
      const style = document.createElement('div').style;
      style.setProperty('stroke', 'var(--color-text)');
      return style;
    });

    const line = clone.querySelector('line');
    expect(line?.getAttribute('stroke')).toBe('var(--color-text)');
    expect(line?.hasAttribute('class')).toBe(false);
    // The page itself is untouched.
    expect(sheet.querySelector('line')?.hasAttribute('stroke')).toBe(false);
  });

  it('names a file after the drawing and sheet', () => {
    expect(sheetFilename('Pump station MCC-1', 2)).toBe('pump-station-mcc-1-sheet-2.png');
    expect(sheetFilename('***', 1)).toBe('schematic-sheet-1.png');
  });
});

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

describe('buildSchematic', () => {
  it('returns the specification', async () => {
    const outcome = await buildSchematic({
      token: 't',
      schedule: EXAMPLE_SCHEDULE,
      fetchImpl: respond(200, SMALL),
    });
    expect(outcome).toEqual({ kind: 'built', spec: SMALL });
  });

  it('sends the bearer token and the schedule', async () => {
    const fetchImpl = respond(200, SMALL);
    await buildSchematic({ token: 'tok', schedule: EXAMPLE_SCHEDULE, fetchImpl });

    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/v1/schematics');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer tok');
    expect(JSON.parse(init.body as string)).toEqual(EXAMPLE_SCHEDULE);
  });

  it('carries the server’s topology problems for an ambiguous schedule', async () => {
    const outcome = await buildSchematic({
      token: 't',
      schedule: EXAMPLE_SCHEDULE,
      fetchImpl: respond(422, { detail: 'ambiguous incomer: Q0, Q1 all have no upstream' }),
    });
    expect(outcome).toEqual({
      kind: 'invalid',
      message: 'ambiguous incomer: Q0, Q1 all have no upstream',
    });
  });

  it('flattens schema errors into one readable line', async () => {
    const outcome = await buildSchematic({
      token: 't',
      schedule: EXAMPLE_SCHEDULE,
      fetchImpl: respond(422, {
        detail: [{ loc: ['body', 'lines', 0, 'designator'], msg: 'String should match pattern' }],
      }),
    });
    expect(outcome).toEqual({
      kind: 'invalid',
      message: 'lines.0.designator: String should match pattern',
    });
  });

  it('refuses a payload that is not a specification', async () => {
    const outcome = await buildSchematic({
      token: 't',
      schedule: EXAMPLE_SCHEDULE,
      fetchImpl: respond(200, { title: 'x' }),
    });
    expect(outcome).toEqual({ kind: 'failed' });
  });

  it('reports a missing endpoint and a network failure distinctly', async () => {
    expect(
      await buildSchematic({ token: 't', schedule: EXAMPLE_SCHEDULE, fetchImpl: respond(404, {}) }),
    ).toEqual({ kind: 'unavailable' });
    expect(
      await buildSchematic({
        token: 't',
        schedule: EXAMPLE_SCHEDULE,
        fetchImpl: vi.fn().mockRejectedValue(new TypeError('offline')),
      }),
    ).toEqual({ kind: 'failed' });
  });
});

describe('SchematicWorkbench', () => {
  it('draws the example schedule', async () => {
    renderApp(<SchematicWorkbench token="t" fetchImpl={respond(200, SMALL)} />, { locale: 'en' });

    fireEvent.click(screen.getByRole('button', { name: 'Draw schematic' }));

    await waitFor(() => {
      expect(screen.getByTestId('schematic-sheet-1')).toBeTruthy();
    });
    expect(screen.getByRole('button', { name: 'Download sheet 1 (PNG)' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Print or save as PDF' })).toBeTruthy();
  });

  it('says why a schedule cannot be drawn', async () => {
    renderApp(
      <SchematicWorkbench
        token="t"
        fetchImpl={respond(422, { detail: 'no incomer: every line names an upstream' })}
      />,
      { locale: 'en' },
    );

    fireEvent.click(screen.getByRole('button', { name: 'Draw schematic' }));

    await waitFor(() => {
      expect(screen.getByTestId('schematic-error').textContent).toContain('no incomer');
    });
  });

  it('catches malformed JSON before calling the server', () => {
    const fetchImpl = respond(200, SMALL);
    renderApp(<SchematicWorkbench token="t" fetchImpl={fetchImpl} />, { locale: 'en' });

    fireEvent.change(screen.getByTestId('schedule-input'), { target: { value: '{ nope' } });
    fireEvent.click(screen.getByRole('button', { name: 'Draw schematic' }));

    expect(screen.getByTestId('schematic-error').textContent).toContain('not valid JSON');
    expect(fetchImpl).not.toHaveBeenCalled();
  });
});
