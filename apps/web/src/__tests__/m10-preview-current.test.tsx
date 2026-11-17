import { act, fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type {
  ProjectDesignRequest,
  ConfigurationOutcome,
  ConfigurationReview,
  V2ModelSummary,
} from '@/lib/design';
import en from '@/messages/en.json';

import { parseConfiguration, type ConfirmedReference } from '@/components/design/draft';

import { renderApp } from './helpers';

/**
 * M10: the preview of the current load schedule with the configuration; choices
 * from the server's design of the current inputs; moved references said beside
 * their binding and kept only by an explicit action. SYNTHETIC data; mocked API
 * (the real composition and save are checked by the API tests and the browser run).
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

const REQUEST = { boards: [{ name: 'MB-1', loads: [] }] } as unknown as ProjectDesignRequest;
const OTHER = { boards: [{ name: 'MB-1', loads: [{}] }] } as unknown as ProjectDesignRequest;

type Validate = (options: {
  configuration: Record<string, unknown>;
  request?: ProjectDesignRequest | null;
  referencesConfirmed?: ConfirmedReference[];
}) => Promise<ConfigurationOutcome>;

let latestDoc: Record<string, unknown> = BOUND;

function Panel({
  validateImpl,
  request = REQUEST,
  confirmed = [],
  onConfirm = vi.fn(),
  choices = BOARDS,
}: {
  validateImpl: Validate;
  request?: ProjectDesignRequest | null;
  confirmed?: ConfirmedReference[];
  onConfirm?: (entry: ConfirmedReference) => void;
  choices?: BoardChoices[] | null;
}) {
  const [value, setValue] = useState<Record<string, unknown>>(BOUND);
  latestDoc = value;
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
      currentRequest={request}
      confirmed={confirmed}
      onConfirm={onConfirm}
      onUnauthorized={vi.fn()}
      validateImpl={validateImpl as never}
    />
  );
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function moved(): ConfigurationOutcome {
  return {
    kind: 'refused',
    detail: 'the configuration cannot be applied',
    code: 'configuration_refused',
    params: {
      refusal: 'reference_moved',
      binding: 'bind-1',
      field: 'circuit',
      key: '',
      ref: 'c1',
      before: 'Lighting',
      after: 'Heater',
      moved: 'bind-1|circuit||c1',
    },
  };
}

describe('M10 preview of the current inputs', () => {
  it('sends the current load schedule with the configuration and says so', async () => {
    const validate = vi.fn<Validate>(() => Promise.resolve(reviewed('7')));
    renderApp(<Panel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    expect(screen.getByTestId('binding-preview-label').textContent).toBe(B.previewLabelCurrent);
    fireEvent.click(screen.getByTestId('binding-preview-run'));
    await act(async () => {
      await tick();
    });
    const sent = at(validate.mock.calls, 0)[0];
    expect(sent.request).toBe(REQUEST);
    expect(Array.isArray(sent.configuration.bindings)).toBe(true);
  });

  it('a change to the load schedule makes the preview outdated; an older answer never wins', async () => {
    let release: (value: ConfigurationOutcome) => void = () => undefined;
    const slow = new Promise<ConfigurationOutcome>((resolve) => {
      release = resolve;
    });
    const validate = vi
      .fn<Validate>()
      .mockImplementationOnce(() => slow)
      .mockImplementation(() => Promise.resolve(reviewed('8')));
    const view = renderApp(<Panel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('binding-preview-run'));
    // The load schedule changes while the preview is on its way.
    view.rerender(<Panel validateImpl={validate} request={OTHER} />);
    release(reviewed('7'));
    await act(async () => {
      await tick();
    });
    const pv = within(screen.getByTestId('binding-preview'));
    // The answer is for the older inputs: shown, but never as current.
    expect(pv.getByTestId('binding-preview-points').textContent).toContain('7');
    expect(pv.getByTestId('binding-preview-outdated')).toBeTruthy();
    fireEvent.click(pv.getByTestId('binding-preview-run'));
    await act(async () => {
      await tick();
    });
    expect(at(validate.mock.calls, 1)[0].request).toBe(OTHER);
    expect(pv.getByTestId('binding-preview-points').textContent).toContain('8');
    expect(pv.queryByTestId('binding-preview-outdated')).toBeNull();
    view.rerender(<Panel validateImpl={validate} request={REQUEST} />);
    expect(pv.getByTestId('binding-preview-outdated')).toBeTruthy();
  });

  it('a moved reference is said beside its binding, linked to its field, kept only when asked', async () => {
    const onConfirm = vi.fn();
    const validate = vi.fn<Validate>(() => Promise.resolve(moved()));
    renderApp(<Panel validateImpl={validate} onConfirm={onConfirm} />, {
      locale: 'en',
      theme: 'light',
    });
    fireEvent.click(screen.getByTestId('binding-preview-run'));
    await act(async () => {
      await tick();
    });
    const box = within(screen.getByTestId('binding-moved'));
    expect(box.getByTestId('binding-moved-link').textContent).toBe(
      en.configuration.moved.named
        .replace('{field}', 'Circuit')
        .replaceAll('{id}', 'c1')
        .replace('{after}', 'Heater')
        .replace('{before}', 'Lighting'),
    );
    fireEvent.click(box.getByTestId('binding-moved-link'));
    expect(document.activeElement).toBe(screen.getByTestId('binding-circuit'));
    // Nothing changed for the user: the circuit and the wire details are as they were.
    expect((latestDoc.bindings as { circuit: string; wires: unknown }[])[0]).toMatchObject({
      circuit: 'c1',
      wires: { 'n1/0': { number: { value: 'W7', source: 'user' } } },
    });
    fireEvent.click(box.getByTestId('binding-moved-keep'));
    expect(onConfirm).toHaveBeenCalledWith({
      binding: 'bind-1',
      field: 'circuit',
      key: null,
      id: 'c1',
    });
    await act(async () => {
      await tick();
    });
    expect(screen.getByTestId('bindings-status').textContent).toContain('c1');
  });

  it('without current choices, refreshes them from the server design of the current inputs', async () => {
    const validate = vi.fn<Validate>(() =>
      Promise.resolve({
        kind: 'reviewed',
        review: {
          ...(reviewed('7') as { review: ConfigurationReview }).review,
          from_current_request: true,
          boards: [
            {
              id: 'MB-1',
              circuits: [
                { id: 'c1', description: 'Heater' },
                { id: 'c2', description: 'Lighting' },
              ],
              cables: [],
            },
          ],
        },
      } as ConfigurationOutcome),
    );
    renderApp(<Panel validateImpl={validate} choices={null} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('bindings-refresh-choices'));
    await act(async () => {
      await tick();
    });
    const sent = at(validate.mock.calls, 0)[0];
    expect(sent.request).toBe(REQUEST);
    expect(sent.configuration).toEqual(expect.objectContaining({ bindings: [] }));
    const options = [...screen.getByTestId<HTMLSelectElement>('binding-circuit').options].map(
      (o) => o.textContent,
    );
    expect(options).toContain('c2 · Lighting');
    expect(screen.queryByTestId('bindings-choices-unchecked')).toBeNull();
  });

  it('confirmations travel with the draft through a reload', () => {
    const kept = { binding: 'bind-1', field: 'circuit', key: null, id: 'c1' };
    const parsed = parseConfiguration({ doc: BOUND, dropUnrecoverable: false, confirmed: [kept] });
    expect(parsed?.confirmed).toEqual([kept]);
    expect(parseConfiguration({ doc: BOUND, dropUnrecoverable: false })?.confirmed).toBeUndefined();
    expect(
      parseConfiguration({ doc: BOUND, dropUnrecoverable: false, confirmed: [{ id: 'x' }] })
        ?.confirmed,
    ).toBeUndefined();
  });
});
