import { act, fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { bindingNeeds } from '@/components/design/binding-needs';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * M13 (binding prerequisites), on the M10–M12 fixture: the preview of the current load schedule with the configuration; choices
 * from the server's design of the current inputs; moved references said beside
 * their binding and kept only by an explicit action. SYNTHETIC data; mocked API
 * (the real composition and save are checked by the API tests and the browser run).
 */

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';
const REF = 'tenant/synth-two-slot/v1';

function component(id: string, board: string, template: string, product: string) {
  return { id, board, template, designation: { function: null, location: null, product } };
}

const INITIAL = {
  templates: [],
  patterns: [
    {
      ref: REF,
      slots: [
        { id: 'src', template: SRC, device_kind: null },
        { id: 'dst', template: DST, device_kind: null },
      ],
      strips: ['out'],
      points: [{ id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' }],
      nets: [
        {
          role: 'n1',
          signal: 'S1',
          scope: 'binding',
          members: [
            { slot: 'src', terminal: 't-001' },
            { point: 'p1', side: 'inner' },
          ],
          connections: [{ from: 0, to: 1, via: 'wire', core: null, label: null }],
        },
      ],
      provenance: { source: null, author: 'user-entered', created: '2026-11-16' },
      review: {
        status: 'unreviewed',
        reviewer: null,
        reviewed_at: null,
        standards: [],
        scope_note: null,
      },
    },
  ],
  strips: [
    {
      id: 'strip-xp',
      board: 'MB-1',
      template: null,
      designation: { function: null, location: null, product: 'XP' },
    },
  ],
  components: [component('comp-ka', 'MB-1', SRC, 'KA'), component('comp-kb', 'MB-1', DST, 'KB')],
  bindings: [],
};

// The composed rows as the server derives their ids for bind-1 (see m9b1-binding-model.test.ts),
// plus rows of another binding the preview must not show.

const BOUND = {
  ...INITIAL,
  bindings: [
    {
      id: 'bind-1',
      pattern: REF,
      board: 'MB-1',
      circuit: 'c1',
      slots: { src: { component: 'comp-ka' }, dst: { component: 'comp-kb' } },
      strips: { out: 'strip-xp' },
      markings: { p1: '7' },
      cable: null,
      wires: { 'n1/0': { number: { value: 'W7', source: 'user' } } },
    },
  ],
};

const N = en.configuration.needs;
const fill = (text: string, values: Record<string, string>) =>
  Object.entries(values).reduce((out, [k, v]) => out.replaceAll(`{${k}}`, v), text);

/** A binding of the same pattern on SB-1, where nothing exists yet (as "Use on another board"). */
const ON_SB1 = {
  id: 'bind-1-SB-1',
  pattern: REF,
  board: 'SB-1',
  circuit: null,
  slots: {},
  strips: {},
  markings: {},
  cable: null,
  wires: {},
};
const WITH_EMPTY = { ...BOUND, bindings: [...(BOUND.bindings as unknown[]), ON_SB1] };

function NeedsPanel({
  initial,
  choices = BOARDS2,
}: {
  initial: Record<string, unknown>;
  choices?: BoardChoices[] | null;
}) {
  const [value, setValue] = useState<Record<string, unknown>>(initial);
  latest = value;
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={1}
      value={value}
      onChange={setValue}
      status="none"
      boards={['MB-1', 'SB-1']}
      model={null}
      boardChoices={choices}
      onUnauthorized={vi.fn()}
      validateImpl={vi.fn() as never}
    />
  );
}
let latest: Record<string, unknown> = {};
const BOARDS2: BoardChoices[] = [
  { id: 'MB-1', circuits: [{ id: 'c1', label: 'c1 · Lighting' }], cables: [] },
  { id: 'SB-1', circuits: [], cables: [] },
];
const entryOf = (id: string) =>
  screen
    .getAllByTestId('binding-entry')
    .find((el) => within(el).getByTestId('binding-id').textContent === id) as HTMLElement;

describe('bindingNeeds', () => {
  it('says create where nothing compatible exists, and choose where something does', () => {
    const empty = bindingNeeds(WITH_EMPTY, ON_SB1, BOARDS2, false);
    expect(empty).toEqual([
      { kind: 'create', what: 'component', slot: 'src', template: SRC },
      { kind: 'create', what: 'component', slot: 'dst', template: DST },
      { kind: 'create', what: 'strip', role: 'out' },
    ]);
    // A compatible component and a strip on SB-1: choose them, never create duplicates.
    const stocked = {
      ...WITH_EMPTY,
      components: [...(INITIAL.components as unknown[]), component('comp-kc', 'SB-1', SRC, 'KC')],
      strips: [
        ...(INITIAL.strips as unknown[]),
        {
          id: 'strip-ys',
          board: 'SB-1',
          template: null,
          designation: { function: null, location: null, product: 'YS' },
        },
      ],
    };
    expect(bindingNeeds(stocked, ON_SB1 as never, BOARDS2, false)).toEqual([
      { kind: 'choose', field: 'slot', key: 'src', count: 1 },
      { kind: 'create', what: 'component', slot: 'dst', template: DST },
      { kind: 'choose', field: 'role', key: 'out', count: 1 },
    ]);
  });

  it('says design or refresh for a cable whose choices are not known', () => {
    const cabled = {
      ...WITH_EMPTY,
      patterns: [
        {
          ...(INITIAL.patterns[0] as Record<string, unknown>),
          nets: [
            {
              ...(INITIAL.patterns[0] as { nets: Record<string, unknown>[] }).nets[0],
              connections: [{ from: 0, to: 1, via: 'cable_core', core: 1, label: null }],
            },
          ],
        },
      ],
    };
    const needs = (boards: BoardChoices[] | null, refreshable: boolean) =>
      bindingNeeds(cabled, ON_SB1 as never, boards, refreshable).filter(
        (n) => 'field' in n && n.field === 'cable',
      );
    expect(needs(null, true)).toEqual([{ kind: 'design', field: 'cable', refreshable: true }]);
    expect(needs(null, false)).toEqual([{ kind: 'design', field: 'cable', refreshable: false }]);
    expect(needs(BOARDS2, false)).toEqual([{ kind: 'choose', field: 'cable', count: 0 }]);
  });

  it('a complete binding needs nothing', () => {
    expect(bindingNeeds(BOUND, BOUND.bindings[0] as never, BOARDS2, false)).toEqual([]);
  });
});

describe('Needed on this board', () => {
  it('links to the add form with the board shown, back to the field, never creating or choosing', () => {
    renderApp(<NeedsPanel initial={WITH_EMPTY} />, { locale: 'en', theme: 'light' });
    const row = entryOf('bind-1-SB-1');
    const needs = within(row).getByTestId('binding-needs');
    expect(within(needs).getAllByTestId('binding-need')[0]?.textContent).toContain(
      fill(N.createComponent, { slot: 'src', template: SRC, board: 'SB-1' }),
    );
    const before = JSON.stringify(latest);
    fireEvent.click(within(needs).getByTestId('binding-need-create-0'));
    // The existing add-component form, its first field focused, SB-1 shown and still editable.
    const form = screen.getByTestId('add-component');
    expect(form.contains(document.activeElement)).toBe(true);
    const board = within(form).getAllByRole('combobox')[0] as HTMLSelectElement;
    expect(board.value).toBe('SB-1');
    expect(board.disabled).toBe(false);
    // Nothing was created or chosen by following the link.
    expect(JSON.stringify(latest)).toBe(before);
    fireEvent.click(within(form).getByTestId('back-to-binding'));
    expect(document.activeElement?.id.endsWith('-slot-src')).toBe(true);
    expect(row.contains(document.activeElement)).toBe(true);
  });

  it("the suggested board stays changeable: a re-render does not reset the user's board", () => {
    function FreshBoards() {
      const [value, setValue] = useState<Record<string, unknown>>(WITH_EMPTY);
      return (
        <ConfigurationPanel
          token="t"
          projectId="p1"
          latestRevision={1}
          value={value}
          onChange={setValue}
          status="none"
          // A new array every render, as the page passes it.
          boards={['MB-1', 'SB-1'].map((name) => name)}
          model={null}
          boardChoices={BOARDS2}
          onUnauthorized={vi.fn()}
          validateImpl={vi.fn() as never}
        />
      );
    }
    renderApp(<FreshBoards />, { locale: 'en', theme: 'light' });
    fireEvent.click(within(entryOf('bind-1-SB-1')).getByTestId('binding-need-create-0'));
    const form = screen.getByTestId('add-component');
    const board = within(form).getAllByRole('combobox')[0] as HTMLSelectElement;
    expect(board.value).toBe('SB-1');
    fireEvent.change(board, { target: { value: 'MB-1' } });
    // An edit elsewhere re-renders the page (a new board list); the user's choice stays.
    fireEvent.change(within(entryOf('bind-1')).getAllByTestId('point-marking')[0] as HTMLElement, {
      target: { value: '8' },
    });
    expect((within(form).getAllByRole('combobox')[0] as HTMLSelectElement).value).toBe('MB-1');
  });

  it('where a compatible item exists, it leads to the selector, not to creation', () => {
    const stocked = {
      ...WITH_EMPTY,
      components: [...(INITIAL.components as unknown[]), component('comp-kc', 'SB-1', SRC, 'KC')],
    };
    renderApp(<NeedsPanel initial={stocked} />, { locale: 'en', theme: 'light' });
    const needs = within(entryOf('bind-1-SB-1')).getByTestId('binding-needs');
    expect(within(needs).getAllByTestId('binding-need')[0]?.textContent).toContain(
      fill(N.chooseComponent, { slot: 'src', count: '1' }),
    );
    fireEvent.click(within(needs).getByTestId('binding-need-choose-0'));
    const select = document.activeElement as HTMLSelectElement;
    expect(select.id.endsWith('-slot-src')).toBe(true);
    expect(select.value).toBe('');
  });

  it('back to a removed binding reaches the bindings section', () => {
    renderApp(<NeedsPanel initial={WITH_EMPTY} />, { locale: 'en', theme: 'light' });
    fireEvent.click(within(entryOf('bind-1-SB-1')).getByTestId('binding-need-create-2'));
    const strips = screen.getByTestId('add-strip');
    expect(strips.contains(document.activeElement)).toBe(true);
    // The binding is removed meanwhile.
    fireEvent.click(within(entryOf('bind-1-SB-1')).getByTestId('binding-remove'));
    fireEvent.click(within(strips).getByTestId('back-to-binding'));
    expect(document.activeElement?.id.endsWith('-bindings')).toBe(true);
  });

  it('a created item is not chosen for the binding', () => {
    renderApp(<NeedsPanel initial={WITH_EMPTY} />, { locale: 'en', theme: 'light' });
    fireEvent.click(within(entryOf('bind-1-SB-1')).getByTestId('binding-need-create-2'));
    const form = screen.getByTestId('add-strip');
    fireEvent.click(within(form).getByTestId('add-strip-submit'));
    const sb1 = (latest.bindings as { id: string; strips: Record<string, string> }[]).find(
      (b) => b.id === 'bind-1-SB-1',
    );
    expect(sb1?.strips).toEqual({});
    // Now there is a strip on SB-1: the need says choose it.
    expect(within(entryOf('bind-1-SB-1')).getByTestId('binding-needs').textContent).toContain(
      fill(N.chooseStrip, { role: 'out', count: '1' }),
    );
  });

  it('announces only a change of the count, once typing settles, in its own small status', () => {
    vi.useFakeTimers();
    try {
      renderApp(<NeedsPanel initial={WITH_EMPTY} />, { locale: 'en', theme: 'light' });
      const row = entryOf('bind-1-SB-1');
      const status = within(row).getByTestId('binding-needs-status');
      expect(status.getAttribute('role')).toBe('status');
      expect(within(row).getByTestId('binding-needs').getAttribute('aria-live')).toBeNull();
      // Nothing said on opening.
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(status.textContent).toBe('');
      // A strip is added on SB-1 (the role now says choose; the count stays 3): still silent.
      fireEvent.click(within(row).getByTestId('binding-need-create-2'));
      fireEvent.click(within(screen.getByTestId('add-strip')).getByTestId('add-strip-submit'));
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(status.textContent).toBe('');
      // Choosing it lowers the count: said once, after the delay.
      const role = within(row).getByTestId<HTMLSelectElement>('binding-role-strip');
      fireEvent.change(role, { target: { value: role.options[1]?.value ?? '' } });
      expect(within(row).getByTestId('binding-needs-status').textContent).toBe('');
      act(() => {
        vi.advanceTimersByTime(900);
      });
      expect(within(row).getByTestId('binding-needs-status').textContent).toBe(
        fill(N.status, { binding: 'bind-1-SB-1', count: '2' }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('is in Arabic and Hebrew too', async () => {
    const ar = (await import('@/messages/ar.json')).default.configuration.needs;
    const he = (await import('@/messages/he.json')).default.configuration.needs;
    expect(Object.keys(ar).sort()).toEqual(Object.keys(N).sort());
    expect(Object.keys(he).sort()).toEqual(Object.keys(N).sort());
  });
});
