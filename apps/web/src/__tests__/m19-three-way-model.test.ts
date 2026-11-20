import { describe, expect, it } from 'vitest';

import {
  outsideChanges,
  storedSource,
  threeWay,
  type Review,
  type Row,
  type Source,
} from '@/components/design/three-way-model';
import type { SavedProject } from '@/lib/design';

/** M19 three-way classification. SYNTHETIC data. */

type L = Record<string, unknown>;
const load = (description: string, over: L = {}): L => ({
  description,
  load: 'lighting',
  power_kw: '1.5',
  phases: 1,
  power_factor: null,
  controlled: false,
  starter: null,
  length_m: '12',
  ...over,
});
const board = (name: string, loads: L[], over: L = {}): L => ({
  name,
  location: null,
  fed_from: null,
  feeder_length_m: null,
  supply: {
    voltage_v: '400',
    phases: 3,
    frequency_hz: '50',
    earthing: 'TN-S',
    fault_level_ka: '10',
    earth_loop_ohm: null,
  },
  loads,
  ...over,
});
const info = (over: L = {}) => ({
  name: 'Tower',
  number: 'J-1',
  customer: 'Synth',
  consultant: '',
  contractor: '',
  ...over,
});
const src = (boards: L[], over: Partial<Source> & { info?: L } = {}): Source => ({
  request: { info: info(over.info), boards, profile: null },
  configuration: over.configuration ?? {
    templates: [],
    patterns: [],
    strips: [],
    components: [],
    bindings: [],
  },
  links: over.links ?? true,
});

function rows(review: Review): Row[] {
  return review.sections.flatMap((s) => s.rows);
}
function row(review: Review, type: string, label: string): Row | undefined {
  return rows(review).find((r) => r.type === type && r.label === label);
}

const B = [board('MDB', [load('Lights'), load('Pump', { power_kw: '4' }), load('Fan')])];

describe('three-way classification', () => {
  it('disjoint edits: one only in yours, one only in latest', () => {
    const r = threeWay(
      src(B),
      src([
        board('MDB', [
          load('Lights', { power_kw: '2.2' }),
          load('Pump', { power_kw: '4' }),
          load('Fan'),
        ]),
      ]),
      src(B, { info: { customer: 'Synth Two' } }),
    );
    expect(row(r, 'load', 'Lights')).toMatchObject({
      status: 'yours',
      fields: [
        {
          field: 'power_kw',
          base: '1.5',
          yours: '2.2',
          latest: '1.5',
          status: 'yours',
          unit: 'kW',
        },
      ],
    });
    expect(row(r, 'details', 'project')).toMatchObject({
      status: 'latest',
      fields: [{ field: 'customer', status: 'latest' }],
    });
    expect(rows(r)).toHaveLength(2);
  });

  it('the same field changed differently is a conflict; changed the same is the same change', () => {
    const yours = src([
      board('MDB', [
        load('Lights', { power_kw: '2.2' }),
        load('Pump', { power_kw: '5' }),
        load('Fan'),
      ]),
    ]);
    const latest = src([
      board('MDB', [
        load('Lights', { power_kw: '3' }),
        load('Pump', { power_kw: '5.0' }),
        load('Fan'),
      ]),
    ]);
    const r = threeWay(src(B), yours, latest);
    expect(row(r, 'load', 'Lights')).toMatchObject({
      status: 'conflict',
      fields: [{ field: 'power_kw', yours: '2.2', latest: '3', status: 'conflict' }],
    });
    // 5 and 5.0 are one quantity: the same change on both sides.
    expect(row(r, 'load', 'Pump')?.status).toBe('same');
  });

  it('one item, different fields on each side: both, fields shown individually (no verdict)', () => {
    const r = threeWay(
      src(B),
      src([
        board('MDB', [
          load('Lights', { power_kw: '2.2' }),
          load('Pump', { power_kw: '4' }),
          load('Fan'),
        ]),
      ]),
      src([
        board('MDB', [
          load('Lights', { length_m: '20' }),
          load('Pump', { power_kw: '4' }),
          load('Fan'),
        ]),
      ]),
    );
    const lights = row(r, 'load', 'Lights');
    expect(lights?.status).toBe('both');
    expect(lights?.fields.map((f) => [f.field, f.status])).toEqual([
      ['length_m', 'latest'],
      ['power_kw', 'yours'],
    ]);
  });

  it('additions and deletions; a deletion against an edit is a conflict', () => {
    const r = threeWay(
      src(B),
      src([board('MDB', [load('Lights'), load('Fan'), load('Heater', { power_kw: '3' })])]), // Pump removed, Heater added
      src([board('MDB', [load('Lights'), load('Pump', { power_kw: '6' })])]), // Pump edited, Fan removed
    );
    expect(row(r, 'load', 'Heater')).toMatchObject({
      status: 'yours',
      presence: { base: false, yours: true, latest: false },
    });
    expect(row(r, 'load', 'Fan')).toMatchObject({
      status: 'latest',
      presence: { base: true, yours: true, latest: false },
    });
    expect(row(r, 'load', 'Pump')).toMatchObject({
      status: 'conflict',
      presence: { base: true, yours: false, latest: true },
    });
  });

  it('the same addition on both sides is the same change; different additions under one name conflict', () => {
    const r = threeWay(
      src(B),
      src([
        board('MDB', [
          ...(B[0]?.loads as L[]),
          load('New', { power_kw: '2' }),
          load('Other', { power_kw: '1' }),
        ]),
      ]),
      src([
        board('MDB', [
          ...(B[0]?.loads as L[]),
          load('New', { power_kw: '2' }),
          load('Other', { power_kw: '9' }),
        ]),
      ]),
    );
    expect(row(r, 'load', 'New')?.status).toBe('same');
    expect(row(r, 'load', 'Other')?.status).toBe('conflict');
  });

  it('reordering changes nothing', () => {
    const r = threeWay(
      src(B),
      src([board('MDB', [load('Fan'), load('Lights'), load('Pump', { power_kw: '4' })])]),
      src(B),
    );
    expect(rows(r)).toEqual([]);
  });

  it('duplicate descriptions that differ are unresolved with all three sides; identical groups are quiet', () => {
    const twin = (kw: string) => load('Socket', { load: 'socket', power_kw: kw });
    const base = src([board('MDB', [twin('1'), twin('2')])]);
    expect(rows(threeWay(base, src([board('MDB', [twin('2'), twin('1')])]), base))).toEqual([]);
    const r = threeWay(base, src([board('MDB', [twin('1'), twin('3')])]), base);
    expect(row(r, 'load', 'Socket')).toMatchObject({ status: 'unresolved', reason: 'duplicate' });
    expect(row(r, 'load', 'Socket')?.sides?.yours.map((s) => s.power_kw)).toEqual(['1', '3']);
  });

  it('a board renamed on one side is removed and added, never a rename', () => {
    const r = threeWay(src(B), src([board('MDB-1', B[0]?.loads as L[])]), src(B));
    expect(row(r, 'board', 'MDB')).toMatchObject({ status: 'yours', presence: { yours: false } });
    expect(row(r, 'board', 'MDB-1')).toMatchObject({ status: 'yours', presence: { base: false } });
  });

  // Configuration and wiring
  const net = {
    role: 'n1',
    signal: 'S1',
    scope: 'binding',
    members: [
      { slot: 'src', terminal: 'A1' },
      { point: 'p1', side: 'inner' },
    ],
    connections: [{ from: 0, to: 1, via: 'wire', core: null, label: null }],
  };
  const pat = {
    ref: 'pat/two',
    slots: [{ id: 'src', template: 'tpl/a' }],
    strips: ['out'],
    points: [{ id: 'p1', strip: 'out', offset: 0, level: 0, kind: 'x' }],
    nets: [net],
  };
  const cfg = (
    o: { src?: string; wires?: Record<string, unknown>; tplTerminals?: string[] } = {},
  ) => ({
    templates: [{ ref: 'tpl/a', terminals: (o.tplTerminals ?? ['A1']).map((id) => ({ id })) }],
    patterns: [pat],
    strips: [{ id: 'st-1', board: 'MDB', template: null, designation: null, points: [] }],
    components: [
      { id: 'c-1', board: 'MDB', template: 'tpl/a', designation: null },
      { id: 'c-2', board: 'MDB', template: 'tpl/a', designation: null },
    ],
    bindings: [
      {
        id: 'b-1',
        pattern: 'pat/two',
        board: 'MDB',
        circuit: 'Pump',
        slots: { src: { component: o.src ?? 'c-1' } },
        strips: { out: 'st-1' },
        markings: { p1: '7' },
        cable: null,
        wires: o.wires ?? { 'n1/0': { number: 'W7' } },
      },
    ],
  });

  it('wire details by position while net and endpoints are one on all sides', () => {
    const r = threeWay(
      src(B, { configuration: cfg() }),
      src(B, { configuration: cfg({ wires: { 'n1/0': { number: 'W8' } } }) }),
      src(B, { configuration: cfg() }),
    );
    expect(rows(r).find((x) => x.type === 'wire')).toMatchObject({
      status: 'yours',
      fields: [{ field: 'number', yours: 'W8', status: 'yours' }],
    });
  });

  it('a slot remapped on the other side makes your wire edit unresolved, and the binding change is latest', () => {
    const r = threeWay(
      src(B, { configuration: cfg() }),
      src(B, { configuration: cfg({ wires: { 'n1/0': { number: 'W8' } } }) }),
      src(B, { configuration: cfg({ src: 'c-2' }) }),
    );
    expect(rows(r).find((x) => x.type === 'wire')).toBeUndefined();
    expect(rows(r).find((x) => x.type === 'wires')).toMatchObject({
      status: 'unresolved',
      reason: 'wire_endpoints',
    });
    expect(row(r, 'binding', 'b-1')).toMatchObject({
      status: 'latest',
      fields: [{ field: 'slots.src.component', latest: 'c-2' }],
    });
  });

  it('changed terminal definitions on one side also leave wire positions unresolved', () => {
    const r = threeWay(
      src(B, { configuration: cfg() }),
      src(B, { configuration: cfg({ wires: { 'n1/0': { number: 'W8' } } }) }),
      src(B, { configuration: cfg({ tplTerminals: ['A1', 'A2'] }) }),
    );
    expect(rows(r).find((x) => x.type === 'wires')?.status).toBe('unresolved');
    expect(row(r, 'template', 'tpl/a')?.status).toBe('latest');
  });

  it('a one-sided edit lists what it depends on from the other side, without a verdict', () => {
    const yours = cfg();
    (yours.bindings[0] as L).circuit = 'Pump 2';
    const latest = cfg();
    (latest.components[0] as L).designation = { function: null, location: null, product: 'K9' };
    const r = threeWay(
      src(B, { configuration: cfg() }),
      src(B, { configuration: yours }),
      src(B, { configuration: latest }),
    );
    expect(row(r, 'binding', 'b-1')).toMatchObject({
      status: 'yours',
      dependsOn: [{ type: 'component', label: 'c-1', side: 'latest' }],
    });
  });

  it('a board you edit that latest removed is a conflict; a load you add to a board latest renamed depends on it', () => {
    const r = threeWay(
      src(B),
      src([board('MDB', [...(B[0]?.loads as L[]), load('Extra')], { location: 'Hall' })]),
      src([board('MDB-2', B[0]?.loads as L[])]),
    );
    expect(row(r, 'board', 'MDB')?.status).toBe('conflict');
    expect(row(r, 'load', 'Extra')).toMatchObject({
      status: 'yours',
      dependsOn: [{ type: 'board', label: 'MDB' }],
    });
  });

  it('a configuration one source cannot hold is unavailable, never unchanged; links likewise', () => {
    const r = threeWay(src(B, { configuration: 'unavailable', links: false }), src(B), src(B));
    expect(r.sections.find((s) => s.id === 'configuration')).toMatchObject({
      status: 'unavailable',
      unavailableIn: ['base'],
    });
    expect(r.sections.find((s) => s.id === 'links')).toMatchObject({
      status: 'unavailable',
      unavailableIn: ['base'],
    });
  });

  it('identical sources show nothing', () => {
    expect(
      rows(
        threeWay(
          src(B, { configuration: cfg() }),
          src(B, { configuration: cfg() }),
          src(B, { configuration: cfg() }),
        ),
      ),
    ).toEqual([]);
  });
});

describe('one representation for all three sources', () => {
  const stored = (revision: number, request: L, over: L = {}) =>
    ({
      id: 'p1',
      revision,
      request,
      snapshot_schema: 'pp-design/3',
      configuration: { templates: [], patterns: [], strips: [], components: [], bindings: [] },
      configuration_status: 'stored',
      findings: [],
      ...over,
    }) as unknown as SavedProject;
  const request = (over: L = {}, loadOver: L = {}) => ({
    info: { ...info(), revisions: [{ index: '01', date: 'd' }] },
    boards: [
      {
        ...board('MDB', [{ ...load('Lights'), feeds: null, ...loadOver }]),
        conditions: {
          installation_method: 'B1',
          ambient_temp_c: '30',
          grouped_circuits: 1,
          conductor_material: 'copper',
          insulation_rating_c: 70,
        },
        ...over,
      },
    ],
    profile: null,
  });

  it('a stored revision and the form built from it are one: stored defaults are not changes', () => {
    const base = storedSource(stored(1, request()));
    expect(rows(threeWay(base, storedSource(stored(1, request())), base))).toEqual([]);
  });

  it('stored fields the editor does not carry, changed in latest, are listed as outside the comparison', () => {
    const b = stored(1, request());
    const l = stored(2, {
      ...request(
        {
          conditions: {
            installation_method: 'C',
            ambient_temp_c: '40',
            grouped_circuits: 1,
            conductor_material: 'copper',
            insulation_rating_c: 70,
          },
        },
        { feeds: 'DB2' },
      ),
      profile: { key: 'iec-default', rating: 'x' },
    });
    const out = outsideChanges(b, l, storedSource(b), storedSource(l));
    expect([...out].sort()).toEqual(
      [
        'MDB: conditions.ambient_temp_c',
        'MDB: conditions.installation_method',
        'MDB › Lights: feeds',
        'profile',
      ].sort(),
    );
  });

  it('title-block entries (each save records its own) are not listed', () => {
    const b = stored(1, request());
    const lr = request();
    (lr.info as L).revisions = [
      { index: '01', date: 'd' },
      { index: '02', date: 'e' },
    ];
    expect(outsideChanges(b, stored(2, lr), storedSource(b), storedSource(stored(2, lr)))).toEqual(
      [],
    );
  });
});
