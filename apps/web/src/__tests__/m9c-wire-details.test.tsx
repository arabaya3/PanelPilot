import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/**
 * M9c: wire details entered, replaced and removed in the guided binding editor
 * (no JSON). SYNTHETIC data; mocked API (the real composition and save are
 * checked by the API tests and the browser run).
 */

const SRC = 'tenant/synth-src/v1';
const REF = 'tenant/synth-two-slot/v1';
const W = en.configuration.wires;

function at<T>(list: readonly T[] | undefined, index: number): T {
  const item = list?.[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

function doc(wires: Record<string, unknown>) {
  return {
    templates: [
      {
        ref: SRC,
        terminals: [{ id: 't-001', marking: 'A1' }],
      },
    ],
    patterns: [
      {
        ref: REF,
        slots: [{ id: 'src', template: SRC, device_kind: null }],
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
    components: [
      {
        id: 'comp-ka',
        board: 'MB-1',
        template: SRC,
        designation: { function: null, location: null, product: 'KA' },
      },
    ],
    bindings: [
      {
        id: 'bind-1',
        pattern: REF,
        board: 'MB-1',
        circuit: null,
        slots: { src: { component: 'comp-ka' } },
        strips: { out: 'strip-xp' },
        markings: { p1: '7' },
        cable: null,
        wires,
      },
    ],
  };
}

const BOARDS: BoardChoices[] = [
  { id: 'MB-1', circuits: [], cables: [] },
  { id: 'SB-1', circuits: [], cables: [] },
];

let latest: Record<string, unknown> = {};

function Harness({ initial }: { initial: Record<string, unknown> }) {
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
      boardChoices={BOARDS}
      onUnauthorized={vi.fn()}
      validateImpl={vi.fn()}
    />
  );
}

const wires = () => (latest.bindings as { wires: unknown }[])[0]?.wires;

function open(stored: Record<string, unknown> = {}, locale: 'en' | 'ar' | 'he' = 'en') {
  const view = renderApp(<Harness initial={doc(stored)} />, { locale, theme: 'light' });
  const section = within(screen.getByTestId('wire-details'));
  return { view, section, row: within(at(section.getAllByTestId('wire-row'), 0)) };
}

function type(element: HTMLElement, text: string) {
  fireEvent.change(element, { target: { value: text } });
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('M9c guided wire details', () => {
  it('names the wire by board, binding, pattern, net and readable ends; no index', () => {
    const { row } = open();
    const name = row.getByTestId('wire-row-name').textContent;
    expect(name).toBe(
      W.rowName
        .replace('{board}', 'MB-1')
        .replace('{binding}', 'bind-1')
        .replace('{pattern}', REF)
        .replace('{net}', 'n1')
        .replace('{signal}', 'S1')
        .replace('{from}', 'KA:A1')
        .replace('{to}', 'XP:7'),
    );
    expect(name).not.toContain('n1/0');
    expect(screen.getByTestId('wire-details-note').textContent).toBe(W.note);
  });

  it('a typed value is stored as entered by the user; empty fields stay unset', () => {
    const { row } = open();
    type(row.getByTestId('wire-number'), 'W7');
    expect(wires()).toEqual({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
    expect(row.getAllByText(W.source.user)).toHaveLength(1);
    expect(row.getAllByText(W.source.unset)).toHaveLength(2);
  });

  it('a value the server would refuse is said beside it and listed with a link', () => {
    const { section, row } = open();
    const cs = row.getByTestId('wire-cross_section_mm2');
    type(cs, '1,5');
    expect(row.getByTestId('wire-problem').textContent).toBe(W.problem.not_decimal);
    expect(cs.getAttribute('aria-invalid')).toBe('true');
    expect(cs.getAttribute('aria-describedby')).toContain(row.getByTestId('wire-problem').id);
    const link = section.getByTestId('wire-problem-link');
    fireEvent.click(link);
    expect(document.activeElement).toBe(cs);
    expect(screen.getByTestId('binding-problems').textContent).toMatch(/^1 /);
    type(cs, '1.5');
    expect(row.queryByTestId('wire-problem')).toBeNull();
  });

  it('an imported value is shown exactly, read only, and replaced only after confirming', async () => {
    const { row } = open({
      'n1/0': { colour: { value: 'BK', source: 'assumption', statement: 'synthetic' } },
    });
    const colour = row.getByTestId<HTMLInputElement>('wire-colour');
    expect(colour.value).toBe('BK');
    expect(colour.readOnly).toBe(true);
    expect(row.getByText(W.source.assumption.replace('{statement}', 'synthetic'))).toBeTruthy();
    fireEvent.click(row.getByTestId('wire-replace'));
    expect(row.getByTestId('wire-replace-confirm').textContent).toContain('BK');
    fireEvent.click(row.getByTestId('wire-replace-confirm-cancel'));
    expect(wires()).toEqual({
      'n1/0': { colour: { value: 'BK', source: 'assumption', statement: 'synthetic' } },
    });
    fireEvent.click(row.getByTestId('wire-replace'));
    fireEvent.click(row.getByTestId('wire-replace-confirm-confirm'));
    await tick();
    // Replace removes the stored value and leaves the field empty and editable: nothing is copied.
    expect(wires()).toEqual({});
    expect(row.getByTestId<HTMLInputElement>('wire-colour').readOnly).toBe(false);
    expect(document.activeElement).toBe(row.getByTestId('wire-colour'));
    type(row.getByTestId('wire-colour'), 'BU');
    expect(wires()).toEqual({ 'n1/0': { colour: { value: 'BU', source: 'user' } } });
  });

  it('removing lists what goes, is announced, and returns focus', async () => {
    const { row } = open({
      'n1/0': { number: { value: 'W7', source: 'user' }, colour: { value: 'BU', source: 'user' } },
    });
    fireEvent.click(row.getByTestId('wire-remove-entry'));
    const confirm = row.getByTestId('wire-remove-entry-confirm');
    expect(confirm.textContent).toContain('W7');
    expect(confirm.textContent).toContain('BU');
    fireEvent.click(row.getByTestId('wire-remove-entry-confirm-confirm'));
    await tick();
    expect(wires()).toEqual({});
    expect(screen.getByTestId('bindings-status').textContent).toMatch(/removed\.$/);
    expect(document.activeElement).toBe(row.getByTestId('wire-number'));
  });

  it('one field is removed alone, after confirming', () => {
    const { row } = open({ 'n1/0': { number: { value: 'W7', source: 'user' }, colour: 'BU' } });
    fireEvent.click(at(row.getAllByTestId('wire-remove-field'), 0));
    fireEvent.click(row.getByTestId('wire-remove-field-confirm-confirm'));
    expect(wires()).toEqual({ 'n1/0': { colour: 'BU' } });
  });

  it('a binding with details is removed only after they are listed', () => {
    open({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
    fireEvent.click(screen.getByTestId('binding-remove'));
    expect(screen.getByTestId('binding-remove-confirm').textContent).toContain('W7');
    fireEvent.click(screen.getByTestId('binding-remove-confirm-no'));
    expect((latest.bindings as unknown[]).length).toBe(1);
    fireEvent.click(screen.getByTestId('binding-remove'));
    fireEvent.click(screen.getByTestId('binding-remove-confirm-yes'));
    expect((latest.bindings as unknown[]).length).toBe(0);
  });

  it('the board cannot be changed while details are held, and says why', () => {
    open({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
    const board = screen.getByTestId<HTMLSelectElement>('binding-board');
    expect(board.disabled).toBe(true);
    expect(screen.getByTestId('binding-board-locked').textContent).toBe(W.boardLocked);
  });

  it('an unknown stored name and a stray key are kept, said, and removed only when asked', () => {
    const { section, row } = open({
      'n1/0': { number: 'W1', gauge: 'x' },
      'n1/9': { number: 'W9' },
    });
    expect(row.getByTestId('wire-extra').textContent).toContain('gauge');
    expect(section.getByTestId('wire-stray').textContent).toContain('n1/9');
    fireEvent.click(section.getByTestId('wire-remove-stray'));
    fireEvent.click(section.getByTestId('wire-remove-stray-confirm-confirm'));
    expect(wires()).toEqual({ 'n1/0': { number: 'W1', gauge: 'x' } });
  });

  it('an unreadable entry is read only and removed only after its stored form is listed', () => {
    const { section, row } = open({ 'n1/0': 'W7' });
    expect(row.getByTestId('wire-unreadable').textContent).toBe(W.problem.entry_unreadable);
    expect(row.getByTestId<HTMLInputElement>('wire-number').readOnly).toBe(true);
    const link = section.getByTestId('wire-problem-link');
    fireEvent.click(link);
    expect(document.activeElement).toBe(row.getByTestId('wire-remove-entry'));
    fireEvent.click(row.getByTestId('wire-remove-entry'));
    expect(row.getByTestId('wire-remove-entry-confirm').textContent).toContain('"W7"');
    fireEvent.click(row.getByTestId('wire-remove-entry-confirm-confirm'));
    expect(wires()).toEqual({});
  });

  it('a confirmation is dropped when the value it named changes underneath it', () => {
    const { row } = open({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
    fireEvent.click(row.getByTestId('wire-remove-field'));
    expect(row.getByTestId('wire-remove-field-confirm')).toBeTruthy();
    type(row.getByTestId('wire-number'), 'W9');
    expect(row.queryByTestId('wire-remove-field-confirm')).toBeNull();
    expect(wires()).toEqual({ 'n1/0': { number: { value: 'W9', source: 'user' } } });
  });

  it('stray and extra problems are linked from the overview to their removal', () => {
    const { section, row } = open({
      'n1/0': { number: 'W1', gauge: 'x' },
      'n1/9': { number: 'W9' },
    });
    const links = section.getAllByTestId('wire-problem-link');
    expect(links).toHaveLength(2);
    fireEvent.click(at(links, 0));
    expect(document.activeElement).toBe(row.getByTestId('wire-remove-extra'));
    fireEvent.click(at(links, 1));
    expect(document.activeElement).toBe(section.getByTestId('wire-remove-stray'));
  });

  it('the binding removal confirmation takes focus', async () => {
    open({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
    fireEvent.click(screen.getByTestId('binding-remove'));
    await tick();
    expect(document.activeElement?.textContent).toContain('W7');
  });

  it('renders in Arabic and Hebrew with the same messages', () => {
    const keys = (o: object): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        typeof v === 'object' && v !== null ? keys(v as object).map((x) => `${k}.${x}`) : [k],
      );
    expect(keys(ar.configuration.wires).sort()).toEqual(keys(W).sort());
    expect(keys(he.configuration.wires).sort()).toEqual(keys(W).sort());
    for (const locale of ['ar', 'he'] as const) {
      const { view, row } = open({}, locale);
      expect(row.getAllByTestId('wire-field')).toHaveLength(3);
      view.unmount();
    }
  });
});
