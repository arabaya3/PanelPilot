'use client';

/**
 * The single-line schematic renderer (PD-008).
 *
 * Composes PD-006's symbols along PD-007's specification, laid out by
 * `layoutSheets`: supply at the top, each device under its feeder, branches
 * fanning out left to right, continued across numbered sheets rather than
 * shrunk.
 *
 * **What was not calculated is printed, not left blank.** Every conductor
 * carries a label, `n/c` when no tool sized it, and every sheet has a notes
 * box stating why each uncalculated quantity is missing and which task
 * blocks it. A blank on a drawing is completed by whoever reads it, from
 * habit; a mark tells them the tool did not decide it and they must.
 *
 * Each sheet renders at its real size inside a horizontally scrolling frame,
 * so a narrow screen scrolls instead of shrinking the drawing, and each sheet
 * prints on its own page.
 */
import { useTranslations } from 'next-intl';
import { forwardRef } from 'react';

import {
  FOOTER_HEIGHT,
  MARGIN,
  SHEET_HEIGHT,
  SHEET_WIDTH,
  conductorLabel,
  layoutSheets,
  type CalcValue,
  type SchematicSpec,
  type Sheet,
} from './layout';
import { SchematicSymbol } from './symbols';

export interface SingleLineDiagramProps {
  spec: SchematicSpec;
  /** Receives each sheet's `<svg>`, for export. */
  sheetRef?: (sheetNumber: number, element: SVGSVGElement | null) => void;
}

/**
 * Every sheet of the diagram, in order.
 *
 * @param props - The specification, and an optional ref collector.
 * @returns The sheets, each in its own scrollable frame.
 */
export function SingleLineDiagram({ spec, sheetRef }: SingleLineDiagramProps) {
  const t = useTranslations('schematic');
  const sheets = layoutSheets(spec);

  return (
    <div className="flex flex-col gap-4" data-testid="single-line-diagram">
      {spec.unknown_kinds.length > 0 && (
        <p
          role="alert"
          className="rounded-md border border-severity-warning bg-severity-warning-surface p-3 text-sm text-text"
        >
          {t('unknownKinds', {
            count: spec.unknown_kinds.length,
            kinds: spec.unknown_kinds.join(', '),
          })}
        </p>
      )}
      {sheets.map((sheet) => (
        <div
          key={sheet.number}
          className="overflow-x-auto rounded-md border border-border bg-surface print:break-after-page print:overflow-visible print:border-0"
        >
          <SheetSvg
            ref={(element) => sheetRef?.(sheet.number, element)}
            spec={spec}
            sheet={sheet}
            total={sheets.length}
          />
        </div>
      ))}
      <NotesList spec={spec} />
    </div>
  );
}

interface SheetSvgProps {
  spec: SchematicSpec;
  sheet: Sheet;
  total: number;
}

/** One sheet, at its real size. */
const SheetSvg = forwardRef<SVGSVGElement, SheetSvgProps>(function SheetSvg(
  { spec, sheet, total },
  ref,
) {
  const t = useTranslations('schematic');
  const titleId = `schematic-sheet-${String(sheet.number)}-title`;

  return (
    <svg
      ref={ref}
      xmlns="http://www.w3.org/2000/svg"
      viewBox={`0 0 ${String(SHEET_WIDTH)} ${String(SHEET_HEIGHT)}`}
      width={SHEET_WIDTH}
      height={SHEET_HEIGHT}
      // Drawings read left to right in every locale: designators, ratings and
      // the flow from supply to load are not text to be mirrored.
      direction="ltr"
      role="img"
      aria-labelledby={titleId}
      data-testid={`schematic-sheet-${String(sheet.number)}`}
      className="max-w-none bg-surface"
    >
      <title id={titleId}>
        {`${spec.title} — ${t('sheetOf', { number: sheet.number, total })}`}
      </title>
      <rect
        x={MARGIN / 2}
        y={MARGIN / 2}
        width={SHEET_WIDTH - MARGIN}
        height={SHEET_HEIGHT - MARGIN}
        className="fill-surface stroke-border"
        strokeWidth={1}
      />

      {sheet.number === 1 && (
        <text
          x={sheet.bars.find((bar) => bar.from === 'supply')?.trunkX ?? SHEET_WIDTH / 2}
          y={MARGIN - 4}
          textAnchor="middle"
          className="fill-text font-mono text-xs font-semibold"
          data-testid="schematic-supply"
        >
          {spec.supply}
        </text>
      )}

      {sheet.bars.map((bar, index) => (
        <g key={`bar-${String(index)}`}>
          <line
            x1={bar.trunkX}
            y1={bar.trunkY1}
            x2={bar.trunkX}
            y2={bar.y}
            className="stroke-text"
            strokeWidth={2}
          />
          {bar.x2 > bar.x1 && (
            <line
              x1={bar.x1}
              y1={bar.y}
              x2={bar.x2}
              y2={bar.y}
              className="stroke-text"
              strokeWidth={2}
            />
          )}
        </g>
      ))}

      {sheet.drops.map((drop) => (
        <g key={`drop-${drop.downstream}`} data-testid={`conductor-${drop.downstream}`}>
          <line
            x1={drop.x}
            y1={drop.y1}
            x2={drop.x}
            y2={drop.y2}
            className="stroke-text"
            strokeWidth={2}
          />
          {drop.conductor !== null && (
            <text
              x={drop.x - 6}
              y={drop.y1 + (drop.y2 - drop.y1) / 2 + 4}
              textAnchor="end"
              className={
                drop.conductor.status === 'calculated'
                  ? 'fill-text-muted font-mono text-xs'
                  : 'fill-severity-warning font-mono text-xs font-semibold'
              }
              data-status={drop.conductor.status}
            >
              {conductorLabel(drop.conductor)}
            </text>
          )}
        </g>
      ))}

      {sheet.nodes.map((node) => (
        <SchematicSymbol
          key={node.component.designator}
          kind={node.component.kind}
          designator={node.component.designator}
          {...(node.component.rating ? { rating: node.component.rating } : {})}
          x={node.x}
          y={node.y}
        />
      ))}

      {sheet.markers.map((marker) => (
        <OffSheet
          key={`${marker.direction}-${marker.designator}-${String(marker.sheet)}`}
          label={
            marker.direction === 'to'
              ? t('continuedOn', { sheet: marker.sheet })
              : t('continuedFrom', { designator: marker.designator, sheet: marker.sheet })
          }
          direction={marker.direction}
          sheet={marker.sheet}
          x={marker.x}
          y={marker.y}
        />
      ))}

      <Footer spec={spec} sheet={sheet} total={total} />
    </svg>
  );
});

/** An off-sheet connector: an arrow tag naming the other sheet. */
function OffSheet({
  label,
  direction,
  sheet,
  x,
  y,
}: {
  label: string;
  direction: 'to' | 'from';
  sheet: number;
  x: number;
  y: number;
}) {
  const width = Math.max(64, label.length * 7 + 16);
  const top = direction === 'from' ? y - 22 : y;
  // Kept inside the sheet frame: a marker at the end of a full row of
  // circuits would otherwise run off the right edge and be cut in print.
  const left = Math.min(
    Math.max(x - width / 2, MARGIN / 2 + 4),
    SHEET_WIDTH - MARGIN / 2 - width - 4,
  );
  return (
    <g data-testid={`offsheet-${direction}-${String(sheet)}`}>
      <rect
        x={left}
        y={top}
        width={width}
        height={18}
        rx={9}
        className="fill-surface-raised stroke-accent"
        strokeWidth={1.5}
      />
      <text
        x={left + width / 2}
        y={top + 13}
        textAnchor="middle"
        className="fill-accent font-mono text-xs"
      >
        {label}
      </text>
    </g>
  );
}

/** The notes box and title block along the bottom of every sheet. */
function Footer({ spec, sheet, total }: { spec: SchematicSpec; sheet: Sheet; total: number }) {
  const t = useTranslations('schematic');
  const top = SHEET_HEIGHT - MARGIN / 2 - FOOTER_HEIGHT + 10;
  const blockWidth = 330;
  const notesWidth = SHEET_WIDTH - MARGIN - blockWidth;

  const notes = notesFor(spec, t);
  const shown = notes.length > FOOTER_NOTE_LINES ? notes.slice(0, FOOTER_NOTE_LINES - 1) : notes;
  const hidden = notes.length - shown.length;

  return (
    <g data-testid="schematic-footer">
      <line
        x1={MARGIN / 2}
        y1={top}
        x2={SHEET_WIDTH - MARGIN / 2}
        y2={top}
        className="stroke-border"
        strokeWidth={1}
      />
      <text x={MARGIN} y={top + 20} className="fill-text text-xs font-semibold">
        {t('notes')}
      </text>
      {shown.map(([label, value], index) => (
        <text
          key={label}
          x={MARGIN}
          y={top + 38 + index * 15}
          className={
            value.status === 'calculated' ? 'fill-text text-xs' : 'fill-severity-warning text-xs'
          }
          data-status={value.status}
        >
          <title>{describe(value, t)}</title>
          {`${label}: ${truncate(describe(value, t), Math.floor(notesWidth / 6.4) - label.length)}`}
        </text>
      ))}
      {hidden > 0 && (
        <text
          x={MARGIN}
          y={top + 38 + shown.length * 15}
          className="fill-severity-warning text-xs font-semibold"
        >
          {t('moreNotes', { count: hidden })}
        </text>
      )}

      <rect
        x={SHEET_WIDTH - MARGIN / 2 - blockWidth}
        y={top}
        width={blockWidth}
        height={FOOTER_HEIGHT - 10}
        className="fill-surface-raised stroke-border"
        strokeWidth={1}
      />
      <text
        x={SHEET_WIDTH - MARGIN / 2 - blockWidth + 14}
        y={top + 30}
        className="fill-text text-sm font-semibold"
      >
        {truncate(spec.title, 40)}
      </text>
      <text
        x={SHEET_WIDTH - MARGIN / 2 - blockWidth + 14}
        y={top + 52}
        className="fill-text-muted font-mono text-xs"
      >
        {spec.supply}
      </text>
      <text
        x={SHEET_WIDTH - MARGIN / 2 - blockWidth + 14}
        y={top + FOOTER_HEIGHT - 30}
        className="fill-text text-xs font-semibold"
        data-testid="schematic-sheet-number"
      >
        {t('sheetOf', { number: sheet.number, total })}
      </text>
    </g>
  );
}

/** Note lines that fit the footer beside the title block. */
const FOOTER_NOTE_LINES = 7;

/**
 * Every quantity the notes report, in a fixed order.
 *
 * @param spec - The specification.
 * @param t - Translator for the schematic namespace.
 * @returns Label and value pairs. Conductors appear once: they share a state.
 */
function notesFor(
  spec: SchematicSpec,
  t: ReturnType<typeof useTranslations>,
): Array<[string, CalcValue]> {
  const conductor = spec.connections[0]?.conductor;
  return [
    [t('enclosure'), spec.enclosure],
    [t('trunking'), spec.trunking],
    ...(conductor !== undefined
      ? ([[t('conductors'), conductor]] as Array<[string, CalcValue]>)
      : []),
    ...spec.groups.map((group): [string, CalcValue] => [
      t('railRows', { group: group.name }),
      group.rail_rows,
    ]),
  ];
}

/**
 * Every note in full, below the sheets.
 *
 * The footer shortens lines to fit the sheet; this list does not, so nothing
 * a note says is only ever available as a tooltip.
 */
function NotesList({ spec }: { spec: SchematicSpec }) {
  const t = useTranslations('schematic');
  return (
    <section aria-labelledby="schematic-notes-heading" className="text-sm print:hidden">
      <h2 id="schematic-notes-heading" className="mb-2 font-semibold text-text">
        {t('notes')}
      </h2>
      <ul className="flex flex-col gap-1" data-testid="schematic-notes">
        {notesFor(spec, t).map(([label, value]) => (
          <li
            key={label}
            data-status={value.status}
            className={value.status === 'calculated' ? 'text-text' : 'text-severity-warning'}
          >
            <span className="font-semibold">{label}:</span> {describe(value, t)}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * A calculated quantity in words.
 *
 * @param value - The quantity.
 * @param t - Translator for the schematic namespace.
 * @returns The value with its source, or why there is none.
 */
function describe(value: CalcValue, t: ReturnType<typeof useTranslations>): string {
  switch (value.status) {
    case 'calculated':
      return `${value.display} (${value.source})`;
    case 'not_calculated':
      // The blocking task first: if the line is cut to fit the sheet, what
      // must survive is which tool did not decide it.
      return `${t('notCalculated')} [${value.blocked_by}] — ${value.reason}`;
    case 'refused':
      return `${t('refused')} — ${value.reason}`;
  }
}

/** Shorten text to fit a fixed-width box, marking the cut. */
function truncate(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
}
