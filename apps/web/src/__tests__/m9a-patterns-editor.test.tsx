import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type { V2ModelSummary } from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/** The item at `index`, or a test failure (no non-null assertions). */
function at<T>(list: readonly T[] | undefined, index: number): T {
  const item = list?.[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

/**
 * M9a: a connection pattern built entirely through the guided editor (no JSON),
 * with problems said beside the field, removals checked first, and every add
 * or remove announced with focus kept in the pattern. SYNTHETIC data; no API.
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

const P = en.configuration.patterns;

function type(element: HTMLElement, text: string) {
  fireEvent.change(element, { target: { value: text } });
}

function build() {
  renderApp(<Harness />, { locale: 'en', theme: 'light' });
  const editor = screen.getByTestId('patterns-editor');
  type(within(editor).getByTestId('add-pattern-id'), 'synth-two-slot');
  fireEvent.click(within(editor).getByTestId('add-pattern-submit'));
  const row = within(editor).getByTestId('pattern-entry');
  const r = within(row);
  for (const [slot, template] of [
    ['src', SRC],
    ['dst', DST],
  ] as const) {
    type(r.getByTestId('pattern-new-slot'), slot);
    fireEvent.change(r.getByTestId('pattern-new-slot-template'), { target: { value: template } });
    fireEvent.click(r.getByTestId('pattern-add-slot'));
  }
  type(r.getByTestId('pattern-new-role'), 'out');
  fireEvent.click(r.getByTestId('pattern-add-role'));
  for (const point of ['p1', 'p2']) {
    type(r.getByTestId('pattern-new-point'), point);
    fireEvent.click(r.getByTestId('pattern-add-point'));
  }
  const addNet = (role: string, signal: string) => {
    type(r.getByTestId('pattern-new-net'), role);
    type(r.getByTestId('pattern-new-net-signal'), signal);
    fireEvent.click(r.getByTestId('pattern-add-net'));
  };
  const net = (role: string) =>
    within(
      at(
        r.getAllByTestId('pattern-net').filter((card) => card.textContent.includes(`Net ${role}`)),
        0,
      ),
    );
  const slotMember = (role: string, slot: string, terminal: string) => {
    const n = net(role);
    fireEvent.change(n.getByTestId('pattern-member-kind'), { target: { value: 'slot' } });
    fireEvent.change(n.getByTestId('pattern-member-slot'), { target: { value: slot } });
    fireEvent.change(n.getByTestId('pattern-member-terminal'), { target: { value: terminal } });
    fireEvent.click(n.getByTestId('pattern-add-member-submit'));
  };
  const pointMember = (role: string, point: string) => {
    const n = net(role);
    fireEvent.change(n.getByTestId('pattern-member-kind'), { target: { value: 'point' } });
    fireEvent.change(n.getByTestId('pattern-member-point'), { target: { value: point } });
    fireEvent.change(n.getByTestId('pattern-member-side'), { target: { value: 'inner' } });
    fireEvent.click(n.getByTestId('pattern-add-member-submit'));
  };
  addNet('n1', 'S1');
  slotMember('n1', 'src', 't-001');
  pointMember('n1', 'p1');
  slotMember('n1', 'dst', 't-001');
  addNet('n2', 'S2');
  slotMember('n2', 'src', 't-002');
  pointMember('n2', 'p2');
  return { editor, row, r, net };
}

describe('M9a guided pattern editor', () => {
  it('builds a two-slot pattern with a strip role, points and explicit nets without JSON', () => {
    const { r, net } = build();
    const pattern = at(latest.patterns as Record<string, unknown>[], 0);
    expect(pattern.slots).toEqual([
      { id: 'src', template: SRC, device_kind: null },
      { id: 'dst', template: DST, device_kind: null },
    ]);
    expect(pattern.strips).toEqual(['out']);
    expect(pattern.points).toEqual([
      { id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' },
      { id: 'p2', strip: 'out', offset: 2, level: 1, kind: 'unspecified' },
    ]);
    expect(pattern.nets).toEqual([
      {
        role: 'n1',
        signal: 'S1',
        scope: 'binding',
        members: [
          { slot: 'src', terminal: 't-001' },
          { point: 'p1', side: 'inner' },
          { slot: 'dst', terminal: 't-001' },
        ],
        connections: [],
      },
      {
        role: 'n2',
        signal: 'S2',
        scope: 'binding',
        members: [
          { slot: 'src', terminal: 't-002' },
          { point: 'p2', side: 'inner' },
        ],
        connections: [],
      },
    ]);
    expect((pattern.review as { status: string }).status).toBe('unreviewed');
    expect(r.getByTestId('pattern-unreviewed').textContent).toBe(P.unreviewed);
    // Each net says which terminals belong to it, by marking.
    expect(
      net('n1')
        .getAllByTestId('pattern-net-member-text')
        .map((m) => m.textContent),
    ).toEqual([
      'Slot src: terminal A1 (t-001)',
      'Point p1 (out): inner side',
      'Slot dst: terminal B1 (t-001)',
    ]);
    expect(r.queryByTestId('pattern-problems')).toBeNull();
  });

  it('says problems beside the field and does not add what it refuses', () => {
    const { r } = build();
    type(r.getByTestId('pattern-new-slot'), 'src');
    expect(r.getByTestId('pattern-new-slot-problem').textContent).toBe(P.partProblem.taken);
    expect(r.getByTestId('pattern-new-slot').getAttribute('aria-describedby')).toBe(
      r.getByTestId('pattern-new-slot-problem').id,
    );
    expect(r.getByTestId<HTMLButtonElement>('pattern-add-slot').disabled).toBe(true);
    type(r.getByTestId('pattern-new-point-offset'), '0');
    expect(r.getByTestId('pattern-new-point-offset-problem').textContent).toBe(P.positionProblem);
    // An existing point's offset typed invalid is said, and the stored value is kept.
    const offset = at(r.getAllByTestId('pattern-point-offset'), 0);
    type(offset, 'x');
    expect(r.getAllByTestId('pattern-point-offset-problem')).toHaveLength(1);
    expect(at(at(latest.patterns as { points: { offset: number }[] }[], 0).points, 0).offset).toBe(
      1,
    );
    // A slot moved to a template that lacks a member's terminal: said at the member and the pattern.
    fireEvent.change(at(r.getAllByTestId('pattern-slot-template'), 1), { target: { value: SRC } });
    fireEvent.change(at(r.getAllByTestId('pattern-slot-template'), 0), { target: { value: DST } });
    expect(r.getAllByTestId('pattern-net-member-problem').map((p) => p.textContent)).toContain(
      P.memberProblem.not_on_template,
    );
    expect(r.getByTestId('pattern-problems').textContent).toMatch(/save will be refused/);
  });

  it('blocks removing what is used and names the user', () => {
    const { r } = build();
    const slotRemove = at(r.getAllByTestId<HTMLButtonElement>('pattern-slot-remove'), 0);
    expect(slotRemove.disabled).toBe(true);
    const described = document.getElementById(slotRemove.getAttribute('aria-describedby') ?? '');
    expect(described?.textContent).toContain('n1');
    expect(r.getByTestId<HTMLButtonElement>('pattern-role-remove').disabled).toBe(true);
    expect(at(r.getAllByTestId<HTMLButtonElement>('pattern-point-remove'), 0).disabled).toBe(true);
  });

  it('announces adds and removals and keeps focus in the pattern', async () => {
    const { editor, r, net } = build();
    const status = within(editor).getByTestId('patterns-status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe('Point p2 (out): inner side added.');
    const n1 = net('n1');
    const remove = at(n1.getAllByTestId('pattern-net-member-remove'), 2);
    remove.focus();
    fireEvent.click(remove);
    expect(status.textContent).toBe('Slot dst: terminal B1 (t-001) removed.');
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.activeElement).toBe(net('n1').getByTestId('pattern-member-kind'));
    // The slot is now free, so it can go; focus returns to the new-slot field.
    const slotRemove = at(r.getAllByTestId<HTMLButtonElement>('pattern-slot-remove'), 1);
    expect(slotRemove.disabled).toBe(false);
    fireEvent.click(slotRemove);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(status.textContent).toBe('Slot dst removed.');
    expect(document.activeElement).toBe(r.getByTestId('pattern-new-slot'));
  });

  it('keeps stored connections and refuses to remove a member one names', () => {
    const stored = {
      ...INITIAL,
      patterns: [
        {
          ref: 'tenant/imported/v1',
          slots: [{ id: 'a', template: SRC, device_kind: 'circuit_breaker' }],
          strips: ['out'],
          points: [{ id: 'p', strip: 'out', offset: 1, level: 1, kind: 'power_line' }],
          nets: [
            {
              role: 'line',
              signal: 'L',
              scope: 'binding',
              members: [
                { slot: 'a', terminal: 't-001' },
                { point: 'p', side: 'inner' },
              ],
              connections: [{ from: 0, to: 1, via: 'wire', core: null, label: null }],
            },
          ],
          provenance: { source: 'synthetic', author: 'test', created: '2026-11-16' },
          review: {
            status: 'unreviewed',
            reviewer: null,
            reviewed_at: null,
            standards: [],
            scope_note: null,
          },
        },
      ],
    };
    renderApp(<Harness initial={stored} />, { locale: 'en', theme: 'light' });
    const row = within(screen.getByTestId('pattern-entry'));
    expect(row.getByTestId('pattern-net-connections').textContent).toMatch(/^1 stored connections/);
    expect(
      row.getAllByTestId<HTMLButtonElement>('pattern-net-member-remove').every((b) => b.disabled),
    ).toBe(true);
    expect(row.getByText(/Device kind circuit_breaker/)).toBeTruthy();
    expect(row.getByTestId('pattern-net-remove').textContent).toBe(
      P.removeNetConnections.replace('{net}', 'line').replace('{count}', '1'),
    );
  });

  it('has the same messages in Arabic and Hebrew', () => {
    const keys = (o: object): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        typeof v === 'object' && v !== null ? keys(v as object).map((x) => `${k}.${x}`) : [k],
      );
    expect(keys(ar.configuration.patterns).sort()).toEqual(keys(P).sort());
    expect(keys(he.configuration.patterns).sort()).toEqual(keys(P).sort());
  });
});
