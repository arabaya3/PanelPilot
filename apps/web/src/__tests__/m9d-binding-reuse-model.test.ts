import { describe, expect, it } from 'vitest';

import {
  bindingRowIds,
  reuseBinding,
  reuseSummary,
  suggestReuseId,
} from '@/components/design/binding-model';
import { findingsOfBinding, problemTarget } from '@/components/design/binding-overview';
import { bindingsOf, type Configuration } from '@/components/design/configuration-model';
import type { V2Finding } from '@/lib/design';

/** M9d: a binding reused on another board; the overview's attribution (SYNTHETIC data). */

const REF = 'tenant/synth-two-slot/v1';
type Raw = Record<string, unknown>;

function base(): Configuration {
  return {
    templates: [],
    patterns: [
      {
        ref: REF,
        slots: [{ id: 'src', template: null, device_kind: null }],
        strips: ['out'],
        points: [{ id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' }],
        nets: [
          {
            role: 'n1',
            signal: 'S1',
            scope: 'binding',
            members: [
              { slot: 'src', terminal: 't1' },
              { point: 'p1', side: 'inner' },
            ],
            connections: [
              { from: 0, to: 1, via: 'wire', core: null, label: null },
              { from: 0, to: 1, via: 'cable_core', core: 3, label: 'BN' },
            ],
          },
        ],
      },
    ],
    strips: [],
    components: [],
    bindings: [
      {
        id: 'bind-1',
        pattern: REF,
        board: 'MB-1',
        circuit: 'c1',
        slots: { src: { component: 'comp-ka' } },
        strips: { out: 'strip-xp' },
        markings: { p1: '7' },
        cable: 'c1-cable',
        wires: { 'n1/0': { number: { value: 'W7', source: 'user' } } },
      },
    ],
  };
}

const byId = (doc: Configuration, id: string) => bindingsOf(doc).find((b) => b.id === id);

describe('using a binding on another board', () => {
  it('adds a new binding of the same pattern with every board-specific choice unset', () => {
    const doc = reuseBinding(base(), 'bind-1', { id: 'bind-1-SB-1', board: 'SB-1' });
    expect((doc.bindings as Raw[])[1]).toEqual({
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
    // The source and the shared pattern are untouched.
    expect((doc.bindings as Raw[])[0]).toEqual((base().bindings as Raw[])[0]);
    expect(doc.patterns).toEqual(base().patterns);
  });

  it('refuses a taken or malformed id, the source board, or an unknown source', () => {
    const doc = base();
    expect(reuseBinding(doc, 'bind-1', { id: 'bind-1', board: 'SB-1' })).toBe(doc);
    expect(reuseBinding(doc, 'bind-1', { id: 'a b', board: 'SB-1' })).toBe(doc);
    expect(reuseBinding(doc, 'bind-1', { id: 'x', board: 'MB-1' })).toBe(doc);
    expect(reuseBinding(doc, 'bind-1', { id: 'x', board: '' })).toBe(doc);
    expect(reuseBinding(doc, 'nope', { id: 'x', board: 'SB-1' })).toBe(doc);
  });

  it('suggests an id no entry has, numbering past taken ones', () => {
    expect(suggestReuseId(base(), 'bind-1', 'SB-1')).toBe('bind-1-SB-1');
    let doc = reuseBinding(base(), 'bind-1', { id: 'bind-1-SB-1', board: 'SB-1' });
    expect(suggestReuseId(doc, 'bind-1', 'SB-1')).toBe('bind-1-SB-1-2');
    doc = reuseBinding(doc, 'bind-1', { id: 'bind-1-SB-1-2', board: 'SB-1' });
    expect(suggestReuseId(doc, 'bind-1', 'SB-1')).toBe('bind-1-SB-1-3');
    expect(suggestReuseId(base(), 'bind-1', 'board one')).toBe('bind-1-board-one');
  });

  it('says what is shared (pattern cores included) and what must be entered', () => {
    const doc = reuseBinding(base(), 'bind-1', { id: 'b2', board: 'SB-1' });
    const b2 = byId(doc, 'b2');
    if (b2 === undefined) throw new Error('no b2');
    expect(reuseSummary(doc, b2)).toEqual({
      pattern: REF,
      sharedWith: ['bind-1'],
      connections: 2,
      patternCores: [{ role: 'n1', index: 1, core: 3 }],
      toEnter: { slots: ['src'], roles: ['out'], points: ['p1'], cable: true, wires: 1 },
    });
  });
});

describe('the overview', () => {
  it('attributes a finding to the binding whose parts it names, not to its siblings', () => {
    const doc = reuseBinding(base(), 'bind-1', { id: 'b2', board: 'SB-1' });
    const b1 = byId(doc, 'bind-1');
    const b2 = byId(doc, 'b2');
    if (b1 === undefined || b2 === undefined) throw new Error('missing');
    const wire1 = [...bindingRowIds(doc, b1).wires][0] ?? '';
    const finding = (location: Record<string, string>): V2Finding => ({
      code: 'wire_attribute_unset',
      severity: 'warning',
      message: 'synthetic',
      location,
      unresolved: true,
      blocks_approval: false,
      blocks_execution: true,
      requires_engineering_confirmation: false,
    });
    const found = [
      finding({ wire: wire1 }),
      finding({ component: 'comp-ka' }),
      finding({ board_id: 'MB-1' }),
    ];
    expect(findingsOfBinding(doc, b1, found)).toHaveLength(2);
    expect(findingsOfBinding(doc, b2, found)).toHaveLength(0);
  });

  it('links each problem to the field it is said beside', () => {
    expect(problemTarget('r', { field: 'slot', key: 'src', code: 'not_chosen' })).toBe(
      'r-slot-src',
    );
    expect(problemTarget('r', { field: 'role', key: 'out', code: 'not_chosen' })).toBe(
      'r-role-out',
    );
    expect(problemTarget('r', { field: 'marking', key: 'p1', code: 'missing' })).toBe('r-point-p1');
    expect(problemTarget('r', { field: 'cable', code: 'cable_needed' })).toBe('r-cable');
    expect(problemTarget('r', { field: 'circuit', code: 'no_circuit' })).toBe('r-circuit');
  });
});

describe('independent review regressions', () => {
  it('a component two bindings share is attributed to neither', () => {
    let doc = reuseBinding(base(), 'bind-1', { id: 'b2', board: 'MB-1-bis' });
    doc = {
      ...doc,
      bindings: (doc.bindings as Raw[]).map((b) =>
        b.id === 'b2' ? { ...b, slots: { src: { component: 'comp-ka' } } } : b,
      ),
    };
    const b1 = byId(doc, 'bind-1');
    if (b1 === undefined) throw new Error('missing');
    const finding: V2Finding = {
      code: 'synthetic',
      severity: 'warning',
      message: 'synthetic',
      location: { component: 'comp-ka' },
      unresolved: false,
      blocks_approval: false,
      blocks_execution: false,
      requires_engineering_confirmation: false,
    };
    expect(findingsOfBinding(doc, b1, [finding])).toEqual([]);
  });

  it('a board named in another script gives a readable suggestion', () => {
    expect(suggestReuseId(base(), 'bind-1', 'لوحة 2')).toBe('bind-1-2');
    // Nothing readable left: the source id alone is taken, so it is numbered.
    expect(suggestReuseId(base(), 'bind-1', 'לוח')).toBe('bind-1-2');
  });
});
