'use client';

import type { components } from '@panelpilot/shared-types';

type LadderRung = components['schemas']['LadderRung'];
type LadderContact = components['schemas']['LadderContact'];
type LadderBlock = components['schemas']['LadderBlock'];
type LadderBranch = components['schemas']['LadderBranch'];
type LadderElement = LadderContact | LadderBlock | LadderBranch;

/**
 * Ladder rendered as SVG, from the structured representation the backend
 * returns.
 *
 * SVG rather than an image or ASCII art, because a panel engineer zooms. A
 * raster diagram of a rung is unreadable at the magnification someone actually
 * inspects a contact at, and ASCII art stops being a diagram the moment a
 * branch appears.
 *
 * The geometry is computed rather than hand-placed. A rung's width depends on
 * what is on it, and a branch's height depends on how many paths it holds, so
 * a layout pass measures each element before anything is drawn — the
 * alternative is fixed slots, which either clip a long tag name or leave a
 * simple rung stranded in whitespace.
 */

/** Horizontal space one contact occupies, including its rail segment. */
const CONTACT_WIDTH = 90;

/** Horizontal space one function block occupies. */
const BLOCK_WIDTH = 130;

/** Vertical space one branch path occupies. */
const PATH_HEIGHT = 56;

/** Height of a rung with no branches. */
const RUNG_HEIGHT = 56;

/** Space either side of the rung, where the power rails sit. */
const RAIL_MARGIN = 24;

/** Height of a function block's body. */
const BLOCK_HEIGHT = 40;

/** Half the gap in the rail where a contact's plates sit. */
const CONTACT_GAP = 9;

/** How far a contact plate extends above and below the rail. */
const PLATE_REACH = 11;

/** Radius of a coil's arcs. */
const COIL_REACH = 11;

/**
 * What one element needs, before anything is positioned.
 *
 * Width and height are measured first because a branch's height is the sum of
 * its paths', and a path's width is the sum of its elements'. Drawing and
 * measuring in one pass would need the answer before computing it.
 */
interface Measured {
  readonly width: number;
  readonly height: number;
}

/**
 * Measure one element.
 *
 * @param element - The element to measure.
 * @returns Its footprint.
 */
function measure(element: LadderElement): Measured {
  if (isBranch(element)) {
    const measured = element.paths.map(measureSeries);
    return {
      width: Math.max(...measured.map((m) => m.width), CONTACT_WIDTH),
      height: measured.reduce((total, m) => total + Math.max(m.height, PATH_HEIGHT), 0),
    };
  }
  if (isBlock(element)) {
    return { width: BLOCK_WIDTH, height: RUNG_HEIGHT };
  }
  return { width: CONTACT_WIDTH, height: RUNG_HEIGHT };
}

/**
 * Measure a series of elements.
 *
 * @param elements - The elements, left to right.
 * @returns Their combined footprint.
 */
function measureSeries(elements: readonly LadderElement[]): Measured {
  if (elements.length === 0) {
    return { width: CONTACT_WIDTH, height: RUNG_HEIGHT };
  }
  const measured = elements.map(measure);
  return {
    width: measured.reduce((total, m) => total + m.width, 0),
    height: Math.max(...measured.map((m) => m.height)),
  };
}

/** Narrow an element to a branch. */
function isBranch(element: LadderElement): element is LadderBranch {
  return 'paths' in element;
}

/** Narrow an element to a function block. */
function isBlock(element: LadderElement): element is LadderBlock {
  return 'kind' in element && 'parameters' in element;
}

/**
 * Draw one contact: two vertical plates with a gap in the rail.
 *
 * @param props - Where to draw it and what it is.
 * @returns The contact's SVG.
 *
 * A normally-closed contact is the same symbol with a diagonal through it.
 * That is the convention every ladder editor uses, and inventing a clearer one
 * would make this diagram unreadable to the people who read ladder all day.
 */
function Contact({
  contact,
  x,
  y,
  width,
}: {
  contact: LadderContact;
  x: number;
  y: number;
  width: number;
}) {
  const centre = x + width / 2;
  return (
    <g>
      <line
        x1={x}
        y1={y}
        x2={centre - CONTACT_GAP}
        y2={y}
        className="stroke-text"
        strokeWidth={2}
      />
      <line
        x1={centre - CONTACT_GAP}
        y1={y - PLATE_REACH}
        x2={centre - CONTACT_GAP}
        y2={y + PLATE_REACH}
        className="stroke-text"
        strokeWidth={2}
      />
      <line
        x1={centre + CONTACT_GAP}
        y1={y - PLATE_REACH}
        x2={centre + CONTACT_GAP}
        y2={y + PLATE_REACH}
        className="stroke-text"
        strokeWidth={2}
      />
      {contact.kind === 'nc' && (
        <line
          x1={centre - CONTACT_GAP - 2}
          y1={y + PLATE_REACH}
          x2={centre + CONTACT_GAP + 2}
          y2={y - PLATE_REACH}
          className="stroke-text"
          strokeWidth={2}
        />
      )}
      <line
        x1={centre + CONTACT_GAP}
        y1={y}
        x2={x + width}
        y2={y}
        className="stroke-text"
        strokeWidth={2}
      />
      <text
        x={centre}
        y={y - PLATE_REACH - 6}
        textAnchor="middle"
        className="fill-text font-mono text-xs"
      >
        {contact.tag}
      </text>
    </g>
  );
}

/**
 * Draw a coil: two arcs facing each other.
 *
 * @param props - Where to draw it and what it drives.
 * @returns The coil's SVG.
 */
function Coil({
  contact,
  x,
  y,
  width,
}: {
  contact: LadderContact;
  x: number;
  y: number;
  width: number;
}) {
  const centre = x + width / 2;
  return (
    <g>
      <line x1={x} y1={y} x2={centre - COIL_REACH} y2={y} className="stroke-text" strokeWidth={2} />
      <path
        d={`M ${String(centre - COIL_REACH)} ${String(y - COIL_REACH)} A ${String(COIL_REACH)} ${String(COIL_REACH)} 0 0 0 ${String(centre - COIL_REACH)} ${String(y + COIL_REACH)}`}
        className="fill-none stroke-text"
        strokeWidth={2}
      />
      <path
        d={`M ${String(centre + COIL_REACH)} ${String(y - COIL_REACH)} A ${String(COIL_REACH)} ${String(COIL_REACH)} 0 0 1 ${String(centre + COIL_REACH)} ${String(y + COIL_REACH)}`}
        className="fill-none stroke-text"
        strokeWidth={2}
      />
      <line
        x1={centre + COIL_REACH}
        y1={y}
        x2={x + width}
        y2={y}
        className="stroke-text"
        strokeWidth={2}
      />
      <text
        x={centre}
        y={y - COIL_REACH - 8}
        textAnchor="middle"
        className="fill-text font-mono text-xs"
      >
        {contact.tag}
      </text>
    </g>
  );
}

/**
 * Draw a function block: a labelled box with its parameters inside.
 *
 * @param props - Where to draw it and what it is.
 * @returns The block's SVG.
 *
 * Parameters are drawn as given rather than interpreted. A timer preset means
 * nothing to this component and everything to the engineer reading it, so it
 * is passed through verbatim — a rendered approximation of `T#5s` would be a
 * different number on the drawing than in the program.
 */
function Block({
  block,
  x,
  y,
  width,
}: {
  block: LadderBlock;
  x: number;
  y: number;
  width: number;
}) {
  const boxX = x + 10;
  const boxWidth = width - 20;
  const boxY = y - BLOCK_HEIGHT / 2;
  const entries = Object.entries(block.parameters ?? {});

  return (
    <g>
      <line x1={x} y1={y} x2={boxX} y2={y} className="stroke-text" strokeWidth={2} />
      <rect
        x={boxX}
        y={boxY}
        width={boxWidth}
        height={BLOCK_HEIGHT}
        rx={3}
        className="fill-surface-raised stroke-text"
        strokeWidth={2}
      />
      <text
        x={boxX + boxWidth / 2}
        y={boxY + 15}
        textAnchor="middle"
        className="fill-text font-mono text-xs font-semibold"
      >
        {block.kind}
      </text>
      <text
        x={boxX + boxWidth / 2}
        y={boxY + 30}
        textAnchor="middle"
        className="fill-text-muted font-mono text-xs"
      >
        {entries.length > 0 ? entries.map(([k, v]) => `${k}=${v}`).join(' ') : block.tag}
      </text>
      <line
        x1={boxX + boxWidth}
        y1={y}
        x2={x + width}
        y2={y}
        className="stroke-text"
        strokeWidth={2}
      />
      <text
        x={boxX + boxWidth / 2}
        y={boxY - 6}
        textAnchor="middle"
        className="fill-text font-mono text-xs"
      >
        {block.tag}
      </text>
    </g>
  );
}

/**
 * Draw a series of elements along one horizontal line.
 *
 * @param props - Where to start, how wide, and what to draw.
 * @returns The series' SVG.
 *
 * Given an explicit width so parallel paths in a branch end at the same
 * vertical, which is what makes the closing rail a straight line rather than a
 * staircase. A short path is padded with rail, not stretched.
 */
function Series({
  elements,
  x,
  y,
  width,
}: {
  elements: readonly LadderElement[];
  x: number;
  y: number;
  width: number;
}) {
  if (elements.length === 0) {
    return <line x1={x} y1={y} x2={x + width} y2={y} className="stroke-text" strokeWidth={2} />;
  }

  const measured = elements.map(measure);
  const natural = measured.reduce((total, m) => total + m.width, 0);
  const padding = Math.max(width - natural, 0);

  let cursor = x;
  const drawn = elements.map((element, index) => {
    const elementWidth = measured[index]?.width ?? CONTACT_WIDTH;
    const at = cursor;
    cursor += elementWidth;
    return <Element key={index} element={element} x={at} y={y} width={elementWidth} />;
  });

  return (
    <g>
      {drawn}
      {padding > 0 && (
        <line
          x1={cursor}
          y1={y}
          x2={cursor + padding}
          y2={y}
          className="stroke-text"
          strokeWidth={2}
        />
      )}
    </g>
  );
}

/**
 * Draw parallel paths, joined at both ends.
 *
 * @param props - Where to draw and what to draw.
 * @returns The branch's SVG.
 *
 * The vertical connectors at each end are the whole point: they are what says
 * "any of these conducts" rather than "all of these in a row". Drawing the
 * paths without them would be a picture of a different circuit.
 */
function Branch({
  branch,
  x,
  y,
  width,
}: {
  branch: LadderBranch;
  x: number;
  y: number;
  width: number;
}) {
  const heights = branch.paths.map((path) => Math.max(measureSeries(path).height, PATH_HEIGHT));
  const total = heights.reduce((sum, h) => sum + h, 0);

  let offset = y - total / 2 + (heights[0] ?? PATH_HEIGHT) / 2;
  const centres: number[] = [];
  const rows = branch.paths.map((path, index) => {
    const centre = offset;
    centres.push(centre);
    offset += ((heights[index] ?? PATH_HEIGHT) + (heights[index + 1] ?? 0)) / 2;
    return <Series key={index} elements={path} x={x} y={centre} width={width} />;
  });

  const first = centres[0] ?? y;
  const last = centres[centres.length - 1] ?? y;

  return (
    <g>
      <line x1={x} y1={first} x2={x} y2={last} className="stroke-text" strokeWidth={2} />
      <line
        x1={x + width}
        y1={first}
        x2={x + width}
        y2={last}
        className="stroke-text"
        strokeWidth={2}
      />
      {rows}
    </g>
  );
}

/**
 * Draw whichever element this is.
 *
 * @param props - Where to draw it and what it is.
 * @returns Its SVG.
 */
function Element({
  element,
  x,
  y,
  width,
}: {
  element: LadderElement;
  x: number;
  y: number;
  width: number;
}) {
  if (isBranch(element)) {
    return <Branch branch={element} x={x} y={y} width={width} />;
  }
  if (isBlock(element)) {
    return <Block block={element} x={x} y={y} width={width} />;
  }
  return <Contact contact={element} x={x} y={y} width={width} />;
}

/**
 * Draw one rung between its power rails.
 *
 * @param props - The rung and where it starts vertically.
 * @returns The rung's SVG group and the height it consumed.
 */
export function Rung({ rung, index }: { rung: LadderRung; index: number }) {
  const elements = rung.elements ?? [];
  const series = measureSeries(elements);
  const height = Math.max(series.height, RUNG_HEIGHT);
  const centre = height / 2 + 18;

  return (
    <g data-testid={`rung-${String(index)}`}>
      <text x={RAIL_MARGIN} y={12} className="fill-text-muted font-mono text-xs">
        {rung.comment}
      </text>
      <Series elements={elements} x={RAIL_MARGIN} y={centre} width={series.width} />
      <Coil contact={rung.output} x={RAIL_MARGIN + series.width} y={centre} width={CONTACT_WIDTH} />
    </g>
  );
}

/**
 * The full ladder diagram.
 *
 * @param props - The rungs to draw.
 * @returns The diagram.
 *
 * `role="img"` with a label rather than bare SVG: a screen reader given an
 * unlabelled diagram announces nothing useful, and a ladder is exactly the
 * content where "image" alone tells a reader they have missed something
 * without telling them what.
 */
export function LadderDiagram({ rungs, label }: { rungs: readonly LadderRung[]; label: string }) {
  const laid = rungs.map((rung) => {
    const series = measureSeries(rung.elements ?? []);
    return {
      rung,
      width: series.width + CONTACT_WIDTH,
      height: Math.max(series.height, RUNG_HEIGHT) + 28,
    };
  });

  const width = Math.max(...laid.map((l) => l.width), CONTACT_WIDTH) + RAIL_MARGIN * 2;
  const height = laid.reduce((total, l) => total + l.height, 0) + 16;

  let offset = 8;
  const positioned = laid.map((l, index) => {
    const at = offset;
    offset += l.height;
    return (
      <g key={index} transform={`translate(0, ${String(at)})`}>
        <Rung rung={l.rung} index={index} />
      </g>
    );
  });

  return (
    <svg
      role="img"
      aria-label={label}
      viewBox={`0 0 ${String(width)} ${String(height)}`}
      className="h-auto w-full max-w-full"
      data-testid="ladder-diagram"
    >
      {/* The power rails. Ladder is read as current flowing left to right
          between them, and a diagram without them is a floating collection of
          symbols. */}
      <line
        x1={RAIL_MARGIN / 2}
        y1={0}
        x2={RAIL_MARGIN / 2}
        y2={height}
        className="stroke-text"
        strokeWidth={2}
      />
      <line
        x1={width - RAIL_MARGIN / 2}
        y1={0}
        x2={width - RAIL_MARGIN / 2}
        y2={height}
        className="stroke-text"
        strokeWidth={2}
      />
      {positioned}
    </svg>
  );
}
