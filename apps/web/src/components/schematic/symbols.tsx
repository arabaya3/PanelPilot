'use client';

/**
 * IEC 60617-style schematic symbols, as parameterised SVG components.
 *
 * The visual language every generated single-line diagram is drawn in. Same
 * structured-data-to-SVG pattern FE-009 proved for ladder logic, applied to
 * panel schematics: each symbol takes a reference designator and a rating
 * label as props rather than being drawn per instance, so a diagram cannot
 * drift symbol-by-symbol into its own dialect.
 *
 * **The unknown-type path is the safety-critical part of this file.** A
 * schematic is read by someone who will wire a panel from it. A missing
 * component is worse than a wrong one, because a wrong symbol invites a
 * question and a gap invites nothing — the reader completes the picture from
 * expectation. So an unrecognised type renders a visibly marked placeholder
 * that occupies the same footprint and carries the type name it could not
 * draw. It is never omitted, and never substituted with a plausible-looking
 * symbol from a neighbouring category.
 *
 * That is the same rule as cite-or-refuse, in a different medium: the diagram
 * says what it does not know rather than quietly looking complete.
 */

/**
 * Component types this library can draw.
 *
 * Deliberately a closed union rather than a string. A free-text type would
 * make "unrecognised" unrepresentable at the type level, and the placeholder
 * path would then only ever be exercised by a typo — whereas its real job is
 * to catch a component category PD-001/PD-002 can produce and this library
 * has not yet been taught.
 */
export type SymbolKind =
  | 'circuit-breaker'
  | 'contactor'
  | 'overload-relay'
  | 'relay-coil'
  | 'terminal-block'
  | 'fuse'
  | 'isolator'
  | 'transformer'
  | 'motor'
  | 'vfd'
  | 'busbar'
  | 'indicator-lamp'
  | 'push-button';

/** Every kind this library draws, for exhaustive iteration in tests. */
export const SYMBOL_KINDS: readonly SymbolKind[] = [
  'circuit-breaker',
  'contactor',
  'overload-relay',
  'relay-coil',
  'terminal-block',
  'fuse',
  'isolator',
  'transformer',
  'motor',
  'vfd',
  'busbar',
  'indicator-lamp',
  'push-button',
];

/** Footprint every symbol occupies, so a row of them aligns without layout. */
export const SYMBOL_WIDTH = 64;

/** Height of the symbol body, excluding its labels. */
export const SYMBOL_HEIGHT = 48;

/** Length of the connection stub entering and leaving each symbol. */
const STUB = 12;

/** Half the body width, used by nearly every glyph. */
const HALF = SYMBOL_WIDTH / 2;

export interface SymbolProps {
  /** Which symbol to draw. */
  readonly kind: SymbolKind | (string & {});
  /** Reference designator, e.g. `Q1`, `KM3`, `-X1`. */
  readonly designator: string;
  /** Rating or value label, e.g. `63 A`, `400 V`, `4 mm²`. */
  readonly rating?: string;
  /** Where to place the symbol's top-left corner. */
  readonly x?: number;
  readonly y?: number;
}

/**
 * Report whether this library has a definition for a type.
 *
 * @param kind - The component type to check.
 * @returns Whether a real symbol exists for it.
 *
 * Exported so a caller can count its own coverage before rendering — PD-008
 * needs to know how many placeholders a diagram will contain, and finding out
 * by reading the rendered SVG is too late to warn anybody.
 */
export function isKnownSymbol(kind: string): kind is SymbolKind {
  return (SYMBOL_KINDS as readonly string[]).includes(kind);
}

/**
 * The vertical connection stub above and below a symbol body.
 *
 * @param props - Where the body sits.
 * @returns The stubs.
 */
function Stubs({ x, y }: { x: number; y: number }) {
  return (
    <>
      <line
        x1={x + HALF}
        y1={y}
        x2={x + HALF}
        y2={y + STUB}
        className="stroke-text"
        strokeWidth={2}
      />
      <line
        x1={x + HALF}
        y1={y + SYMBOL_HEIGHT - STUB}
        x2={x + HALF}
        y2={y + SYMBOL_HEIGHT}
        className="stroke-text"
        strokeWidth={2}
      />
    </>
  );
}

/**
 * The designator and rating labels beside a symbol.
 *
 * @param props - Where the body sits and what to label it.
 * @returns The labels.
 *
 * Placed to the trailing side rather than centred, because a single-line
 * diagram stacks symbols vertically and centred text would collide with the
 * connection stubs.
 */
function Labels({
  x,
  y,
  designator,
  rating,
}: {
  x: number;
  y: number;
  designator: string;
  rating: string | undefined;
}) {
  return (
    <>
      <text
        x={x + SYMBOL_WIDTH - 4}
        y={y + SYMBOL_HEIGHT / 2 - 2}
        className="fill-text font-mono text-xs"
      >
        {designator}
      </text>
      {rating !== undefined && rating !== '' && (
        <text
          x={x + SYMBOL_WIDTH - 4}
          y={y + SYMBOL_HEIGHT / 2 + 10}
          className="fill-text-muted font-mono text-xs"
        >
          {rating}
        </text>
      )}
    </>
  );
}

/**
 * The glyph for a known symbol kind.
 *
 * @param props - Which glyph, and where.
 * @returns The glyph's SVG, or `null` for an unknown kind.
 *
 * Split from `SchematicSymbol` so the placeholder decision is made in exactly
 * one place: this returns `null` for anything it cannot draw, and the caller
 * turns that into a visible marker. A glyph that "handled" an unknown kind by
 * drawing something generic would put the decision here, where it is invisible.
 */
function Glyph({ kind, x, y }: { kind: string; x: number; y: number }) {
  const cx = x + HALF;
  const top = y + STUB;
  const bottom = y + SYMBOL_HEIGHT - STUB;
  const mid = y + SYMBOL_HEIGHT / 2;
  const stroke = 'stroke-text';

  switch (kind) {
    case 'circuit-breaker':
      // A break in the line with the diagonal contact and the IEC cross.
      return (
        <g>
          <line x1={cx} y1={top} x2={cx + 10} y2={bottom} className={stroke} strokeWidth={2} />
          <line
            x1={cx - 6}
            y1={top + 2}
            x2={cx + 6}
            y2={top - 4}
            className={stroke}
            strokeWidth={2}
          />
        </g>
      );

    case 'isolator':
      // The same break, without the trip cross: it disconnects, it does not
      // interrupt a fault. Drawing them alike would be the quiet kind of wrong.
      return <line x1={cx} y1={top} x2={cx + 10} y2={bottom} className={stroke} strokeWidth={2} />;

    case 'contactor':
      // Diagonal contact with the arc-suppression bowl.
      return (
        <g>
          <line x1={cx} y1={top} x2={cx + 10} y2={bottom} className={stroke} strokeWidth={2} />
          <path
            d={`M ${String(cx + 4)} ${String(mid - 6)} A 7 7 0 0 0 ${String(cx + 14)} ${String(mid - 6)}`}
            className={`fill-none ${stroke}`}
            strokeWidth={2}
          />
        </g>
      );

    case 'overload-relay':
      // Rectangle with the thermal element.
      return (
        <g>
          <rect
            x={cx - 12}
            y={top}
            width={24}
            height={bottom - top}
            className={`fill-surface ${stroke}`}
            strokeWidth={2}
          />
          <path
            d={`M ${String(cx - 6)} ${String(mid + 4)} L ${String(cx)} ${String(mid - 4)} L ${String(cx + 6)} ${String(mid + 4)}`}
            className={`fill-none ${stroke}`}
            strokeWidth={2}
          />
        </g>
      );

    case 'relay-coil':
      // Plain rectangle across the line: IEC draws a coil as a box.
      return (
        <rect
          x={cx - 14}
          y={mid - 9}
          width={28}
          height={18}
          className={`fill-surface ${stroke}`}
          strokeWidth={2}
        />
      );

    case 'fuse':
      // Rectangle with the element through it.
      return (
        <g>
          <rect
            x={cx - 8}
            y={mid - 11}
            width={16}
            height={22}
            className={`fill-surface ${stroke}`}
            strokeWidth={2}
          />
          <line x1={cx} y1={mid - 11} x2={cx} y2={mid + 11} className={stroke} strokeWidth={2} />
        </g>
      );

    case 'terminal-block':
      // The open circle every terminal is drawn as.
      return <circle cx={cx} cy={mid} r={6} className={`fill-surface ${stroke}`} strokeWidth={2} />;

    case 'transformer':
      // Two coupled windings.
      return (
        <g>
          <circle cx={cx} cy={mid - 6} r={8} className={`fill-none ${stroke}`} strokeWidth={2} />
          <circle cx={cx} cy={mid + 6} r={8} className={`fill-none ${stroke}`} strokeWidth={2} />
        </g>
      );

    case 'motor':
      // Circle with M, the one symbol nobody misreads.
      return (
        <g>
          <circle cx={cx} cy={mid} r={13} className={`fill-surface ${stroke}`} strokeWidth={2} />
          <text
            x={cx}
            y={mid + 4}
            textAnchor="middle"
            className="fill-text font-mono text-xs font-semibold"
          >
            M
          </text>
        </g>
      );

    case 'vfd':
      // Box with the diagonal, marking a converter rather than a plain load.
      return (
        <g>
          <rect
            x={cx - 14}
            y={mid - 12}
            width={28}
            height={24}
            className={`fill-surface ${stroke}`}
            strokeWidth={2}
          />
          <line
            x1={cx - 14}
            y1={mid + 12}
            x2={cx + 14}
            y2={mid - 12}
            className={stroke}
            strokeWidth={2}
          />
        </g>
      );

    case 'busbar':
      // A heavy horizontal bar; the one symbol drawn across rather than along.
      return (
        <line
          x1={x + 4}
          y1={mid}
          x2={x + SYMBOL_WIDTH - 4}
          y2={mid}
          className={stroke}
          strokeWidth={5}
        />
      );

    case 'indicator-lamp':
      // Circle with the cross.
      return (
        <g>
          <circle cx={cx} cy={mid} r={10} className={`fill-none ${stroke}`} strokeWidth={2} />
          <line
            x1={cx - 7}
            y1={mid - 7}
            x2={cx + 7}
            y2={mid + 7}
            className={stroke}
            strokeWidth={2}
          />
          <line
            x1={cx - 7}
            y1={mid + 7}
            x2={cx + 7}
            y2={mid - 7}
            className={stroke}
            strokeWidth={2}
          />
        </g>
      );

    case 'push-button':
      // Contact with the actuator stem above it.
      return (
        <g>
          <line
            x1={cx - 10}
            y1={mid - 4}
            x2={cx + 10}
            y2={mid - 4}
            className={stroke}
            strokeWidth={2}
          />
          <line x1={cx} y1={mid - 4} x2={cx} y2={mid - 14} className={stroke} strokeWidth={2} />
          <line
            x1={cx - 7}
            y1={mid - 14}
            x2={cx + 7}
            y2={mid - 14}
            className={stroke}
            strokeWidth={2}
          />
        </g>
      );

    default:
      // Not drawable. The caller renders the placeholder; see the module
      // docstring for why that decision does not live here.
      return null;
  }
}

/**
 * The placeholder drawn for a component type this library cannot render.
 *
 * @param props - Where it sits, and the type name that was not recognised.
 * @returns The placeholder.
 *
 * Deliberately loud. It is a dashed box in the warning colour, carrying a `?`
 * and the unrecognised type name, and it occupies the same footprint as a real
 * symbol so the diagram's layout does not silently reflow around an absence.
 *
 * The type name is printed rather than elided because the person reading the
 * schematic is not the person who can fix the library — printing it turns "the
 * diagram is wrong" into "the diagram is missing `soft-starter`", which is
 * actionable.
 */
function Placeholder({ kind, x, y }: { kind: string; x: number; y: number }) {
  const cx = x + HALF;
  const mid = y + SYMBOL_HEIGHT / 2;

  return (
    <g data-testid="symbol-placeholder" data-unknown-kind={kind}>
      <rect
        x={cx - 16}
        y={mid - 14}
        width={32}
        height={28}
        rx={2}
        strokeDasharray="4 3"
        className="fill-severity-warning-surface stroke-severity-warning"
        strokeWidth={2}
      />
      <text
        x={cx}
        y={mid + 5}
        textAnchor="middle"
        className="fill-severity-warning font-mono text-sm font-semibold"
      >
        ?
      </text>
      <text
        x={cx}
        y={mid + 26}
        textAnchor="middle"
        className="fill-severity-warning font-mono text-xs"
      >
        {kind}
      </text>
    </g>
  );
}

/**
 * One schematic symbol, with its designator and rating.
 *
 * @param props - Which symbol, what to label it, and where it sits.
 * @returns The symbol's SVG group.
 */
export function SchematicSymbol({ kind, designator, rating, x = 0, y = 0 }: SymbolProps) {
  const known = isKnownSymbol(kind);

  return (
    <g
      data-testid={`symbol-${designator}`}
      data-kind={kind}
      data-known={known ? 'true' : 'false'}
      role="img"
      aria-label={
        known
          ? `${kind} ${designator}${rating !== undefined && rating !== '' ? `, ${rating}` : ''}`
          : `Unrecognised component type ${kind}, ${designator}`
      }
    >
      <Stubs x={x} y={y} />
      {known ? <Glyph kind={kind} x={x} y={y} /> : <Placeholder kind={kind} x={x} y={y} />}
      <Labels x={x} y={y} designator={designator} rating={rating} />
    </g>
  );
}
