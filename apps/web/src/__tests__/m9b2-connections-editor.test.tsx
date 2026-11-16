import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type { V2ModelSummary } from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/** The item at `index`, or a test failure (no non-null assertions). */
function at<T>(list: readonly T[] | undefined, index: number): T {
  const item = list?.[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

/**
 * M9b2: connections of a pattern net added, edited and removed through the
 * guided editor (no JSON). Problems are said beside the fields; a change that
 * would detach saved wire details is refused with the bindings named.
 * SYNTHETIC data; no API.
 */

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';

const MODEL = {
  definitions: [],
  templates: [],
  components: [],
  strips: [],
  terminals: [],
  nets: [],
  connections: [],
  wires: [],
  findings: [],
} as unknown as V2ModelSummary;

function definition(ref: string, markings: string[]) {
  return {
    ref,
    function: 'unspecified',
    terminals: markings.map((marking, index) => ({
      id: `t-00${String(index + 1)}`,
      marking,
      kind: 'unspecified',
      required: null,
      group: null,
    })),
    contact_groups: [],
    parameters: [],
    provenance: { source: null, author: 'user-entered', created: '2026-11-16' },
    review: {
      status: 'unreviewed',
      reviewer: null,
      reviewed_at: null,
      standards: [],
      scope_note: null,
    },
    restricted_scope: [],
  };
}

const INITIAL = {
  templates: [definition(SRC, ['A1', 'A2']), definition(DST, ['B1'])],
  patterns: [],
  strips: [],
  components: [],
  bindings: [],
};

let latest: Record<string, unknown> = INITIAL;

function Harness({ initial = INITIAL }: { initial?: Record<string, unknown> }) {
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
      boards={['MB-1']}
      model={MODEL}
      onUnauthorized={vi.fn()}
      validateImpl={vi.fn()}
    />
  );
}

const C = en.configuration.patterns.conn;
const REF = 'tenant/synth-conn/v1';

function stored(wires: Record<string, unknown> | null) {
  return {
    ...INITIAL,
    patterns: [
      {
        ref: REF,
        slots: [{ id: 'a', template: SRC, device_kind: null }],
        strips: ['out'],
        points: [{ id: 'p', strip: 'out', offset: 1, level: 1, kind: 'unspecified' }],
        nets: [
          {
            role: 'line',
            signal: 'L',
            scope: 'binding',
            members: [
              { slot: 'a', terminal: 't-001' },
              { point: 'p', side: 'inner' },
              { point: 'p', side: 'outer' },
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
    bindings:
      wires === null
        ? []
        : [
            {
              id: 'bind-1',
              pattern: REF,
              board: 'MB-1',
              circuit: null,
              slots: {},
              strips: {},
              markings: {},
              cable: null,
              wires,
            },
          ],
  };
}

function connections(): Record<string, unknown>[] {
  const pattern = (latest.patterns as { nets: { connections: Record<string, unknown>[] }[] }[])[0];
  return pattern?.nets[0]?.connections ?? [];
}

function open(wires: Record<string, unknown> | null = null) {
  renderApp(<Harness initial={stored(wires)} />, { locale: 'en', theme: 'light' });
  const editor = screen.getByTestId('patterns-editor');
  return { editor, card: within(at(within(editor).getAllByTestId('pattern-net'), 0)) };
}

function type(element: HTMLElement, text: string) {
  fireEvent.change(element, { target: { value: text } });
}

function pick(element: HTMLElement, value: string) {
  fireEvent.change(element, { target: { value } });
}

describe('M9b2 guided connections', () => {
  it('adds a cable-core connection from the net members, with the core beside it', async () => {
    const { editor, card } = open();
    const add = within(card.getByTestId('pattern-add-connection'));
    const submit = add.getByTestId<HTMLButtonElement>('pattern-add-connection-submit');
    pick(add.getByTestId('pattern-connection-new-to'), '0');
    expect(add.getByTestId('pattern-add-connection-problem').textContent).toBe(C.problem.same_end);
    expect(submit.disabled).toBe(true);
    pick(add.getByTestId('pattern-connection-new-to'), '2');
    pick(add.getByTestId('pattern-connection-new-via'), 'cable_core');
    expect(add.getByTestId('pattern-add-connection-problem').textContent).toBe(
      C.problem.core_missing,
    );
    type(add.getByTestId('pattern-connection-new-core'), 'x');
    expect(add.getByTestId('pattern-add-connection-problem').textContent).toBe(
      C.problem.core_invalid,
    );
    type(add.getByTestId('pattern-connection-new-core'), '2');
    type(add.getByTestId('pattern-connection-new-label'), 'BN');
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    expect(connections()[1]).toEqual({ from: 0, to: 2, via: 'cable_core', core: 2, label: 'BN' });
    expect(card.getAllByTestId('pattern-connection')).toHaveLength(2);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(editor).getByTestId('patterns-status').textContent).toMatch(
      /Cable core added\.$/,
    );
    expect(document.activeElement).toBe(add.getByTestId('pattern-connection-new-from'));
  });

  it('offers only the five connection types, and point sides only for one point', () => {
    const { card } = open();
    const add = within(card.getByTestId('pattern-add-connection'));
    const options = [
      ...add.getByTestId<HTMLSelectElement>('pattern-connection-new-via').options,
    ].map((o) => o.value);
    expect(options).toEqual(['wire', 'cable_core', 'jumper', 'busbar', 'internal']);
    pick(add.getByTestId('pattern-connection-new-via'), 'internal');
    expect(add.getByTestId('pattern-add-connection-problem').textContent).toBe(
      C.problem.internal_not_sides,
    );
    pick(add.getByTestId('pattern-connection-new-from'), '1');
    pick(add.getByTestId('pattern-connection-new-to'), '2');
    expect(add.queryByTestId('pattern-add-connection-problem')).toBeNull();
    expect(add.queryByTestId('pattern-connection-new-core')).toBeNull();
  });

  it('edits a stored connection in place and says what a save would refuse', () => {
    const { card } = open();
    const row = within(at(card.getAllByTestId('pattern-connection'), 0));
    pick(row.getByTestId('pattern-connection-via'), 'cable_core');
    expect(connections()[0]).toEqual({
      from: 0,
      to: 1,
      via: 'cable_core',
      core: null,
      label: null,
    });
    expect(row.getByTestId('pattern-connection-problem').textContent).toBe(C.problem.core_missing);
    const core = row.getByTestId('pattern-connection-core');
    expect(core.getAttribute('aria-invalid')).toBe('true');
    type(core, '3');
    expect(connections()[0]).toMatchObject({ core: 3 });
    expect(row.queryByTestId('pattern-connection-problem')).toBeNull();
    pick(row.getByTestId('pattern-connection-via'), 'jumper');
    expect(connections()[0]).toEqual({ from: 0, to: 1, via: 'jumper', core: null, label: null });
  });

  it('removes a connection, then the member it named can go', async () => {
    const { editor, card } = open();
    const memberRemove = () =>
      at(card.getAllByTestId<HTMLButtonElement>('pattern-net-member-remove'), 0);
    expect(memberRemove().disabled).toBe(true);
    fireEvent.click(card.getByTestId('pattern-connection-remove'));
    expect(connections()).toEqual([]);
    expect(card.getByTestId('pattern-connections-none')).toBeTruthy();
    expect(memberRemove().disabled).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(within(editor).getByTestId('patterns-status').textContent).toMatch(/removed\.$/);
    expect(document.activeElement).toBe(card.getByTestId('pattern-connection-new-from'));
  });

  it('refuses changes that would detach saved wire details, naming them', () => {
    const { card } = open({ 'line/0': { number: { value: 'W7', source: 'user' } } });
    const row = within(at(card.getAllByTestId('pattern-connection'), 0));
    for (const id of [
      'pattern-connection-from',
      'pattern-connection-to',
      'pattern-connection-via',
    ]) {
      expect(row.getByTestId<HTMLSelectElement>(id).disabled).toBe(true);
    }
    expect(row.getByTestId('pattern-connection-locked').textContent).toBe(
      C.locked.replace('{uses}', 'bind-1 (line/0)'),
    );
    const remove = row.getByTestId<HTMLButtonElement>('pattern-connection-remove');
    expect(remove.disabled).toBe(true);
    expect(remove.getAttribute('aria-describedby')).toBe(
      row.getByTestId('pattern-connection-keep').id,
    );
    expect(card.getByTestId<HTMLButtonElement>('pattern-net-remove').disabled).toBe(true);
    expect(card.getByTestId('pattern-net-keep').textContent).toBe(
      C.netBlocked.replace('{uses}', 'bind-1 (line/0)'),
    );
    // A later connection can still be added and removed; the details stay with connection 0.
    fireEvent.click(card.getByTestId('pattern-add-connection-submit'));
    expect(connections()).toHaveLength(2);
    fireEvent.click(at(card.getAllByTestId('pattern-connection-remove'), 1));
    expect(connections()).toHaveLength(1);
    const bindings = latest.bindings as { wires: unknown }[];
    expect(bindings[0]?.wires).toEqual({ 'line/0': { number: { value: 'W7', source: 'user' } } });
  });

  it('a label typed in place keeps its inner space and loses end spaces on leaving', () => {
    const { card } = open();
    const row = within(at(card.getAllByTestId('pattern-connection'), 0));
    pick(row.getByTestId('pattern-connection-via'), 'cable_core');
    type(row.getByTestId('pattern-connection-core'), '1');
    const label = row.getByTestId('pattern-connection-label');
    type(label, 'L1 ');
    type(label, 'L1 A ');
    expect(connections()[0]).toMatchObject({ label: 'L1 A ' });
    fireEvent.blur(label);
    expect(connections()[0]).toMatchObject({ label: 'L1 A' });
  });

  it('ends chosen before the members changed are not used', () => {
    const { card } = open();
    const add = within(card.getByTestId('pattern-add-connection'));
    // Choose the third member (index 2) as From, then remove the connection and the second member.
    pick(add.getByTestId('pattern-connection-new-from'), '2');
    fireEvent.click(card.getByTestId('pattern-connection-remove'));
    fireEvent.click(at(card.getAllByTestId('pattern-net-member-remove'), 1));
    // Two members left: the form starts again from the first and second, not a stale index.
    expect(add.getByTestId<HTMLSelectElement>('pattern-connection-new-from').value).toBe('0');
    expect(add.getByTestId<HTMLSelectElement>('pattern-connection-new-to').value).toBe('1');
    fireEvent.click(add.getByTestId('pattern-add-connection-submit'));
    expect(connections()).toEqual([{ from: 0, to: 1, via: 'wire', core: null, label: null }]);
  });

  it('typed core text never hides a stored core it was not typed over', () => {
    const { card } = open();
    const row = () => within(at(card.getAllByTestId('pattern-connection'), 0));
    pick(row().getByTestId('pattern-connection-via'), 'cable_core');
    type(row().getByTestId('pattern-connection-core'), 'x');
    expect(row().getByTestId('pattern-connection-problem').textContent).toBe(
      C.problem.core_invalid,
    );
    // The stored core changes elsewhere (here: the type changes and back): the field follows it.
    pick(row().getByTestId('pattern-connection-via'), 'wire');
    pick(row().getByTestId('pattern-connection-via'), 'cable_core');
    expect(row().getByTestId<HTMLInputElement>('pattern-connection-core').value).toBe('');
    expect(row().getByTestId('pattern-connection-problem').textContent).toBe(
      C.problem.core_missing,
    );
  });

  it('new-connection problem is linked from the end and type fields', () => {
    const { card } = open();
    const add = within(card.getByTestId('pattern-add-connection'));
    pick(add.getByTestId('pattern-connection-new-to'), '0');
    const problem = add.getByTestId('pattern-add-connection-problem').id;
    for (const id of [
      'pattern-connection-new-from',
      'pattern-connection-new-to',
      'pattern-connection-new-via',
    ]) {
      expect(add.getByTestId(id).getAttribute('aria-describedby')).toBe(problem);
    }
  });

  it('renders in Arabic and Hebrew with the same controls', () => {
    for (const locale of ['ar', 'he'] as const) {
      const { unmount } = renderApp(<Harness initial={stored(null)} />, { locale, theme: 'light' });
      expect(screen.getAllByTestId('pattern-connection')).toHaveLength(1);
      expect(screen.getByTestId('pattern-add-connection-submit')).toBeTruthy();
      unmount();
    }
  });
});
