import { describe, expect, it } from 'vitest';

import {
  COLUMN_WIDTH,
  MAX_COLUMNS,
  MAX_ROWS,
  SHEET_HEIGHT,
  SHEET_WIDTH,
  conductorLabel,
  layoutSheets,
  type SchematicSpec,
  type Sheet,
} from '@/components/schematic/layout';
import { SYMBOL_HEIGHT, SYMBOL_WIDTH } from '@/components/schematic/symbols';

const NOT_CALCULATED = {
  status: 'not_calculated' as const,
  reason: 'conductor sizing is not sourced',
  blocked_by: 'AI-005, PD-005',
};

/** Build a spec from `[designator, upstream]` pairs; the first is the incomer. */
function spec(edges: Array<[string, string | null]>, kind = 'circuit-breaker'): SchematicSpec {
  return {
    title: 'Test panel',
    supply: '400 V 3~ 50 Hz',
    incomer: edges[0]?.[0] ?? 'Q0',
    components: edges.map(([designator]) => ({
      designator,
      kind,
      rating: null,
      group: 'main',
      mounting: 'rail' as const,
    })),
    connections: edges
      .filter(([, upstream]) => upstream !== null)
      .map(([designator, upstream]) => ({
        upstream: upstream as string,
        downstream: designator,
        conductor: NOT_CALCULATED,
      })),
    groups: [],
    enclosure: { status: 'not_calculated', reason: 'r', blocked_by: 'PD-003' },
    trunking: { status: 'not_calculated', reason: 'r', blocked_by: 'PD-004' },
    unknown_kinds: [],
  };
}

function placed(sheets: Sheet[]): string[] {
  return sheets.flatMap((sheet) => sheet.nodes.map((node) => node.component.designator));
}

function expectInsideTheSheet(sheet: Sheet): void {
  for (const node of sheet.nodes) {
    expect(node.x).toBeGreaterThanOrEqual(0);
    expect(node.x + SYMBOL_WIDTH).toBeLessThanOrEqual(SHEET_WIDTH);
    expect(node.y + SYMBOL_HEIGHT).toBeLessThanOrEqual(SHEET_HEIGHT);
  }
}

function expectNoOverlaps(sheet: Sheet): void {
  const boxes = sheet.nodes.map((node) => [node.x, node.y] as const);
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const [ax, ay] = boxes[i] as readonly [number, number];
      const [bx, by] = boxes[j] as readonly [number, number];
      const apart = Math.abs(ax - bx) >= SYMBOL_WIDTH || Math.abs(ay - by) >= SYMBOL_HEIGHT;
      expect(apart).toBe(true);
    }
  }
}

describe('layoutSheets', () => {
  it('draws a small panel on one sheet, every device once', () => {
    const sheets = layoutSheets(
      spec([
        ['Q0', null],
        ['W1', 'Q0'],
        ['Q1', 'W1'],
        ['K1', 'Q1'],
        ['M1', 'K1'],
        ['Q2', 'W1'],
        ['M2', 'Q2'],
      ]),
    );

    expect(sheets).toHaveLength(1);
    expect(placed(sheets).sort()).toEqual(['K1', 'M1', 'M2', 'Q0', 'Q1', 'Q2', 'W1']);
    expectInsideTheSheet(sheets[0] as Sheet);
    expectNoOverlaps(sheets[0] as Sheet);
    expect(sheets[0]?.markers).toEqual([]);
  });

  it('draws each device below the one that feeds it', () => {
    const [sheet] = layoutSheets(
      spec([
        ['Q0', null],
        ['Q1', 'Q0'],
        ['K1', 'Q1'],
      ]),
    );
    const y = new Map(sheet?.nodes.map((n) => [n.component.designator, n.y]));

    expect(y.get('Q1')).toBeGreaterThan(y.get('Q0') as number);
    expect(y.get('K1')).toBeGreaterThan(y.get('Q1') as number);
  });

  it('gives every connection a drop carrying its conductor', () => {
    const [sheet] = layoutSheets(
      spec([
        ['Q0', null],
        ['Q1', 'Q0'],
        ['Q2', 'Q0'],
      ]),
    );
    const drops = sheet?.drops.filter((d) => d.upstream === 'Q0') ?? [];

    expect(drops.map((d) => d.downstream).sort()).toEqual(['Q1', 'Q2']);
    expect(drops.every((d) => d.conductor?.status === 'not_calculated')).toBe(true);
  });

  it('continues a wide design on further sheets instead of shrinking it', () => {
    const branches: Array<[string, string]> = Array.from({ length: 20 }, (_, i) => [
      `Q${String(i + 1)}`,
      'W1',
    ]);
    const sheets = layoutSheets(spec([['Q0', null], ['W1', 'Q0'], ...branches]));

    expect(sheets.map((s) => s.number)).toEqual([1, 2, 3]);
    expect(placed(sheets).sort()).toEqual(['Q0', 'W1', ...branches.map(([d]) => d)].sort());
    for (const sheet of sheets) {
      expectInsideTheSheet(sheet);
      expectNoOverlaps(sheet);
      // Same pitch everywhere: nothing was squeezed to fit.
      const xs = [...new Set(sheet.nodes.map((n) => n.x))].sort((a, b) => a - b);
      for (let i = 1; i < xs.length; i += 1) {
        expect((xs[i] as number) - (xs[i - 1] as number)).toBeGreaterThanOrEqual(COLUMN_WIDTH / 2);
      }
      // No more branch circuits than a sheet has columns.
      const branchesHere = sheet.nodes.filter(
        (n) => /^Q\d+$/.test(n.component.designator) && n.component.designator !== 'Q0',
      );
      expect(branchesHere.length).toBeLessThanOrEqual(MAX_COLUMNS);
    }
  });

  it('marks both ends of every cut with the other sheet', () => {
    const branches: Array<[string, string]> = Array.from({ length: 12 }, (_, i) => [
      `Q${String(i + 1)}`,
      'W1',
    ]);
    const [first, second] = layoutSheets(spec([['Q0', null], ['W1', 'Q0'], ...branches]));

    expect(first?.markers).toContainEqual(
      expect.objectContaining({ direction: 'to', designator: 'W1', sheet: 2 }),
    );
    expect(second?.markers).toContainEqual(
      expect.objectContaining({ direction: 'from', designator: 'W1', sheet: 1 }),
    );
  });

  it('continues a chain deeper than one sheet on the next', () => {
    const chain: Array<[string, string | null]> = [['D0', null]];
    for (let i = 1; i < MAX_ROWS + 3; i += 1) chain.push([`D${String(i)}`, `D${String(i - 1)}`]);

    const sheets = layoutSheets(spec(chain));

    expect(sheets).toHaveLength(2);
    expect(placed(sheets).sort()).toEqual(chain.map(([d]) => d).sort());
    expect(sheets[0]?.nodes).toHaveLength(MAX_ROWS);
    expect(sheets[0]?.markers).toContainEqual(
      expect.objectContaining({
        direction: 'to',
        designator: `D${String(MAX_ROWS - 1)}`,
        sheet: 2,
      }),
    );
    for (const sheet of sheets) expectInsideTheSheet(sheet);
  });

  it('places every device exactly once however the tree is shaped', () => {
    // A deterministic pseudo-random tree of 120 devices.
    let seed = 7;
    const next = (): number => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };
    const edges: Array<[string, string | null]> = [['N0', null]];
    for (let i = 1; i < 120; i += 1) {
      const parent = Math.floor(next() * i);
      edges.push([`N${String(i)}`, `N${String(parent)}`]);
    }

    const sheets = layoutSheets(spec(edges));
    const all = placed(sheets);

    expect(all).toHaveLength(edges.length);
    expect(new Set(all).size).toBe(edges.length);
    for (const sheet of sheets) {
      expectInsideTheSheet(sheet);
      expectNoOverlaps(sheet);
    }
  });
});

describe('conductorLabel', () => {
  it('marks an uncalculated size rather than leaving it blank', () => {
    expect(conductorLabel(NOT_CALCULATED)).toBe('n/c');
  });

  it('prints a calculated size as given', () => {
    expect(conductorLabel({ status: 'calculated', display: '4 mm²', source: 'AI-005' })).toBe(
      '4 mm²',
    );
  });

  it('marks a refusal', () => {
    expect(conductorLabel({ status: 'refused', reason: 'out of range' })).toBe('refused');
  });
});
