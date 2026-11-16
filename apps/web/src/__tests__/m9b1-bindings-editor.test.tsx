import { act, fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type {
  ConfigurationOutcome,
  ConfigurationReview,
  Refusal,
  V2ModelSummary,
} from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/**
 * M9b1: a binding created entirely through the guided editor, with a preview
 * the server composes (here a stand-in for the validate call). SYNTHETIC data;
 * mocked API: the real composition is checked by the API tests and the browser run.
 */

function at<T>(list: readonly T[] | undefined, index: number): T {
  const item = list?.[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';
const REF = 'tenant/synth-two-slot/v1';
const B = en.configuration.bindings;

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

const BOARDS: BoardChoices[] = [
  { id: 'MB-1', circuits: [{ id: 'c1', label: 'c1 · Lighting' }], cables: [] },
  { id: 'SB-1', circuits: [], cables: [] },
];

// The composed rows as the server derives their ids for bind-1 (see m9b1-binding-model.test.ts),
// plus rows of another binding the preview must not show.
function model(marking: string): V2ModelSummary {
  return {
    definitions: [],
    components: [],
    strips: [
      {
        id: 's-x',
        board_id: 'MB-1',
        designation: 'XP',
        template_ref: null,
        points: [
          {
            id: 'p-5b8e47a76d272eaee692caaf',
            position: 1,
            level: 1,
            marking,
            kind: 'unspecified',
            pattern_ref: REF,
          },
          {
            id: 'p-other',
            position: 2,
            level: 1,
            marking: '99',
            kind: 'unspecified',
            pattern_ref: REF,
          },
        ],
      },
    ],
    nets: [
      {
        id: 'n-a55eae8a8ed8e0240fc78b53',
        board_id: 'MB-1',
        signal: 'S1',
        origin: 'derived',
        members: ['KA:A1', `XP:${marking} (inner)`],
        pattern_refs: [REF],
      },
      {
        id: 'n-other',
        board_id: 'MB-1',
        signal: 'S9',
        origin: 'derived',
        members: ['ZZ:9'],
        pattern_refs: [REF],
      },
    ],
    connections: [
      {
        id: 'x-68a8425e238b1342873a4233',
        net_id: 'n-a55eae8a8ed8e0240fc78b53',
        start: 'KA:A1',
        end: `XP:${marking} (inner)`,
        via: 'wire',
        cable_core: null,
        pattern_ref: REF,
      },
      {
        id: 'x-other',
        net_id: 'n-other',
        start: 'ZZ:9',
        end: 'XP:99 (inner)',
        via: 'wire',
        cable_core: null,
        pattern_ref: REF,
      },
    ],
    wires: [
      {
        board_id: 'MB-1',
        wire_id: 'w-2d28959012cb5b4ba8297f9e',
        connection_id: 'x-68a8425e238b1342873a4233',
        start: 'KA:A1',
        end: `XP:${marking} (inner)`,
        net_id: 'n-a55eae8a8ed8e0240fc78b53',
        signal: 'S1',
        number: null,
        cross_section_mm2: null,
        colour: null,
        pattern_ref: REF,
        flags: [],
      },
      {
        board_id: 'MB-1',
        wire_id: 'w-other',
        connection_id: 'x-other',
        start: 'ZZ:9',
        end: 'XP:99 (inner)',
        net_id: 'n-other',
        signal: 'S9',
        number: null,
        cross_section_mm2: null,
        colour: null,
        pattern_ref: REF,
        flags: [],
      },
    ],
    terminals: [],
    findings: [],
  } as unknown as V2ModelSummary;
}

function reviewed(marking: string): ConfigurationOutcome {
  return {
    kind: 'reviewed',
    review: {
      based_on_revision: 1,
      snapshot_schema: 'pp-design/2',
      written_schema: 'pp-design/3',
      saveable: true,
      snapshot_sha256: 'x',
      findings: [],
      model: model(marking),
    } as unknown as ConfigurationReview,
  };
}

let latest: Record<string, unknown> = INITIAL;

function Harness({
  validateImpl,
  initial = INITIAL,
  externalRefusal = null,
  jump = 0,
}: {
  validateImpl: (options: {
    configuration: Record<string, unknown>;
  }) => Promise<ConfigurationOutcome>;
  initial?: Record<string, unknown>;
  externalRefusal?: Refusal | null;
  jump?: number;
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
      externalRefusal={externalRefusal}
      jumpToConflict={jump}
      boardChoices={BOARDS}
      onUnauthorized={vi.fn()}
      validateImpl={validateImpl as never}
    />
  );
}

function type(element: HTMLElement, text: string) {
  fireEvent.change(element, { target: { value: text } });
}

function build(validateImpl = vi.fn(() => Promise.resolve(reviewed('7')))) {
  renderApp(<Harness validateImpl={validateImpl} />, { locale: 'en', theme: 'light' });
  const editor = screen.getByTestId('bindings-editor');
  type(within(editor).getByTestId('add-binding-id'), 'bind-1');
  fireEvent.change(within(editor).getByTestId('add-binding-board'), { target: { value: 'MB-1' } });
  fireEvent.click(within(editor).getByTestId('add-binding-submit'));
  const row = within(within(editor).getByTestId('binding-entry'));
  const slots = row.getAllByTestId('binding-slot-component');
  fireEvent.change(at(slots, 0), { target: { value: 'comp-ka' } });
  fireEvent.change(at(slots, 1), { target: { value: 'comp-kb' } });
  fireEvent.change(row.getByTestId('binding-role-strip'), { target: { value: 'strip-xp' } });
  type(row.getByTestId('point-marking'), '7');
  return { editor, row, validateImpl };
}

describe('M9b1 guided binding', () => {
  it('builds a binding through the controls, with nothing inferred', () => {
    const { row } = build();
    expect((latest.bindings as unknown[])[0]).toEqual({
      id: 'bind-1',
      pattern: REF,
      board: 'MB-1',
      circuit: null,
      slots: { src: { component: 'comp-ka' }, dst: { component: 'comp-kb' } },
      strips: { out: 'strip-xp' },
      markings: { p1: '7' },
      cable: null,
      wires: {},
    });
    expect(row.queryByTestId('binding-problems')).toBeNull();
    // Only compatible components are offered per slot.
    const options = (select: HTMLElement) =>
      Array.from((select as HTMLSelectElement).options).map((o) => o.textContent);
    expect(options(at(row.getAllByTestId('binding-slot-component'), 0))).toEqual([
      B.notChosen,
      'KA',
    ]);
    expect(options(at(row.getAllByTestId('binding-slot-component'), 1))).toEqual([
      B.notChosen,
      'KB',
    ]);
  });

  it('previews only this binding, labelled unsaved and review only, saving nothing', async () => {
    const { row, validateImpl } = build();
    expect(row.getByTestId('binding-preview-label').textContent).toBe(B.previewLabel);
    const before = JSON.stringify(latest);
    await act(async () => {
      fireEvent.click(row.getByTestId('binding-preview-run'));
      await Promise.resolve();
    });
    expect(validateImpl).toHaveBeenCalledTimes(1);
    expect(row.getByTestId('binding-preview-nets').textContent).toBe('S1: KA:A1, XP:7 (inner)');
    expect(row.getByTestId('binding-preview-wires').textContent).toBe('KA:A1 to XP:7 (inner) (S1)');
    expect(row.getByTestId('binding-preview-connections').textContent).toBe(
      'KA:A1 to XP:7 (inner), wire',
    );
    expect(row.getByTestId('binding-preview-points').textContent).toBe(
      'XP: 7 (position 1, level 1)',
    );
    // The preview did not change the configuration.
    expect(JSON.stringify(latest)).toBe(before);
  });

  it('marks the preview outdated after an edit, and never lets an older answer replace a newer one', async () => {
    let resolveFirst: (value: ConfigurationOutcome) => void = () => undefined;
    const validateImpl = vi
      .fn<() => Promise<ConfigurationOutcome>>()
      .mockImplementationOnce(() => new Promise((resolve) => (resolveFirst = resolve)))
      .mockImplementationOnce(() => Promise.resolve(reviewed('8')));
    const { row } = build(validateImpl as never);
    fireEvent.click(row.getByTestId('binding-preview-run')); // first, slow
    type(row.getByTestId('point-marking'), '8');
    // A second preview for the newer edit (the button is enabled again once loading is replaced).
    await act(async () => {
      resolveFirst(reviewed('7')); // the old answer arrives first...
      await Promise.resolve();
    });
    await act(async () => {
      fireEvent.click(row.getByTestId('binding-preview-run'));
      await Promise.resolve();
    });
    expect(row.getByTestId('binding-preview-points').textContent).toBe(
      'XP: 8 (position 1, level 1)',
    );
    expect(row.queryByTestId('binding-preview-outdated')).toBeNull();
    type(row.getByTestId('point-marking'), '9');
    expect(row.getByTestId('binding-preview-outdated').textContent).toBe(B.previewOutdated);
  });

  it('drops a late answer to a superseded request', async () => {
    const pending: ((value: ConfigurationOutcome) => void)[] = [];
    const validateImpl = vi.fn(
      () => new Promise<ConfigurationOutcome>((resolve) => pending.push(resolve)),
    );
    const { row } = build(validateImpl as never);
    fireEvent.click(row.getByTestId('binding-preview-run'));
    // A new request while the first is pending (as after the first resolves; simulated directly).
    await act(async () => {
      at(pending, 0)(reviewed('7'));
      await Promise.resolve();
    });
    type(row.getByTestId('point-marking'), '8');
    fireEvent.click(row.getByTestId('binding-preview-run'));
    expect(pending).toHaveLength(2);
    await act(async () => {
      at(pending, 1)(reviewed('8'));
      await Promise.resolve();
    });
    // Resolving the first again (a duplicate late answer) changes nothing.
    await act(async () => {
      at(pending, 0)(reviewed('7'));
      await Promise.resolve();
    });
    expect(row.getByTestId('binding-preview-points').textContent).toBe(
      'XP: 8 (position 1, level 1)',
    );
  });

  it('says reference problems beside each field and keeps a removed selection visible', () => {
    const { row } = build();
    fireEvent.change(row.getByTestId('binding-circuit'), { target: { value: 'c1' } });
    type(row.getByTestId('point-marking'), '');
    expect(row.getByTestId('binding-problems').textContent).toMatch(/1 problem/);
    const marking = row.getByTestId('point-marking');
    expect(
      document.getElementById(marking.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toBe(B.problem.missing);
    // A component a binding uses cannot be removed; the binding is named.
    expect(screen.getAllByTestId('component-bound').map((note) => note.textContent)).toEqual([
      B.entryBound.replace('{bindings}', 'bind-1'),
      B.entryBound.replace('{bindings}', 'bind-1'),
    ]);
    expect(
      screen
        .getAllByTestId<HTMLButtonElement>('component-remove')
        .every((button) => button.disabled),
    ).toBe(true);
  });

  it('shows a stale component as gone (never cleared) after an import removes it', () => {
    const stale = {
      ...INITIAL,
      components: [component('comp-kb', 'MB-1', DST, 'KB')],
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
          wires: {},
        },
      ],
    };
    renderApp(<Harness validateImpl={vi.fn()} initial={stale} />, { locale: 'en', theme: 'light' });
    const select = at(screen.getAllByTestId<HTMLSelectElement>('binding-slot-component'), 0);
    expect(select.value).toBe('comp-ka');
    expect(select.selectedOptions[0]?.textContent).toBe(B.goneOption.replace('{id}', 'comp-ka'));
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect((latest.bindings as { slots: unknown }[])[0]?.slots).toEqual({
      src: { component: 'comp-ka' },
    });
  });

  it('marks a refused binding, says the reason, and focuses it when the overview asks', async () => {
    const stored = {
      ...INITIAL,
      bindings: [
        {
          id: 'bind-1',
          pattern: REF,
          board: 'MB-1',
          circuit: null,
          slots: { src: { component: 'comp-ka' }, dst: { component: 'comp-kb' } },
          strips: { out: 'strip-xp' },
          markings: { p1: '7' },
          cable: null,
          wires: {},
        },
      ],
    };
    const refusal: Refusal = {
      kind: 'refused',
      detail: 'the configuration cannot be applied',
      code: 'configuration_refused',
      params: { refusal: 'board_mismatch', binding: 'bind-1', strip: 'strip-xp' },
    };
    const view = renderApp(
      <Harness validateImpl={vi.fn()} initial={stored} externalRefusal={refusal} />,
      {
        locale: 'en',
        theme: 'light',
      },
    );
    const entry = screen.getByTestId('binding-entry');
    expect(entry.getAttribute('data-conflict')).toBe('true');
    expect(within(entry).getByTestId('binding-refused').textContent).toContain('board_mismatch');
    view.rerender(
      <Harness validateImpl={vi.fn()} initial={stored} externalRefusal={refusal} jump={1} />,
    );
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(document.activeElement).toBe(entry);
  });

  it('announces an added and removed binding and returns focus to the add field', async () => {
    const { editor } = build();
    const status = within(editor).getByTestId('bindings-status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe(B.added.replace('{id}', 'bind-1'));
    fireEvent.click(within(editor).getByTestId('binding-remove'));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(status.textContent).toBe(B.removed.replace('{id}', 'bind-1'));
    expect(document.activeElement).toBe(within(editor).getByTestId('add-binding-id'));
  });

  it('shows the id help with its tenant/<id>/v1 placeholder literally (no message error)', () => {
    // Regression: "<id>" was read as an unclosed message tag, so the help did not render.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    renderApp(<Harness validateImpl={vi.fn()} />, { locale: 'en', theme: 'light' });
    const helps = screen.getAllByText(/stored as tenant\/<id>\/v1/);
    expect(helps.length).toBeGreaterThanOrEqual(2); // definitions and patterns
    expect(errors.mock.calls.some((call) => String(call[0]).includes('INVALID_MESSAGE'))).toBe(
      false,
    );
    errors.mockRestore();
  });

  it('has the same binding messages in Arabic and Hebrew', () => {
    const keys = (o: object): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        typeof v === 'object' && v !== null ? keys(v as object).map((x) => `${k}.${x}`) : [k],
      );
    expect(keys(ar.configuration.bindings).sort()).toEqual(keys(B).sort());
    expect(keys(he.configuration.bindings).sort()).toEqual(keys(B).sort());
  });
});
