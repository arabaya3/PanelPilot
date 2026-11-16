import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import { bindingRowIds } from '@/components/design/binding-model';
import { bindingsOf } from '@/components/design/configuration-model';
import type { ConfigurationOutcome, ConfigurationReview } from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * M9d: "Use on another board" and the bindings overview, in the guided editor
 * (no JSON). SYNTHETIC data; mocked API (the real composition and save are
 * checked by the API tests and the browser run).
 */

const SRC = 'tenant/synth-src/v1';
const REF = 'tenant/synth-two-slot/v1';
const R = en.configuration.reuse;
const O = en.configuration.overview;

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

function Harness({
  initial,
  validateImpl = vi.fn(),
}: {
  initial: Record<string, unknown>;
  validateImpl?: (options: {
    configuration: Record<string, unknown>;
  }) => Promise<ConfigurationOutcome>;
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
      boardChoices={BOARDS}
      onUnauthorized={vi.fn()}
      validateImpl={validateImpl}
    />
  );
}

function open(stored: Record<string, unknown> = {}, locale: 'en' | 'ar' | 'he' = 'en') {
  const view = renderApp(<Harness initial={doc(stored)} />, { locale, theme: 'light' });
  const section = within(screen.getByTestId('wire-details'));
  return { view, section, row: within(at(section.getAllByTestId('wire-row'), 0)) };
}

function type(element: HTMLElement, text: string) {
  fireEvent.change(element, { target: { value: text } });
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const W7 = { 'n1/0': { number: { value: 'W7', source: 'user' } } };
const bindings = () => latest.bindings as Record<string, unknown>[];
const entries = () => screen.getAllByTestId('binding-entry');

describe('M9d use on another board', () => {
  it('asks for the board (no default, not the source board) and suggests a free name', () => {
    open(W7);
    fireEvent.click(screen.getByTestId('binding-reuse'));
    const board = screen.getByTestId<HTMLSelectElement>('binding-reuse-board');
    expect(board.value).toBe('');
    expect([...board.options].map((o) => o.value)).toEqual(['', 'SB-1']);
    const submit = screen.getByTestId<HTMLButtonElement>('binding-reuse-submit');
    expect(submit.disabled).toBe(true);
    fireEvent.change(board, { target: { value: 'SB-1' } });
    expect(screen.getByTestId<HTMLInputElement>('binding-reuse-id').value).toBe('bind-1-SB-1');
    expect(submit.disabled).toBe(false);
    type(screen.getByTestId('binding-reuse-id'), 'bind-1');
    expect(screen.getByTestId('binding-problem').textContent).toBe(
      en.configuration.bindings.idProblem.taken,
    );
    expect(submit.disabled).toBe(true);
  });

  it('adds a draft that shares only the pattern; the source and its wire details stay put', async () => {
    open(W7);
    const before = JSON.stringify(bindings()[0]);
    fireEvent.click(screen.getByTestId('binding-reuse'));
    fireEvent.change(screen.getByTestId('binding-reuse-board'), { target: { value: 'SB-1' } });
    fireEvent.click(screen.getByTestId('binding-reuse-submit'));
    await tick();
    expect(JSON.stringify(bindings()[0])).toBe(before);
    expect(bindings()[1]).toEqual({
      id: 'bind-1-SB-1',
      pattern: REF,
      board: 'SB-1',
      circuit: null,
      slots: {},
      strips: {},
      markings: {},
      cable: null,
      wires: {},
    });
    const draft = within(at(entries(), 1));
    expect(document.activeElement).toBe(at(entries(), 1));
    expect(screen.getByTestId('bindings-status').textContent).toContain('bind-1-SB-1');
    expect(draft.getByTestId('binding-reuse-shared').textContent).toContain('bind-1');
    expect(draft.getByTestId('binding-reuse-enter').textContent).toContain('src');
    expect(draft.getByTestId('binding-reuse-summary').textContent).toContain(R.notCopied);
    // Nothing chosen for the user: every slot, strip and marking is unset, and no wire value is there.
    expect(draft.getByTestId<HTMLSelectElement>('binding-slot-component').value).toBe('');
    expect(draft.getByTestId<HTMLSelectElement>('binding-role-strip').value).toBe('');
    expect(draft.getByTestId<HTMLInputElement>('point-marking').value).toBe('');
    expect(draft.getByTestId<HTMLInputElement>('wire-number').value).toBe('');
  });

  it('cancel adds nothing and returns focus to the action', async () => {
    open();
    fireEvent.click(screen.getByTestId('binding-reuse'));
    fireEvent.click(screen.getByTestId('binding-reuse-cancel'));
    await tick();
    expect(bindings()).toHaveLength(1);
    expect(document.activeElement).toBe(screen.getByTestId('binding-reuse'));
  });
});

describe('M9d bindings overview', () => {
  it('groups bindings by board and links each local problem to its field', () => {
    open();
    fireEvent.click(screen.getByTestId('binding-reuse'));
    fireEvent.change(screen.getByTestId('binding-reuse-board'), { target: { value: 'SB-1' } });
    fireEvent.click(screen.getByTestId('binding-reuse-submit'));
    const overview = within(screen.getByTestId('binding-overview'));
    expect(
      overview.getAllByTestId('overview-board').map((b) => b.querySelector('h5')?.textContent),
    ).toEqual([O.board.replace('{board}', 'MB-1'), O.board.replace('{board}', 'SB-1')]);
    const draftIssues = within(at(overview.getAllByTestId('overview-binding'), 1));
    const links = draftIssues.getAllByTestId('overview-local-link');
    expect(links.length).toBeGreaterThanOrEqual(3);
    fireEvent.click(at(links, 0));
    expect(document.activeElement).toBe(
      within(at(entries(), 1)).getByTestId('binding-slot-component'),
    );
    // The source binding (complete here) shows no local problem, and nothing says it is complete or safe.
    expect(
      within(at(overview.getAllByTestId('overview-binding'), 0)).getByTestId('overview-local')
        .textContent,
    ).toContain(O.localNone);
    expect(screen.getByTestId('binding-overview-note').textContent).toBe(O.note);
    expect(screen.getByTestId('binding-overview-no-validation')).toBeTruthy();
  });

  it('the binding name links to the binding', () => {
    open();
    fireEvent.click(screen.getByTestId('overview-binding-link'));
    expect(document.activeElement).toBe(at(entries(), 0));
  });
});

describe('M9d overview: server validation, current or outdated', () => {
  it('shows findings naming this binding, then marks them outdated after an edit', async () => {
    const stored = doc({});
    const wire = [...bindingRowIds(stored, at(bindingsOf(stored), 0)).wires][0] ?? '';
    const validateImpl = vi.fn(() =>
      Promise.resolve({
        kind: 'reviewed',
        review: {
          based_on_revision: 4,
          snapshot_schema: 'pp-design/2',
          written_schema: 'pp-design/3',
          saveable: true,
          snapshot_sha256: 'x',
          findings: [],
          model: {
            definitions: [],
            templates: [],
            components: [],
            strips: [],
            terminals: [],
            nets: [],
            connections: [],
            wires: [],
            findings: [
              {
                code: 'wire_attribute_unset',
                severity: 'warning',
                message: 'Wire number is not set (synthetic).',
                location: { wire },
                unresolved: true,
                blocks_approval: false,
                blocks_execution: true,
                requires_engineering_confirmation: false,
              },
            ],
          },
        } as unknown as ConfigurationReview,
      } as ConfigurationOutcome),
    );
    renderApp(<Harness initial={stored} validateImpl={validateImpl} />, {
      locale: 'en',
      theme: 'light',
    });
    fireEvent.click(screen.getByTestId('configuration-validate'));
    await tick();
    await tick();
    expect(screen.getByTestId('binding-overview-validation').textContent).toBe(
      O.validation.replace('{revision}', '4'),
    );
    expect(screen.getByTestId('overview-found').textContent).toContain('wire_attribute_unset');
    // An edit: the same findings now read as outdated, never as current.
    type(screen.getByTestId('point-marking'), '8');
    expect(screen.queryByTestId('overview-found')).toBeNull();
    expect(screen.getByTestId('overview-found-outdated').textContent).toContain(
      'wire_attribute_unset',
    );
    expect(screen.getByTestId('binding-overview-outdated').textContent).toContain('4');
  });
});
