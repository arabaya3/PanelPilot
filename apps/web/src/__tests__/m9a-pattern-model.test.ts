import { describe, expect, it } from 'vitest';

import { conflictTarget, type Configuration } from '@/components/design/configuration-model';
import { removeDefinition } from '@/components/design/definition-model';
import {
  addMember,
  addNet,
  addPattern,
  addPoint,
  addRole,
  addSlot,
  partIdProblem,
  patternIdProblem,
  patternProblems,
  patternsOf,
  patternUsers,
  positionProblem,
  removeMember,
  removePattern,
  removePoint,
  removeRole,
  removeSlot,
  renamePattern,
  templateTerminals,
  updateSlotTemplate,
} from '@/components/design/pattern-model';

/** The item at `index`, or a test failure (no non-null assertions). */
function at<T>(list: readonly T[] | undefined, index: number): T {
  const item = list?.[index];
  if (item === undefined) throw new Error(`no item ${String(index)}`);
  return item;
}

/** M9a: connection patterns entered in the guided editor (SYNTHETIC data). */

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';
const REF = 'tenant/synth-two-slot/v1';

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

function base(): Configuration {
  return {
    templates: [definition(SRC, ['A1', 'A2']), definition(DST, ['B1'])],
    patterns: [],
    strips: [],
    components: [],
    bindings: [],
  };
}

/** The acceptance pattern: two slots, one strip role, two points, two explicit nets. */
function built(): Configuration {
  let doc = addPattern(base(), 'synth-two-slot');
  doc = addSlot(doc, REF, { id: 'src', template: SRC });
  doc = addSlot(doc, REF, { id: 'dst', template: DST });
  doc = addRole(doc, REF, 'out');
  doc = addPoint(doc, REF, { id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' });
  doc = addPoint(doc, REF, { id: 'p2', strip: 'out', offset: 2, level: 1, kind: 'unspecified' });
  doc = addNet(doc, REF, { role: 'n1', signal: 'S1', scope: 'binding' });
  doc = addMember(doc, REF, 'n1', { slot: 'src', terminal: 't-001' });
  doc = addMember(doc, REF, 'n1', { point: 'p1', side: 'inner' });
  doc = addMember(doc, REF, 'n1', { slot: 'dst', terminal: 't-001' });
  doc = addNet(doc, REF, { role: 'n2', signal: 'S2', scope: 'binding' });
  doc = addMember(doc, REF, 'n2', { slot: 'src', terminal: 't-002' });
  doc = addMember(doc, REF, 'n2', { point: 'p2', side: 'inner' });
  return doc;
}

const known = new Set([SRC, DST]);

describe('building a pattern without JSON', () => {
  it('writes the composer shape, user-entered and unreviewed, with nothing inferred', () => {
    const stored = (built().patterns as Record<string, unknown>[])[0] as Record<string, unknown>;
    expect(stored).toEqual({
      ref: REF,
      slots: [
        { id: 'src', template: SRC, device_kind: null },
        { id: 'dst', template: DST, device_kind: null },
      ],
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
      ],
      provenance: expect.objectContaining({ source: null, author: 'user-entered' }) as unknown,
      review: {
        status: 'unreviewed',
        reviewer: null,
        reviewed_at: null,
        standards: [],
        scope_note: null,
      },
    });
    expect(patternProblems(built(), at(patternsOf(built()), 0), known)).toEqual({
      slots: {},
      points: {},
      nets: {},
      members: {},
    });
  });

  it('refuses ids that are empty, malformed or taken, and positions below 1', () => {
    const doc = built();
    expect(patternIdProblem(doc, '')).toBe('empty');
    expect(patternIdProblem(doc, 'Bad Id')).toBe('invalid');
    expect(patternIdProblem(doc, 'synth-two-slot')).toBe('taken');
    expect(partIdProblem(['src'], 'src')).toBe('taken');
    expect(partIdProblem([], 'a b')).toBe('invalid');
    expect(positionProblem('0')).toBe('invalid');
    expect(positionProblem('1.5')).toBe('invalid');
    expect(positionProblem('3')).toBeNull();
    // Refused changes leave the document unchanged.
    expect(addSlot(doc, REF, { id: 'src', template: null })).toBe(doc);
    expect(addNet(doc, REF, { role: 'n3', signal: '  ', scope: 'binding' })).toBe(doc);
    expect(addMember(doc, REF, 'n1', { slot: 'src', terminal: 't-001' })).toBe(doc);
    expect(
      addPoint(doc, REF, { id: 'p3', strip: 'no-role', offset: 1, level: 1, kind: 'unspecified' }),
    ).toBe(doc);
  });

  it("offers a slot template's terminals where the configuration holds them", () => {
    expect(templateTerminals(base(), SRC)).toEqual([
      { id: 't-001', marking: 'A1' },
      { id: 't-002', marking: 'A2' },
    ]);
    expect(templateTerminals(base(), 'global/registry-only/v1')).toBeNull();
    expect(templateTerminals(base(), null)).toBeNull();
  });
});

describe('what the composer would refuse, said before saving', () => {
  it('a slot on an unknown template, a terminal its template lacks, a member twice or in two nets', () => {
    let doc = updateSlotTemplate(built(), REF, 'src', 'tenant/not-held/v1');
    expect(patternProblems(doc, at(patternsOf(doc), 0), known).slots).toEqual({
      src: 'unknown_template',
    });

    doc = updateSlotTemplate(built(), REF, 'src', DST); // DST has only t-001
    expect(patternProblems(doc, at(patternsOf(doc), 0), known).members).toEqual({
      'n2#0': 'not_on_template',
    });

    const raw = structuredClone(built()) as { patterns: { nets: { members: unknown[] }[] }[] };
    at(at(raw.patterns, 0).nets, 1).members.push({ slot: 'dst', terminal: 't-001' });
    at(at(raw.patterns, 0).nets, 0).members.push({ point: 'p1', side: 'inner' });
    const twice = raw as unknown as Configuration;
    expect(patternProblems(twice, at(patternsOf(twice), 0), known).members).toEqual({
      'n1#3': 'twice',
      'n2#2': 'two_nets',
    });
  });

  it('two board-scope nets with one signal are one net and may share a member', () => {
    let doc = addNet(built(), REF, { role: 'b1', signal: 'N', scope: 'board' });
    doc = addNet(doc, REF, { role: 'b2', signal: 'N', scope: 'board' });
    doc = addMember(doc, REF, 'b1', { point: 'p2', side: 'outer' });
    doc = addMember(doc, REF, 'b2', { point: 'p2', side: 'outer' });
    expect(patternProblems(doc, at(patternsOf(doc), 0), known).members).toEqual({});
  });

  it('an empty net is said', () => {
    const doc = addNet(built(), REF, { role: 'n3', signal: 'S3', scope: 'binding' });
    expect(patternProblems(doc, at(patternsOf(doc), 0), known).nets).toEqual({ n3: 'no_members' });
  });
});

describe('references are checked before anything is removed or renamed', () => {
  function bound(): Configuration {
    return {
      ...built(),
      bindings: [
        {
          id: 'bind-1',
          pattern: REF,
          board: 'MB-1',
          circuit: null,
          slots: { src: { component: 'q1' } },
          strips: { out: 'strip-x' },
          markings: { p2: '8' },
          cable: null,
          wires: {},
        },
      ],
    };
  }

  it('a slot, role or point in use is not removed; an unused one is', () => {
    const doc = bound();
    expect(removeSlot(doc, REF, 'src')).toBe(doc); // net member and binding
    expect(removeRole(doc, REF, 'out')).toBe(doc); // points and binding
    expect(removePoint(doc, REF, 'p1')).toBe(doc); // net member
    expect(removePoint(doc, REF, 'p2')).toBe(doc); // net member and binding marking
    const free = addSlot(doc, REF, { id: 'spare', template: null });
    expect(at(patternsOf(removeSlot(free, REF, 'spare')), 0).slots.map((s) => s.id)).toEqual([
      'src',
      'dst',
    ]);
  });

  it('a pattern a binding applies is not removed; a rename repoints the binding', () => {
    const doc = bound();
    expect(patternUsers(doc, REF)).toEqual(['bind-1']);
    expect(removePattern(doc, REF)).toBe(doc);
    const renamed = renamePattern(doc, REF, 'synth-renamed');
    expect(patternsOf(renamed).map((p) => p.ref)).toEqual(['tenant/synth-renamed/v1']);
    expect(at(renamed.bindings as { pattern: string }[], 0).pattern).toBe(
      'tenant/synth-renamed/v1',
    );
  });

  it('a definition a slot names is not removed', () => {
    const doc = built();
    expect(removeDefinition(doc, SRC)).toBe(doc);
  });

  it('a member a stored connection names is kept; removing another re-indexes the connections', () => {
    const raw = structuredClone(built()) as {
      patterns: { nets: { connections: Record<string, unknown>[] }[] }[];
    };
    at(at(raw.patterns, 0).nets, 0).connections = [
      { from: 1, to: 2, via: 'wire', core: null, label: null },
    ];
    const doc = raw as unknown as Configuration;
    expect(removeMember(doc, REF, 'n1', 1)).toBe(doc);
    const after = removeMember(doc, REF, 'n1', 0);
    const net = at(at(after.patterns as { nets: Record<string, unknown>[] }[], 0).nets, 0);
    expect(net.members).toEqual([
      { point: 'p1', side: 'inner' },
      { slot: 'dst', terminal: 't-001' },
    ]);
    // The same two members, at their new indexes; the other keys kept as stored.
    expect(net.connections).toEqual([{ from: 0, to: 1, via: 'wire', core: null, label: null }]);
  });
});

describe('a save refusal naming a pattern', () => {
  it('marks that pattern', () => {
    expect(
      conflictTarget({ refusal: 'terminal_not_on_template', pattern: REF, role: 'n2' }),
    ).toEqual({
      kind: 'pattern',
      entry: REF,
      ref: REF,
      refKind: 'pattern',
      reason: 'terminal_not_on_template',
    });
  });
});
