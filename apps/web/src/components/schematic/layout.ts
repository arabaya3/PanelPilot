/**
 * Laying a schematic specification out across numbered sheets (PD-008).
 *
 * Pure geometry: no DOM, no React, so pagination can be tested exactly.
 *
 * **A sheet never shrinks.** Every symbol is drawn at the same size on every
 * sheet, and a design too large for one sheet continues on the next — the way
 * a hand-drawn single-line diagram splits across numbered pages — with an
 * off-sheet marker at each cut naming where the circuit goes and where it came
 * from. Scaling a large design down to fit would make it illegible at exactly
 * the size it most needs reading.
 *
 * The diagram is a tree drawn top to bottom: the supply at the top, each
 * device below the one that feeds it, branches fanning out left to right in
 * schedule order.
 */
import type { components } from '@panelpilot/shared-types';

import { SYMBOL_HEIGHT, SYMBOL_WIDTH } from './symbols';

export type SchematicSpec = components['schemas']['SchematicSpec'];
export type SchematicComponent = components['schemas']['SchematicComponent'];
export type CalcValue = SchematicSpec['connections'][number]['conductor'];

/** Horizontal space one branch column takes, symbol and labels included. */
export const COLUMN_WIDTH = 150;

/** Vertical space one level of the tree takes, wiring included. */
export const ROW_HEIGHT = 112;

/** Columns on one sheet. Chosen with ROW_HEIGHT for an A-series landscape page. */
export const MAX_COLUMNS = 8;

/** Tree levels on one sheet. */
export const MAX_ROWS = 6;

/** Space around the drawing area. */
export const MARGIN = 40;

/** The band above the first row: the supply, or the incoming off-sheet marker. */
export const TOP_BAND = 72;

/** Title block and notes below the drawing area. */
export const FOOTER_HEIGHT = 150;

export const SHEET_WIDTH = MARGIN * 2 + MAX_COLUMNS * COLUMN_WIDTH;
export const SHEET_HEIGHT = TOP_BAND + MAX_ROWS * ROW_HEIGHT + FOOTER_HEIGHT + MARGIN;

/** How far above a child's symbol its feeder's horizontal bar runs. */
const BAR_OFFSET = 18;

/** A device placed on a sheet. */
export interface PlacedNode {
  component: SchematicComponent;
  /** Top-left corner of the symbol. */
  x: number;
  y: number;
}

/** One drop from a feeder's bar down to the device it feeds. */
export interface Drop {
  upstream: string;
  downstream: string;
  x: number;
  /** From the bar to the top of the fed symbol. */
  y1: number;
  y2: number;
  conductor: CalcValue | null;
}

/** The horizontal distribution bar under a feeder, with its trunk. */
export interface Bar {
  /** The feeder, or the incoming marker's designator on a continuation sheet. */
  from: string;
  /** Where the trunk leaves the feeder (or marker) and meets the bar. */
  trunkX: number;
  trunkY1: number;
  y: number;
  x1: number;
  x2: number;
}

/** Where a circuit leaves or enters a sheet. */
export interface OffSheetMarker {
  direction: 'to' | 'from';
  /** The device whose downstream circuits continue. */
  designator: string;
  /** The other sheet. */
  sheet: number;
  x: number;
  y: number;
}

export interface Sheet {
  number: number;
  nodes: PlacedNode[];
  bars: Bar[];
  drops: Drop[];
  markers: OffSheetMarker[];
}

interface SheetRequest {
  number: number;
  roots: string[];
  /** Absent on sheet 1, whose roots hang from the supply. */
  entry: { from: string; sheet: number } | null;
}

/**
 * Lay a specification out across as many sheets as it needs.
 *
 * @param spec - The schematic specification.
 * @returns Sheets in order, numbered from 1. Every component appears on
 *   exactly one sheet.
 */
export function layoutSheets(spec: SchematicSpec): Sheet[] {
  const byDesignator = new Map(spec.components.map((c) => [c.designator, c]));
  const children = new Map<string, string[]>();
  const conductorOf = new Map<string, CalcValue>();
  for (const connection of spec.connections) {
    const list = children.get(connection.upstream) ?? [];
    list.push(connection.downstream);
    children.set(connection.upstream, list);
    conductorOf.set(connection.downstream, connection.conductor);
  }

  const queue: SheetRequest[] = [{ number: 1, roots: [spec.incomer], entry: null }];
  const sheets: Sheet[] = [];
  let nextNumber = 2;

  while (queue.length > 0) {
    const request = queue.shift() as SheetRequest;
    const sheet: Sheet = { number: request.number, nodes: [], bars: [], drops: [], markers: [] };

    /** Columns a subtree needs on this sheet, cutting at the last row. */
    const width = (designator: string, depth: number): number => {
      const kids = children.get(designator) ?? [];
      if (kids.length === 0 || depth === MAX_ROWS - 1) return 1;
      return Math.max(
        1,
        kids.reduce((sum, kid) => sum + width(kid, depth + 1), 0),
      );
    };

    const columnCentre = (column: number): number => MARGIN + (column + 0.5) * COLUMN_WIDTH;
    const rowTop = (depth: number): number => TOP_BAND + depth * ROW_HEIGHT;

    /** Continue `roots` on a new sheet, fed from `from`. */
    const continueElsewhere = (roots: string[], from: string): number => {
      const number = nextNumber++;
      queue.push({ number, roots, entry: { from, sheet: request.number } });
      return number;
    };

    /**
     * Place a subtree with its left edge at `column`.
     *
     * @returns Columns used, and the centre x of the placed root.
     */
    const placeSubtree = (
      designator: string,
      depth: number,
      column: number,
    ): { used: number; centre: number } => {
      const component = byDesignator.get(designator);
      const kids = children.get(designator) ?? [];
      let used = 1;
      let centre = columnCentre(column);

      if (kids.length > 0 && depth === MAX_ROWS - 1) {
        // Out of rows: everything below continues on its own sheet.
        const target = continueElsewhere(kids, designator);
        sheet.markers.push({
          direction: 'to',
          designator,
          sheet: target,
          x: centre,
          y: rowTop(depth) + SYMBOL_HEIGHT + 8,
        });
      } else if (kids.length > 0) {
        const forest = placeForest(
          kids,
          depth + 1,
          column,
          designator,
          rowTop(depth) + SYMBOL_HEIGHT,
        );
        used = forest.used;
        centre = columnCentre(column) + ((used - 1) * COLUMN_WIDTH) / 2;
        // Re-anchor the trunk now the feeder's centre is known.
        const bar = sheet.bars[forest.barIndex];
        if (bar !== undefined) {
          bar.trunkX = centre;
          bar.x1 = Math.min(bar.x1, centre);
          bar.x2 = Math.max(bar.x2, centre);
        }
      }

      if (component !== undefined) {
        sheet.nodes.push({ component, x: centre - SYMBOL_WIDTH / 2, y: rowTop(depth) });
      }
      return { used, centre };
    };

    /**
     * Place sibling subtrees left to right under one feeder, splitting onto a
     * continuation sheet when they no longer fit.
     *
     * @returns Columns used, and the index of the bar drawn for them.
     */
    const placeForest = (
      roots: string[],
      depth: number,
      startColumn: number,
      from: string,
      trunkTop: number,
    ): { used: number; barIndex: number } => {
      const barY = rowTop(depth) - BAR_OFFSET;
      const barIndex = sheet.bars.length;
      sheet.bars.push({ from, trunkX: 0, trunkY1: trunkTop, y: barY, x1: Infinity, x2: -Infinity });

      let column = startColumn;
      for (let index = 0; index < roots.length; index += 1) {
        const root = roots[index] as string;
        const needed = width(root, depth);
        const remaining = MAX_COLUMNS - column;
        const placedAny = column > startColumn;

        if (placedAny && needed > remaining) {
          // The rest of this feeder's circuits continue on another sheet.
          const target = continueElsewhere(roots.slice(index), from);
          const bar = sheet.bars[barIndex] as Bar;
          // Half a column past the last circuit placed, so the marker sits at
          // the end of the bar it continues rather than over a symbol.
          const x = Math.min(bar.x2 + COLUMN_WIDTH / 2, SHEET_WIDTH - MARGIN / 2);
          sheet.markers.push({ direction: 'to', designator: from, sheet: target, x, y: barY });
          bar.x2 = x;
          break;
        }

        const placed = placeSubtree(root, depth, column);
        sheet.drops.push({
          upstream: from,
          downstream: root,
          x: placed.centre,
          y1: barY,
          y2: rowTop(depth),
          conductor: conductorOf.get(root) ?? null,
        });
        const bar = sheet.bars[barIndex] as Bar;
        bar.x1 = Math.min(bar.x1, placed.centre);
        bar.x2 = Math.max(bar.x2, placed.centre);
        column += placed.used;
      }
      return { used: Math.max(1, column - startColumn), barIndex };
    };

    if (request.entry === null) {
      // Sheet 1: the supply feeds the incomer directly.
      const placed = placeSubtree(request.roots[0] as string, 0, 0);
      sheet.bars.push({
        from: 'supply',
        trunkX: placed.centre,
        trunkY1: MARGIN,
        y: TOP_BAND - BAR_OFFSET,
        x1: placed.centre,
        x2: placed.centre,
      });
      sheet.drops.push({
        upstream: 'supply',
        downstream: request.roots[0] as string,
        x: placed.centre,
        y1: TOP_BAND - BAR_OFFSET,
        y2: TOP_BAND,
        conductor: null,
      });
    } else {
      // The trunk starts at the bottom of the incoming marker, which the
      // renderer draws in the band above the bar.
      const forest = placeForest(request.roots, 0, 0, request.entry.from, MARGIN - 4);
      const bar = sheet.bars[forest.barIndex] as Bar;
      bar.trunkX = (bar.x1 + bar.x2) / 2;
      sheet.markers.push({
        direction: 'from',
        designator: request.entry.from,
        sheet: request.entry.sheet,
        x: bar.trunkX,
        y: MARGIN,
      });
    }

    sheets.push(sheet);
  }

  return sheets.sort((a, b) => a.number - b.number);
}

/**
 * The short text printed beside a conductor.
 *
 * @param value - The conductor size, in whichever of its three states.
 * @returns The label, which is never empty: an uncalculated size is marked,
 *   not left blank for the reader to fill in.
 */
export function conductorLabel(value: CalcValue | null): string {
  if (value === null) return '';
  switch (value.status) {
    case 'calculated':
      return value.display;
    case 'not_calculated':
      return 'n/c';
    case 'refused':
      return 'refused';
  }
}
