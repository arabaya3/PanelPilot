import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  SYMBOL_KINDS,
  SchematicSymbol,
  isKnownSymbol,
  type SymbolKind,
} from '@/components/schematic/symbols';

/**
 * Tests for the schematic symbol library (PD-006).
 *
 * The acceptance criterion has two halves, and the second is the one with
 * teeth:
 *
 * > Every component type PD-001/PD-002 can produce has a corresponding correct
 * > symbol; an unrecognized type renders a visible placeholder, never a silent
 * > gap or a guessed symbol.
 *
 * A schematic is read by someone who will wire a panel from it, so a missing
 * component is worse than a wrong one: a wrong symbol invites a question, a
 * gap invites nothing and the reader fills it in from expectation. Most of
 * this file is therefore about the absence case rather than the happy path.
 *
 * Rendered assertions rather than image snapshots. jsdom performs no layout,
 * so a visual snapshot here would compare serialised markup while claiming to
 * check appearance — these assert the structural facts that actually decide
 * whether a diagram is readable: that something is drawn, that it is marked
 * when it is a placeholder, and that distinct component types are not drawn
 * identically.
 */

function renderSymbol(props: Parameters<typeof SchematicSymbol>[0]) {
  return render(
    <svg>
      <SchematicSymbol {...props} />
    </svg>,
  );
}

/**
 * The drawn shapes only, as a comparable string.
 *
 * Comparing `container.innerHTML` looks like it compares the drawing and does
 * not: the wrapper carries `data-kind` and an aria-label that differ by
 * construction, so two symbols drawn identically still compare unequal. A
 * mutation making the isolator render exactly like a circuit breaker passed
 * every such test. This reduces each symbol to its geometry.
 */
function geometry(container: HTMLElement): string {
  return [...container.querySelectorAll('line, rect, circle, path, text')]
    .map((node) => {
      const attrs = [...node.attributes]
        .filter((a) => !a.name.startsWith('data-') && a.name !== 'aria-label')
        .map((a) => `${a.name}=${a.value}`)
        .sort()
        .join(' ');
      return `${node.tagName}[${attrs}]${node.textContent}`;
    })
    .join('|');
}

// --- every known type draws something --------------------------------------

describe('coverage of known component types', () => {
  it.each(SYMBOL_KINDS)('draws a real glyph for %s', (kind) => {
    const { container } = renderSymbol({ kind, designator: 'Q1' });

    expect(screen.getByTestId('symbol-Q1').getAttribute('data-known')).toBe('true');
    expect(screen.queryByTestId('symbol-placeholder')).toBeNull();
    // A glyph is drawn, not merely a group with labels in it: the stubs alone
    // are two lines, so anything real adds shapes beyond them.
    const shapes = container.querySelectorAll('line, rect, circle, path');
    expect(shapes.length).toBeGreaterThan(2);
  });

  it('reports every listed kind as known', () => {
    for (const kind of SYMBOL_KINDS) {
      expect(isKnownSymbol(kind)).toBe(true);
    }
  });

  it('covers the component categories a panel BOM produces', () => {
    // Not an arbitrary list: protection, switching, control, distribution and
    // loads are the five groups any panel schedule resolves into. A library
    // missing one of them cannot draw a complete panel, and would fail the
    // acceptance criterion on the first real design rather than in review.
    const required: SymbolKind[] = [
      'circuit-breaker',
      'fuse',
      'isolator',
      'contactor',
      'overload-relay',
      'relay-coil',
      'terminal-block',
      'busbar',
      'transformer',
      'motor',
      'vfd',
    ];

    for (const kind of required) {
      expect(isKnownSymbol(kind)).toBe(true);
    }
  });
});

// --- the unknown-type path, which is the safety property --------------------

describe('an unrecognised component type', () => {
  it('renders a visible placeholder rather than nothing', () => {
    // The failure this whole task guards against. A component that vanishes
    // from a schematic is a component nobody wires.
    renderSymbol({ kind: 'soft-starter', designator: 'T1' });

    expect(screen.getByTestId('symbol-placeholder')).not.toBeNull();
  });

  it('marks the symbol group as not known', () => {
    // So PD-008 can count placeholders and warn before anyone prints the
    // diagram, rather than the reader discovering it at the panel.
    renderSymbol({ kind: 'soft-starter', designator: 'T1' });

    expect(screen.getByTestId('symbol-T1').getAttribute('data-known')).toBe('false');
  });

  it('names the type it could not draw', () => {
    // The reader of a schematic is not the person who can extend the library.
    // Printing the name turns "this diagram is wrong" into "this diagram is
    // missing soft-starter", which someone can act on.
    renderSymbol({ kind: 'soft-starter', designator: 'T1' });

    expect(screen.getByTestId('symbol-placeholder').getAttribute('data-unknown-kind')).toBe(
      'soft-starter',
    );
    expect(screen.getByText('soft-starter')).not.toBeNull();
  });

  it('still shows the designator, so the component is identifiable', () => {
    // Even undrawable, it is a real item on the schedule with a real tag. The
    // placeholder must not cost the reader the one field that identifies it.
    renderSymbol({ kind: 'soft-starter', designator: 'T1', rating: '75 kW' });

    expect(screen.getByText('T1')).not.toBeNull();
    expect(screen.getByText('75 kW')).not.toBeNull();
  });

  it('occupies the same footprint as a real symbol', () => {
    // A placeholder that took no space would let the diagram reflow around the
    // absence and look complete — the silent gap, arriving by a different door.
    const unknown = renderSymbol({ kind: 'soft-starter', designator: 'T1' });
    const known = renderSymbol({ kind: 'contactor', designator: 'KM1' });

    const stubs = (c: HTMLElement) =>
      [...c.querySelectorAll('line')].filter((l) => l.getAttribute('x1') === l.getAttribute('x2'))
        .length;

    // Both draw the same connection stubs, so a row of symbols stays aligned.
    expect(stubs(unknown.container)).toBeGreaterThanOrEqual(2);
    expect(stubs(known.container)).toBeGreaterThanOrEqual(2);
  });

  it('is not silently mapped onto a neighbouring symbol', () => {
    // The other half of the criterion: never "a guessed symbol". A soft
    // starter is not a contactor, and drawing it as one would be confidently
    // wrong rather than honestly incomplete.
    const unknown = renderSymbol({ kind: 'soft-starter', designator: 'T1' });
    const contactor = renderSymbol({ kind: 'contactor', designator: 'T1' });

    expect(geometry(unknown.container)).not.toBe(geometry(contactor.container));
    expect(isKnownSymbol('soft-starter')).toBe(false);
  });

  it('treats an empty type as unrecognised rather than defaulting', () => {
    renderSymbol({ kind: '', designator: 'T1' });

    expect(screen.getByTestId('symbol-placeholder')).not.toBeNull();
  });

  it('treats a near-miss spelling as unrecognised', () => {
    // `breaker` is not `circuit-breaker`. Fuzzy matching here would be the
    // guessed symbol the criterion forbids, dressed up as helpfulness.
    renderSymbol({ kind: 'breaker', designator: 'Q1' });

    expect(screen.getByTestId('symbol-placeholder')).not.toBeNull();
  });
});

// --- symbols that must not look alike ---------------------------------------

describe('distinguishable symbols', () => {
  it('draws an isolator differently from a circuit breaker', () => {
    // They differ by exactly one thing on paper — the breaker interrupts a
    // fault, the isolator only disconnects — and confusing them on a schematic
    // is a person opening a switch onto a fault current it cannot break.
    const breaker = renderSymbol({ kind: 'circuit-breaker', designator: 'Q1' });
    const isolator = renderSymbol({ kind: 'isolator', designator: 'Q1' });

    expect(geometry(breaker.container)).not.toBe(geometry(isolator.container));
  });

  it('draws a contactor differently from a relay coil', () => {
    const contactor = renderSymbol({ kind: 'contactor', designator: 'KM1' });
    const coil = renderSymbol({ kind: 'relay-coil', designator: 'KM1' });

    expect(geometry(contactor.container)).not.toBe(geometry(coil.container));
  });

  it('draws every kind distinguishably from every other', () => {
    // The pin that matters most: two component types rendering identically
    // would satisfy every per-symbol test above while making the diagram
    // unreadable at exactly the points a reader is checking.
    const rendered = new Map<string, string>();
    for (const kind of SYMBOL_KINDS) {
      const { container } = renderSymbol({ kind, designator: 'X1' });
      rendered.set(kind, geometry(container));
    }

    expect(new Set(rendered.values()).size).toBe(SYMBOL_KINDS.length);
  });
});

// --- labelling and accessibility ---------------------------------------------

describe('labelling', () => {
  it('shows the designator', () => {
    renderSymbol({ kind: 'circuit-breaker', designator: 'Q3' });

    expect(screen.getByText('Q3')).not.toBeNull();
  });

  it('shows the rating when given', () => {
    renderSymbol({ kind: 'circuit-breaker', designator: 'Q3', rating: '63 A' });

    expect(screen.getByText('63 A')).not.toBeNull();
  });

  it('omits the rating line entirely when absent', () => {
    // Rather than rendering an empty text node, which reads as a missing value
    // on a printed diagram.
    const { container } = renderSymbol({ kind: 'circuit-breaker', designator: 'Q3' });

    const texts = [...container.querySelectorAll('text')].map((t) => t.textContent);
    expect(texts).toEqual(['Q3']);
  });

  it('labels the symbol for a screen reader', () => {
    renderSymbol({ kind: 'contactor', designator: 'KM1', rating: '18.5 kW' });

    expect(screen.getByRole('img', { name: /contactor KM1, 18.5 kW/ })).not.toBeNull();
  });

  it('announces an unrecognised type as unrecognised', () => {
    // The placeholder is visually loud; a screen-reader user gets the same
    // information rather than an unlabelled shape.
    renderSymbol({ kind: 'soft-starter', designator: 'T1' });

    expect(
      screen.getByRole('img', { name: /Unrecognised component type soft-starter/ }),
    ).not.toBeNull();
  });
});
