import { describe, expect, it } from 'vitest';

import { bindingProblems, type BoardChoices } from '@/components/design/binding-model';
import { bindingsOf, type Configuration } from '@/components/design/configuration-model';
import {
  addConnection,
  addMember,
  connectionProblem,
  patternProblems,
  patternsOf,
  removeConnection,
  removeMember,
  removeNet,
  updateConnection,
  wireDetailUses,
} from '@/components/design/pattern-model';

/** M9b2: connections edited in the guided editor; wire details stay attached (SYNTHETIC data). */

const REF = 'tenant/synth-conn/v1';
const SRC = 'tenant/synth-src/v1';

type Raw = Record<string, unknown>;

function base(wires: Raw = {}): Configuration {
  return {
    templates: [],
    patterns: [
      {
        ref: REF,
        slots: [{ id: 'a', template: SRC, device_kind: null }],
        strips: ['out'],
        points: [
          { id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' },
          { id: 'p2', strip: 'out', offset: 2, level: 1, kind: 'unspecified' },
        ],
        nets: [
          {
            role: 'n1',
            signal: 'S1',
            scope: 'binding',
            members: [
              { slot: 'a', terminal: 't1' },
              { point: 'p1', side: 'inner' },
              { point: 'p1', side: 'outer' },
            ],
            connections: [
              { from: 0, to: 1, via: 'wire', core: null, label: null },
              { from: 1, to: 2, via: 'internal', core: null, label: null },
            ],
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
    strips: [],
    components: [],
    bindings: [
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

function net(doc: Configuration) {
  const found = patternsOf(doc)[0]?.nets[0];
  if (found === undefined) throw new Error('no net');
  return found;
}

function rawConnections(doc: Configuration): Raw[] {
  const pattern = (doc.patterns as Raw[])[0] as { nets: { connections: Raw[] }[] };
  return pattern.nets[0]?.connections ?? [];
}

const W0 = { 'n1/0': { number: { value: 'W7', source: 'user' } } };

describe('adding connections', () => {
  it('appends in the composer shape, core and label only on a cable core', () => {
    let doc = addConnection(base(), REF, 'n1', {
      from: 0,
      to: 2,
      via: 'jumper',
      core: 3,
      label: 'x',
    });
    // A core or label typed for a cable core is not written once the kind is another.
    expect(rawConnections(doc)[2]).toEqual({
      from: 0,
      to: 2,
      via: 'jumper',
      core: null,
      label: null,
    });
    doc = addConnection(base(), REF, 'n1', {
      from: 0,
      to: 2,
      via: 'jumper',
      core: null,
      label: null,
    });
    expect(rawConnections(doc)[2]).toEqual({
      from: 0,
      to: 2,
      via: 'jumper',
      core: null,
      label: null,
    });
    doc = addConnection(doc, REF, 'n1', {
      from: 0,
      to: 2,
      via: 'cable_core',
      core: 2,
      label: ' BN ',
    });
    expect(rawConnections(doc)[3]).toEqual({
      from: 0,
      to: 2,
      via: 'cable_core',
      core: 2,
      label: 'BN',
    });
  });

  it('refuses ends outside the net, the same end twice, and point sides of two points', () => {
    const doc = base();
    expect(
      addConnection(doc, REF, 'n1', { from: 0, to: 0, via: 'wire', core: null, label: null }),
    ).toBe(doc);
    expect(
      addConnection(doc, REF, 'n1', { from: 0, to: 9, via: 'wire', core: null, label: null }),
    ).toBe(doc);
    expect(
      addConnection(doc, REF, 'n1', { from: 0, to: 1, via: 'internal', core: null, label: null }),
    ).toBe(doc);
    expect(
      addConnection(doc, REF, 'n1', { from: 0, to: 1, via: 'cable_core', core: null, label: null }),
    ).toBe(doc);
    expect(
      addConnection(doc, REF, 'n1', { from: 0, to: 1, via: 'cable_core', core: 0, label: null }),
    ).toBe(doc);
  });

  it('says each problem the pattern check would refuse', () => {
    const n = net(base());
    const c = (
      from: number,
      to: number,
      via: string,
      core: number | null = null,
      label: string | null = null,
    ) => connectionProblem(n, { from, to, via, core, label });
    expect(c(0, 1, 'wire')).toBeNull();
    expect(c(1, 2, 'internal')).toBeNull();
    expect(c(0, 3, 'wire')).toBe('end_missing');
    expect(c(1, 1, 'wire')).toBe('same_end');
    expect(c(0, 1, 'cable')).toBe('unknown_via');
    expect(c(0, 1, 'internal')).toBe('internal_not_sides');
    expect(c(0, 1, 'cable_core')).toBe('core_missing');
    expect(c(0, 1, 'cable_core', 0)).toBe('core_invalid');
    expect(c(0, 1, 'wire', 2)).toBe('core_not_allowed');
    expect(c(0, 1, 'busbar', null, 'L')).toBe('label_not_allowed');
  });

  it('marks a stored connection the check would refuse, beside it', () => {
    const doc = updateConnection(base(), REF, 'n1', 0, {
      from: 0,
      to: 1,
      via: 'cable_core',
      core: null,
      label: null,
    });
    const pattern = patternsOf(doc)[0];
    if (pattern === undefined) throw new Error('no pattern');
    expect(patternProblems(doc, pattern, new Set([SRC])).connections).toEqual({
      'n1#0': 'core_missing',
    });
  });
});

describe('wire details stay attached to their connection, or the change is refused', () => {
  it('finds the details each binding saved, by position', () => {
    expect(wireDetailUses(base(W0), REF, 'n1')).toEqual([
      { binding: 'bind-1', key: 'n1/0', index: 0 },
    ]);
    expect(wireDetailUses(base(W0), REF, 'n1', 1)).toEqual([]);
  });

  it('refuses to remove a connection at or before one with details, and names nothing silently', () => {
    const doc = base(W0);
    expect(removeConnection(doc, REF, 'n1', 0)).toBe(doc);
    // After it: the details at n1/0 keep their connection.
    const removed = removeConnection(doc, REF, 'n1', 1);
    expect(rawConnections(removed)).toHaveLength(1);
    expect(bindingsOf(removed)[0]?.wires).toEqual(W0);
    // Details on the later connection block removing an earlier one (they would shift).
    const later = base({ 'n1/1': { number: 'W9' } });
    expect(removeConnection(later, REF, 'n1', 0)).toBe(later);
    expect(removeConnection(later, REF, 'n1', 1)).toBe(later);
  });

  it('refuses to edit a connection with details; others edit freely', () => {
    const doc = base(W0);
    expect(
      updateConnection(doc, REF, 'n1', 0, { from: 0, to: 2, via: 'wire', core: null, label: null }),
    ).toBe(doc);
    expect(
      updateConnection(doc, REF, 'n1', 0, {
        from: 0,
        to: 1,
        via: 'jumper',
        core: null,
        label: null,
      }),
    ).toBe(doc);
    const edited = updateConnection(doc, REF, 'n1', 1, {
      from: 2,
      to: 1,
      via: 'internal',
      core: null,
      label: null,
    });
    expect(rawConnections(edited)[1]).toEqual({
      from: 2,
      to: 1,
      via: 'internal',
      core: null,
      label: null,
    });
    expect(bindingsOf(edited)[0]?.wires).toEqual(W0);
  });

  it('refuses to remove a net with details, and a key that is no plain index counts too', () => {
    expect(removeNet(base(W0), REF, 'n1')).toEqual(base(W0));
    const odd = base({ 'n1/x': { number: 'W1' } });
    expect(removeNet(odd, REF, 'n1')).toBe(odd);
    expect(removeConnection(odd, REF, 'n1', 1)).toBe(odd);
    expect(patternsOf(removeNet(base(), REF, 'n1'))[0]?.nets).toEqual([]);
  });

  it('adding a member keeps both ends of every connection and its details', () => {
    const doc = addMember(base(W0), REF, 'n1', { point: 'p2', side: 'inner' });
    expect(net(doc).members).toHaveLength(4);
    expect(rawConnections(doc).map((c) => [c.from, c.to])).toEqual([
      [0, 1],
      [1, 2],
    ]);
    expect(bindingsOf(doc)[0]?.wires).toEqual(W0);
  });

  it('removing a member re-indexes ends, and is refused while a connection names it', () => {
    let doc = addMember(base(W0), REF, 'n1', { point: 'p2', side: 'inner' });
    doc = addConnection(doc, REF, 'n1', { from: 3, to: 0, via: 'wire', core: null, label: null });
    expect(removeMember(doc, REF, 'n1', 1)).toBe(doc);
    // Remove the connection to p2 (no details after it), then p2's member, then insert a new one.
    doc = removeConnection(doc, REF, 'n1', 2);
    doc = removeMember(doc, REF, 'n1', 3);
    expect(rawConnections(doc).map((c) => [c.from, c.to])).toEqual([
      [0, 1],
      [1, 2],
    ]);
    expect(bindingsOf(doc)[0]?.wires).toEqual(W0);
  });

  it('keeps stored fields of a connection it edits', () => {
    const doc = updateConnection(base(), REF, 'n1', 0, {
      from: 1,
      to: 0,
      via: 'wire',
      core: null,
      label: null,
    });
    expect(rawConnections(doc)[0]).toEqual({
      from: 1,
      to: 0,
      via: 'wire',
      core: null,
      label: null,
    });
  });
});

describe('independent review regressions', () => {
  it('another role key or a non-index key: not misread as this net index', () => {
    const doc = base({ 'n1/b/0': { number: 'W1' }, 'n1/01': { number: 'W2' } });
    // `n1/b/0` belongs to role `n1/b`; `n1/01` names no connection and is kept as a use.
    expect(wireDetailUses(doc, REF, 'n1')).toEqual([
      { binding: 'bind-1', key: 'n1/01', index: Number.POSITIVE_INFINITY },
    ]);
    expect(wireDetailUses(base({ 'n1/b/0': {} }), REF, 'n1')).toEqual([]);
  });

  it('an in-place label keeps a space between words while typed', () => {
    let doc = addConnection(base(), REF, 'n1', {
      from: 0,
      to: 2,
      via: 'cable_core',
      core: 1,
      label: null,
    });
    doc = updateConnection(doc, REF, 'n1', 2, {
      from: 0,
      to: 2,
      via: 'cable_core',
      core: 1,
      label: 'L1 ',
    });
    expect(rawConnections(doc)[2]?.label).toBe('L1 ');
    doc = updateConnection(doc, REF, 'n1', 2, {
      from: 0,
      to: 2,
      via: 'cable_core',
      core: 1,
      label: 'L1 A',
    });
    expect(rawConnections(doc)[2]?.label).toBe('L1 A');
  });
});

describe('cable cores, checked as the composer checks them', () => {
  const BOARDS: BoardChoices[] = [
    { id: 'MB-1', circuits: [], cables: [{ id: 'cab-1', label: 'W1', cores: 3 }] },
  ];
  function cabled(cores: number[]): Configuration {
    let doc = base();
    for (const core of cores) {
      doc = addConnection(doc, REF, 'n1', { from: 0, to: 1, via: 'cable_core', core, label: null });
    }
    const binding = { ...(doc.bindings as Raw[])[0], cable: 'cab-1' };
    return { ...doc, bindings: [binding] };
  }
  const coreProblems = (doc: Configuration) => {
    const binding = bindingsOf(doc)[0];
    if (binding === undefined) throw new Error('no binding');
    return bindingProblems(doc, binding, BOARDS).filter((p) => p.field === 'core');
  };

  it('a core the cable does not have', () => {
    expect(coreProblems(cabled([1, 3]))).toEqual([]);
    expect(coreProblems(cabled([4]))).toEqual([
      { field: 'core', key: 'n1/2', code: 'core_out_of_range', core: 4 },
    ]);
  });

  it('a core used twice, in this binding or another on the same cable', () => {
    expect(coreProblems(cabled([2, 2]))).toEqual([
      { field: 'core', key: 'n1/3', code: 'core_reused', core: 2 },
    ]);
    const doc = cabled([1]);
    const second = { ...(doc.bindings as Raw[])[0], id: 'bind-2' };
    const two = { ...doc, bindings: [...(doc.bindings as Raw[]), second] };
    expect(coreProblems(two)).toEqual([
      { field: 'core', key: 'n1/2', code: 'core_reused', core: 1 },
    ]);
  });

  it('cores are not checked against an unknown cable size', () => {
    const doc = cabled([9]);
    const binding = bindingsOf(doc)[0];
    if (binding === undefined) throw new Error('no binding');
    const unknown: BoardChoices[] = [
      { id: 'MB-1', circuits: [], cables: [{ id: 'cab-1', label: 'W1' }] },
    ];
    expect(bindingProblems(doc, binding, unknown).filter((p) => p.field === 'core')).toEqual([]);
  });
});
