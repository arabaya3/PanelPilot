import { describe, expect, it } from 'vitest';

import {
  addBinding,
  bindablePatterns,
  bindingIdProblem,
  bindingProblems,
  bindingRowIds,
  bindingUsesOf,
  componentChoices,
  removeBinding,
  removeExtra,
  setRole,
  setSlot,
  stripChoices,
  updateBinding,
  type BoardChoices,
} from '@/components/design/binding-model';
import {
  bindingsOf,
  setMarking,
  type ConfigBinding,
  type Configuration,
} from '@/components/design/configuration-model';

/** M9b1: bindings created in the guided editor (SYNTHETIC data). */

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';
const REF = 'tenant/synth-two-slot/v1';

function component(id: string, board: string, template: string, product: string) {
  return { id, board, template, designation: { function: null, location: null, product } };
}

function base(): Configuration {
  return {
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
          {
            role: 'nb',
            signal: 'N',
            scope: 'board',
            members: [{ slot: 'dst', terminal: 't-001' }],
            connections: [],
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
      {
        id: 'strip-xs',
        board: 'SB-1',
        template: null,
        designation: { function: null, location: null, product: 'XS' },
      },
    ],
    components: [
      component('comp-ka', 'MB-1', SRC, 'KA'),
      component('comp-kb', 'MB-1', DST, 'KB'),
      component('comp-kc', 'SB-1', SRC, 'KC'),
    ],
    bindings: [],
  };
}

const BOARDS: BoardChoices[] = [
  {
    id: 'MB-1',
    circuits: [{ id: 'c1', label: 'c1 · Lighting' }],
    cables: [{ id: 'cab-1', label: 'W1 (cab-1)' }],
  },
  { id: 'SB-1', circuits: [], cables: [] },
];

function only(doc: Configuration): ConfigBinding {
  const found = bindingsOf(doc)[0];
  if (found === undefined) throw new Error('no binding');
  return found;
}

function bound(): Configuration {
  let doc = addBinding(base(), { id: 'bind-1', pattern: REF, board: 'MB-1', circuit: null });
  doc = setSlot(doc, 'bind-1', 'src', 'comp-ka');
  doc = setSlot(doc, 'bind-1', 'dst', 'comp-kb');
  doc = setRole(doc, 'bind-1', 'out', 'strip-xp');
  doc = setMarking(doc, 'bind-1', 'p1', '7');
  return doc;
}

describe('building a binding without JSON', () => {
  it('writes the composer shape and nothing more', () => {
    expect((bound().bindings as unknown[])[0]).toEqual({
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
    expect(bindingProblems(bound(), only(bound()), BOARDS)).toEqual([]);
  });

  it('offers only compatible components and strips on the binding board', () => {
    expect(componentChoices(base(), 'MB-1', SRC).map((c) => c.label)).toEqual(['KA']);
    expect(componentChoices(base(), 'MB-1', null).map((c) => c.label)).toEqual(['KA', 'KB']);
    expect(stripChoices(base(), 'MB-1').map((s) => s.label)).toEqual(['XP']);
  });

  it('refuses a taken or malformed name, and keeps unedited keys as stored', () => {
    const doc = bound();
    expect(bindingIdProblem(doc, 'bind-1')).toBe('taken');
    expect(bindingIdProblem(doc, 'a b')).toBe('invalid');
    expect(addBinding(doc, { id: 'bind-1', pattern: REF, board: 'MB-1', circuit: null })).toBe(doc);
    const withWires = {
      ...doc,
      bindings: [
        {
          ...only(doc),
          wires: { 'n1/0': { number: { value: 'W7', source: 'given' } } },
          slots: { ...(only(doc).slots as object), x: { device: 'd1' } },
        },
      ],
    };
    const changed = setSlot(withWires, 'bind-1', 'src', null);
    expect(only(changed).wires).toEqual({ 'n1/0': { number: { value: 'W7', source: 'given' } } });
    expect(only(changed).slots).toEqual({ dst: { component: 'comp-kb' }, x: { device: 'd1' } });
  });
});

describe('what a save would be refused for, said before saving', () => {
  it('each reference problem beside its field', () => {
    let doc = addBinding(base(), { id: 'bind-1', pattern: REF, board: 'MB-1', circuit: 'c9' });
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'circuit', code: 'no_circuit' },
      { field: 'slot', key: 'src', code: 'not_chosen' },
      { field: 'slot', key: 'dst', code: 'not_chosen' },
      { field: 'role', key: 'out', code: 'not_chosen' },
      { field: 'marking', key: 'p1', code: 'missing' },
    ]);
    doc = updateBinding(bound(), 'bind-1', { circuit: null });
    doc = setSlot(doc, 'bind-1', 'src', 'comp-kc'); // on SB-1
    doc = setSlot(doc, 'bind-1', 'dst', 'comp-ka'); // definition is SRC, slot wants DST
    doc = setRole(doc, 'bind-1', 'out', 'strip-xs'); // on SB-1
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'slot', key: 'src', code: 'other_board' },
      { field: 'slot', key: 'dst', code: 'other_template' },
      { field: 'role', key: 'out', code: 'other_board' },
    ]);
  });

  it('a removed component or strip stays selected and is said gone, never cleared', () => {
    const doc = {
      ...bound(),
      components: (bound().components as { id: string }[]).filter((c) => c.id !== 'comp-ka'),
      strips: [],
    };
    expect(only(doc).slots).toEqual({
      src: { component: 'comp-ka' },
      dst: { component: 'comp-kb' },
    });
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'slot', key: 'src', code: 'gone' },
      { field: 'role', key: 'out', code: 'gone' },
    ]);
  });

  it('a pattern no longer in the configuration is said', () => {
    const doc = { ...bound(), patterns: [] };
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'pattern', code: 'no_pattern' },
    ]);
  });

  it('a cable is asked for only where a connection uses a cable core', () => {
    expect(bindablePatterns(bound())[0]?.needsCable).toBe(false);
    const raw = structuredClone(bound()) as {
      patterns: { nets: { connections: { via: string }[] }[] }[];
    };
    const first = raw.patterns[0]?.nets[0]?.connections[0];
    if (first) first.via = 'cable_core';
    const doc = raw as unknown as Configuration;
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'cable', code: 'cable_needed' },
    ]);
    const chosen = updateBinding(doc, 'bind-1', { cable: 'cab-9' });
    expect(bindingProblems(chosen, only(chosen), BOARDS)).toEqual([
      { field: 'cable', code: 'no_cable' },
    ]);
  });
});

describe('references', () => {
  it('names the bindings that use a component, strip or pattern', () => {
    const doc = bound();
    expect(bindingUsesOf(doc, 'component', 'comp-ka')).toEqual(['bind-1']);
    expect(bindingUsesOf(doc, 'component', 'comp-kc')).toEqual([]);
    expect(bindingUsesOf(doc, 'strip', 'strip-xp')).toEqual(['bind-1']);
    expect(bindingUsesOf(doc, 'pattern', REF)).toEqual(['bind-1']);
    expect(bindingsOf(removeBinding(doc, 'bind-1'))).toEqual([]);
  });
});

describe('the preview picks this binding rows by the ids the server derives', () => {
  it('matches compose.py ids (checked against app.design.model.ids.derive)', () => {
    const ids = bindingRowIds(bound(), only(bound()));
    expect([...ids.connections]).toEqual(['x-68a8425e238b1342873a4233']);
    expect([...ids.wires]).toEqual(['w-2d28959012cb5b4ba8297f9e']);
    expect([...ids.points]).toEqual(['p-5b8e47a76d272eaee692caaf']);
    expect([...ids.nets].sort()).toEqual([
      'n-5e5e6bb5cd4ce9397522aa96',
      'n-a55eae8a8ed8e0240fc78b53',
    ]);
  });
});

describe('keys the pattern no longer has (independent review)', () => {
  it('are said, and removed only when the user asks', () => {
    const doc = {
      ...bound(),
      bindings: [
        {
          ...only(bound()),
          slots: { ...(only(bound()).slots as object), gone: { component: 'comp-kb' } },
          strips: { out: 'strip-xp', old: 'strip-xp' },
          markings: { p1: '7', p9: '9' },
        },
      ],
    };
    expect(bindingProblems(doc, only(doc), BOARDS)).toEqual([
      { field: 'extra', key: 'gone', code: 'extra_slot' },
      { field: 'extra', key: 'old', code: 'extra_role' },
      { field: 'extra', key: 'p9', code: 'extra_marking' },
    ]);
    let fixed = removeExtra(doc, 'bind-1', 'extra_slot', 'gone');
    fixed = removeExtra(fixed, 'bind-1', 'extra_role', 'old');
    fixed = removeExtra(fixed, 'bind-1', 'extra_marking', 'p9');
    expect(bindingProblems(fixed, only(fixed), BOARDS)).toEqual([]);
    expect(only(fixed).markings).toEqual({ p1: '7' });
  });

  it('marks a board-scope net as shared with other bindings', () => {
    const ids = bindingRowIds(bound(), only(bound()));
    expect([...ids.sharedNets]).toEqual(['n-5e5e6bb5cd4ce9397522aa96']);
  });
});
