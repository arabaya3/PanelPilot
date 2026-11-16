import { describe, expect, it } from 'vitest';

import { bindingsOf, type Configuration } from '@/components/design/configuration-model';
import { removeConnection, updateConnection } from '@/components/design/pattern-model';
import {
  removeWireEntry,
  removeWireValue,
  replaceWithUserValue,
  setUserValue,
  strayWireKeys,
  wireDetailsOf,
  wireProblems,
  wireRows,
} from '@/components/design/wire-model';

/** M9c: wire details entered in the guided editor (SYNTHETIC data; no electrical values implied). */

const REF = 'tenant/synth-wires/v1';
const SRC = 'tenant/synth-src/v1';
type Raw = Record<string, unknown>;

function base(wires: Raw = {}): Configuration {
  return {
    templates: [
      {
        ref: SRC,
        terminals: [
          { id: 't1', marking: 'A1' },
          { id: 't2', marking: 'A2' },
        ],
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
              { slot: 'src', terminal: 't1' },
              { point: 'p1', side: 'inner' },
              { slot: 'src', terminal: 't2' },
            ],
            connections: [
              { from: 0, to: 1, via: 'wire', core: null, label: null },
              { from: 1, to: 2, via: 'jumper', core: null, label: null },
              { from: 2, to: 1, via: 'wire', core: null, label: null },
            ],
          },
        ],
      },
    ],
    strips: [{ id: 'strip-x', board: 'MB-1', designation: { product: 'XP' } }],
    components: [{ id: 'comp-k', board: 'MB-1', template: SRC, designation: { product: 'KA' } }],
    bindings: [
      {
        id: 'bind-1',
        pattern: REF,
        board: 'MB-1',
        circuit: null,
        slots: { src: { component: 'comp-k' } },
        strips: { out: 'strip-x' },
        markings: { p1: '7' },
        cable: null,
        wires,
      },
    ],
  };
}

const only = (doc: Configuration) => {
  const b = bindingsOf(doc)[0];
  if (b === undefined) throw new Error('no binding');
  return b;
};
const wiresOf = (doc: Configuration) => only(doc).wires;

describe('the wire connections a binding has, named as the user knows them', () => {
  it('lists only wire connections, with readable ends and no index asked for', () => {
    const rows = wireRows(base(), only(base()));
    expect(rows.map((r) => [r.key, r.from, r.to, r.signal])).toEqual([
      ['n1/0', 'KA:A1', 'XP:7', 'S1'],
      ['n1/2', 'KA:A2', 'XP:7', 'S1'],
    ]);
    expect(rows[0]?.values.number).toEqual({ kind: 'unset' });
  });

  it('shows stored values exactly, with their source', () => {
    const doc = base({
      'n1/0': {
        number: 'W1',
        colour: { value: 'BK', source: 'assumption', statement: 'house practice (synthetic)' },
      },
    });
    const row = wireRows(doc, only(doc))[0];
    expect(row?.values.number).toEqual({
      kind: 'given',
      value: 'W1',
      source: 'data',
      statement: null,
    });
    expect(row?.values.colour).toEqual({
      kind: 'given',
      value: 'BK',
      source: 'assumption',
      statement: 'house practice (synthetic)',
    });
    expect(row?.values.cross_section_mm2).toEqual({ kind: 'unset' });
  });
});

describe('entering and removing details', () => {
  it('stores a typed value as user-entered and fills in nothing else', () => {
    const doc = setUserValue(base(), 'bind-1', 'n1/0', 'number', 'W7');
    expect(wiresOf(doc)).toEqual({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
  });

  it('never changes a value of another source in place; replacing is explicit', () => {
    const doc = base({ 'n1/0': { number: 'W1' } });
    expect(setUserValue(doc, 'bind-1', 'n1/0', 'number', 'W9')).toBe(doc);
    const replaced = replaceWithUserValue(doc, 'bind-1', 'n1/0', 'number', '');
    expect(wiresOf(replaced)).toEqual({ 'n1/0': { number: { value: '', source: 'user' } } });
    expect(wireProblems(replaced, only(replaced)).values).toEqual({ 'n1/0|number': 'empty' });
  });

  it('removes one field or a whole entry only when asked, and lists what it holds', () => {
    let doc = setUserValue(base(), 'bind-1', 'n1/0', 'number', 'W7');
    doc = setUserValue(doc, 'bind-1', 'n1/0', 'colour', 'BU');
    expect(wireDetailsOf(only(doc))).toEqual([
      { key: 'n1/0', field: 'number', value: 'W7' },
      { key: 'n1/0', field: 'colour', value: 'BU' },
    ]);
    doc = removeWireValue(doc, 'bind-1', 'n1/0', 'number');
    expect(wiresOf(doc)).toEqual({ 'n1/0': { colour: { value: 'BU', source: 'user' } } });
    doc = removeWireEntry(doc, 'bind-1', 'n1/0');
    expect(wiresOf(doc)).toEqual({});
  });

  it('keeps an unknown stored name untouched and says it, until removed', () => {
    const doc = base({ 'n1/0': { number: 'W1', gauge: 'x' } });
    const typed = setUserValue(doc, 'bind-1', 'n1/0', 'colour', 'RD');
    expect(wiresOf(typed)).toEqual({
      'n1/0': { number: 'W1', gauge: 'x', colour: { value: 'RD', source: 'user' } },
    });
    expect(wireProblems(typed, only(typed)).extra).toEqual(['n1/0|gauge']);
  });
});

describe('what the server would refuse, said beside the field', () => {
  it('cross-section is a positive plain decimal; text has a bound', () => {
    const at = (field: 'number' | 'cross_section_mm2' | 'colour', text: string) => {
      const doc = setUserValue(base(), 'bind-1', 'n1/0', field, text);
      return wireProblems(doc, only(doc)).values[`n1/0|${field}`] ?? null;
    };
    expect(at('cross_section_mm2', '1.5')).toBeNull();
    expect(at('cross_section_mm2', '1,5')).toBe('not_decimal');
    expect(at('cross_section_mm2', '0')).toBe('not_decimal');
    expect(at('cross_section_mm2', '-2')).toBe('not_decimal');
    expect(at('cross_section_mm2', '1e3')).toBe('not_decimal');
    expect(at('number', 'x'.repeat(201))).toBe('too_long');
    expect(at('colour', '')).toBe('empty');
  });

  it('a stored source the design does not accept is said', () => {
    const doc = base({ 'n1/0': { number: { value: 'W1', source: 'rule' } } });
    expect(wireProblems(doc, only(doc)).values).toEqual({ 'n1/0|number': 'unknown_source' });
  });
});

describe('details stay with their connection', () => {
  it('a key no wire connection has is said and removed only when asked', () => {
    const doc = base({ 'n1/1': { number: 'W3' } }); // n1/1 is a jumper: no wire
    expect(strayWireKeys(doc, only(doc))).toEqual(['n1/1']);
    expect(wiresOf(removeWireEntry(doc, 'bind-1', 'n1/1'))).toEqual({});
  });

  it('connection edits that would move or drop details are refused (M9b2 guards)', () => {
    const doc = setUserValue(base(), 'bind-1', 'n1/2', 'number', 'W8');
    expect(removeConnection(doc, REF, 'n1', 0)).toBe(doc);
    expect(
      updateConnection(doc, REF, 'n1', 2, { from: 0, to: 1, via: 'wire', core: null, label: null }),
    ).toBe(doc);
    // A connection with no details at or after it may still go; W8 stays on its connection.
    const free = setUserValue(base(), 'bind-1', 'n1/0', 'number', 'W7');
    const after = removeConnection(free, REF, 'n1', 2);
    expect(wiresOf(after)).toEqual({ 'n1/0': { number: { value: 'W7', source: 'user' } } });
  });
});

describe('independent review regressions', () => {
  it('value shapes the composer refuses are said (assumption statement rules, extra keys)', () => {
    const problems = (raw: unknown) => {
      const doc = base({ 'n1/0': { number: raw } });
      return wireProblems(doc, only(doc)).values['n1/0|number'] ?? null;
    };
    expect(problems({ value: 'W1', source: 'assumption' })).toBe('missing_statement');
    expect(problems({ value: 'W1', source: 'assumption', statement: ' ' })).toBe(
      'missing_statement',
    );
    expect(problems({ value: 'W1', source: 'user', statement: 'x' })).toBe('statement_not_allowed');
    expect(problems({ value: 'W1', source: 'user', assumption: 'a-1' })).toBe('unreadable');
    expect(problems({ value: 'W1', source: 'assumption', statement: 'synthetic' })).toBeNull();
  });

  it('an entry that is not an object is said, never overwritten, and removable', () => {
    const doc = base({ 'n1/0': 'W7' });
    const row = wireRows(doc, only(doc))[0];
    expect(row?.unreadableEntry).toBe('"W7"');
    expect(wireProblems(doc, only(doc)).unreadable).toEqual(['n1/0']);
    expect(setUserValue(doc, 'bind-1', 'n1/0', 'number', 'W8')).toBe(doc);
    expect(wiresOf(removeWireEntry(doc, 'bind-1', 'n1/0'))).toEqual({});
  });

  it('ends are named as the schedule names them: the product, else the id', () => {
    const doc = base();
    const components = doc.components as Raw[];
    const withFunction = {
      ...doc,
      components: [
        { ...components[0], designation: { function: '=A1', location: '+L', product: 'KA' } },
      ],
      strips: [{ id: 'strip-x', board: 'MB-1', designation: null }],
    };
    const row = wireRows(withFunction, only(withFunction))[0];
    expect([row?.from, row?.to]).toEqual(['KA:A1', 'strip-x:7']);
  });
});
