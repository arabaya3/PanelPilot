import { describe, expect, it } from 'vitest';

import {
  changedCount,
  compareRevisions,
  type Comparison,
  type Item,
  type Section,
} from '@/components/design/revision-compare-model';
import type { SavedProject } from '@/lib/design';

/** M18 comparison model: identity never by position or generated id. SYNTHETIC data. */

type Load = Record<string, unknown>;

function load(description: string, over: Load = {}): Load {
  return {
    description,
    load: 'lighting',
    power_kw: '1.5',
    phases: 1,
    power_factor: null,
    controlled: false,
    feeds: null,
    starter: null,
    length_m: '12',
    ...over,
  };
}

function board(name: string, loads: Load[], over: Record<string, unknown> = {}) {
  return {
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
    conditions: {
      installation_method: 'B1',
      ambient_temp_c: '30',
      grouped_circuits: 1,
      conductor_material: 'copper',
      insulation_rating_c: 70,
    },
    loads,
    ...over,
  };
}

function project(revision: number, over: Partial<Record<string, unknown>> = {}): SavedProject {
  const { boards, info, configuration, links, ...rest } = over as {
    boards?: unknown[];
    info?: Record<string, unknown>;
    configuration?: unknown;
    links?: unknown;
  };
  return {
    id: 'p1',
    name: 'Tower',
    revision,
    revisions: [],
    request: {
      info: {
        name: 'Tower',
        number: 'J-1',
        customer: 'Synth',
        consultant: '',
        contractor: '',
        revisions: [],
        ...info,
      },
      boards: boards ?? [board('MDB', [load('Lights')])],
      profile: null,
      feeder_links: links ?? [],
    },
    findings: [],
    validated: true,
    snapshot_sha256: `hash-${String(revision)}`,
    rules_version: 'r1',
    snapshot_schema: 'pp-design/3',
    configuration: configuration ?? {
      templates: [],
      patterns: [],
      strips: [],
      components: [],
      bindings: [],
    },
    configuration_status: 'stored',
    ...rest,
  } as unknown as SavedProject;
}

function compared(c: Comparison) {
  if (c.kind !== 'compared') throw new Error('not compared');
  return c;
}

function section(c: Comparison, id: Section['id']): Section {
  const found = compared(c).sections.find((s) => s.id === id);
  if (!found) throw new Error(id);
  return found;
}

function boardItem(c: Comparison, name: string): Item {
  const item = section(c, 'boards').items.find((i) => i.type === 'board' && i.label === name);
  if (!item) throw new Error(name);
  return item;
}

describe('revision comparison model', () => {
  it('reports additions, removals and edits with units and unset values', () => {
    const a = project(1, {
      boards: [board('MDB', [load('Lights'), load('Pump', { load: 'motor', power_kw: '4' })])],
    });
    const b = project(2, {
      info: { customer: 'Synth Two' },
      boards: [
        board(
          'MDB',
          [
            load('Lights', { power_kw: '2.2', length_m: null }),
            load('Sockets', { load: 'socket' }),
          ],
          {
            location: 'Hall',
          },
        ),
      ],
    });
    const c = compareRevisions(a, b);
    const details = section(c, 'details').items[0];
    expect(details?.changes).toEqual([{ field: 'customer', before: 'Synth', after: 'Synth Two' }]);
    const mdb = boardItem(c, 'MDB');
    expect(mdb.kind).toBe('changed');
    expect(mdb.changes).toEqual([{ field: 'location', after: 'Hall' }]);
    const lights = mdb.children?.find((i) => i.label === 'Lights');
    expect(lights?.changes).toEqual([
      { field: 'length_m', before: '12', unit: 'm' },
      { field: 'power_kw', before: '1.5', after: '2.2', unit: 'kW' },
    ]);
    expect(mdb.children?.find((i) => i.label === 'Pump')?.kind).toBe('removed');
    expect(mdb.children?.find((i) => i.label === 'Sockets')?.kind).toBe('added');
  });

  it('reordering loads changes nothing (never matched by position)', () => {
    const a = project(1, {
      boards: [board('MDB', [load('A'), load('B', { power_kw: '3' }), load('C')])],
    });
    const b = project(2, {
      boards: [board('MDB', [load('C'), load('A'), load('B', { power_kw: '3' })])],
    });
    const c = compareRevisions(a, b);
    expect(boardItem(c, 'MDB').kind).toBe('unchanged');
    expect(changedCount(section(c, 'boards'))).toBe(0);
  });

  it('an insertion before others does not shift identities onto the wrong loads', () => {
    const a = project(1, { boards: [board('MDB', [load('A'), load('B', { power_kw: '3' })])] });
    const b = project(2, {
      boards: [
        board('MDB', [load('New', { power_kw: '9' }), load('A'), load('B', { power_kw: '3' })]),
      ],
    });
    const children = boardItem(compareRevisions(a, b), 'MDB').children ?? [];
    expect(children.filter((i) => i.kind !== 'unchanged').map((i) => [i.kind, i.label])).toEqual([
      ['added', 'New'],
    ]);
  });

  it('a renamed load or board is one removed and one added, never a claimed rename', () => {
    const a = project(1, { boards: [board('MDB', [load('Lights')]), board('DB1', [load('Fan')])] });
    const b = project(2, {
      boards: [board('MDB', [load('Lighting')]), board('DB-1', [load('Fan')])],
    });
    const c = compareRevisions(a, b);
    const kinds = boardItem(c, 'MDB').children?.map((i) => [i.kind, i.label]);
    expect(kinds).toEqual([
      ['added', 'Lighting'],
      ['removed', 'Lights'],
    ]);
    expect(
      section(c, 'boards')
        .items.filter((i) => i.type === 'board')
        .map((i) => [i.kind, i.label]),
    ).toEqual([
      ['added', 'DB-1'],
      ['removed', 'DB1'],
      ['changed', 'MDB'],
    ]);
  });

  it('duplicate descriptions that differ form an ambiguous group with both sides', () => {
    const a = project(1, {
      boards: [
        board('MDB', [load('Socket', { power_kw: '1' }), load('Socket', { power_kw: '2' })]),
      ],
    });
    const b = project(2, {
      boards: [
        board('MDB', [load('Socket', { power_kw: '2' }), load('Socket', { power_kw: '3' })]),
      ],
    });
    const mdb = boardItem(compareRevisions(a, b), 'MDB');
    expect(mdb.kind).toBe('changed');
    expect(mdb.children?.filter((i) => i.label === 'Socket')).toEqual([]);
    expect(mdb.ambiguous).toHaveLength(1);
    expect(mdb.ambiguous?.[0]?.before.map((v) => v.power_kw)).toEqual(['1', '2']);
    expect(mdb.ambiguous?.[0]?.after.map((v) => v.power_kw)).toEqual(['2', '3']);
  });

  it('identical duplicate loads in any order read as identical, not as matched pairs', () => {
    const twin = load('Socket', { power_kw: '1' });
    const a = project(1, { boards: [board('MDB', [twin, load('Other'), twin])] });
    const b = project(2, { boards: [board('MDB', [twin, twin, load('Other')])] });
    const mdb = boardItem(compareRevisions(a, b), 'MDB');
    expect(mdb.kind).toBe('unchanged');
    expect(mdb.children?.find((i) => i.label === 'Socket')).toMatchObject({
      kind: 'identical',
      count: 2,
    });
  });

  it('one more identical duplicate is ambiguous, not an "added" claim on a specific one', () => {
    const twin = load('Socket');
    const mdb = boardItem(
      compareRevisions(
        project(1, { boards: [board('MDB', [twin, twin])] }),
        project(2, { boards: [board('MDB', [twin, twin, twin])] }),
      ),
      'MDB',
    );
    expect(mdb.ambiguous?.[0]).toMatchObject({ label: 'Socket', reason: 'duplicate' });
    expect(mdb.ambiguous?.[0]?.before).toHaveLength(2);
    expect(mdb.ambiguous?.[0]?.after).toHaveLength(3);
  });

  it('identical revisions show no changes', () => {
    const c = compareRevisions(project(1), project(2, { snapshot_sha256: 'hash-1' }));
    for (const s of compared(c).sections) expect(changedCount(s)).toBe(0);
  });

  it('missing historical data reads unavailable, never unchanged', () => {
    const old = project(1, {
      snapshot_schema: 'pp-design/1',
      configuration: null,
      configuration_status: 'none',
    });
    const c = compareRevisions(old, project(2));
    expect(section(c, 'configuration')).toMatchObject({
      status: 'unavailable',
      unavailableIn: [1],
    });
    expect(section(c, 'links')).toMatchObject({ status: 'unavailable', unavailableIn: [1] });
    const lost = compareRevisions(
      project(1, { configuration_status: 'not_recoverable', configuration: null }),
      project(2),
    );
    expect(section(lost, 'configuration')).toMatchObject({
      status: 'unavailable',
      unavailableIn: [1],
    });
  });

  it('an integrity failure in either revision compares nothing', () => {
    const broken = project(2, {
      findings: [{ code: 'snapshot_integrity_failed', severity: 'blocking', message: 'x' }],
    });
    expect(compareRevisions(project(1), broken)).toEqual({ kind: 'integrity', failed: [2] });
    const unknown = project(1, {
      findings: [{ code: 'snapshot_schema_unknown', severity: 'blocking', message: 'x' }],
    });
    expect(compareRevisions(unknown, broken)).toEqual({ kind: 'integrity', failed: [1, 2] });
  });

  it('feeder links are matched by the fed board, field by field', () => {
    const link = (fed: string, over: Record<string, unknown> = {}) => ({
      fed_board: fed,
      from_end: null,
      to_end: null,
      attributes: {
        cores: 4,
        material: 'Cu',
        insulation: null,
        length_m: '30',
        number: null,
        colour: null,
        ...over,
      },
    });
    const c = compareRevisions(
      project(1, { links: [link('DB1'), link('DB2')] }),
      project(2, { links: [link('DB2'), link('DB1', { length_m: '35', number: 'W7' })] }),
    );
    const items = section(c, 'links').items;
    expect(items.find((i) => i.label === 'DB1')?.changes).toEqual([
      { field: 'attributes.length_m', before: '30', after: '35', unit: 'm' },
      { field: 'attributes.number', after: 'W7' },
    ]);
    expect(items.find((i) => i.label === 'DB2')?.kind).toBe('unchanged');
  });

  const pattern = (ref: string, members: unknown[]) => ({
    ref,
    slots: [{ id: 'K', template: 'tpl/contactor' }],
    strips: [],
    points: [],
    nets: [
      {
        role: 'coil',
        signal: 'ctrl',
        scope: 'local',
        members,
        connections: [{ from: 0, to: 1, via: 'wire', core: null, label: null }],
      },
    ],
  });
  const binding = (
    id: string,
    boardName: string,
    wires: Record<string, unknown>,
    over: Record<string, unknown> = {},
  ) => ({
    id,
    pattern: 'pat/dol',
    board: boardName,
    circuit: 'Pump',
    markings: {},
    wires,
    ...over,
  });
  const config = (
    patterns: unknown[],
    bindings: unknown[],
    extra: Record<string, unknown> = {},
  ) => ({
    templates: [],
    patterns,
    strips: [],
    components: [],
    bindings,
    ...extra,
  });
  const members = [
    { slot: 'K', terminal: 'A1' },
    { slot: 'K', terminal: 'A2' },
  ];

  it('wire details are matched by position only while the pattern net is unchanged', () => {
    const before = config(
      [pattern('pat/dol', members)],
      [binding('b-1', 'MDB', { 'coil/0': { number: '101', cross_section_mm2: '1.5' } })],
    );
    const sameNet = config(
      [pattern('pat/dol', members)],
      [binding('b-1', 'MDB', { 'coil/0': { number: '102', cross_section_mm2: '1.5' } })],
    );
    const c = compareRevisions(
      project(1, { configuration: before }),
      project(2, { configuration: sameNet }),
    );
    const item = section(c, 'configuration').items.find((i) => i.type === 'binding');
    expect(item?.children?.[0]).toMatchObject({
      type: 'wire',
      label: 'coil/0',
      changes: [{ field: 'number', before: '101', after: '102' }],
    });

    // The net changed: position 0 may be another wire now, so nothing is matched.
    const changedNet = config(
      [
        pattern('pat/dol', [
          { slot: 'K', terminal: 'A2' },
          { slot: 'K', terminal: 'A1' },
        ]),
      ],
      [binding('b-1', 'MDB', { 'coil/0': { number: '102', cross_section_mm2: '1.5' } })],
    );
    const d = compareRevisions(
      project(1, { configuration: before }),
      project(2, { configuration: changedNet }),
    );
    const bound = section(d, 'configuration').items.find((i) => i.type === 'binding');
    expect(bound?.children).toEqual([]);
    expect(bound?.ambiguous?.[0]).toMatchObject({ label: 'coil', reason: 'wire_positions' });
    expect(section(d, 'configuration').items.find((i) => i.type === 'pattern')?.kind).toBe(
      'changed',
    );
  });

  it('the same configuration id on another board is removed and added, never a move', () => {
    const strip = (id: string, boardName: string) => ({
      id,
      board: boardName,
      template: null,
      designation: null,
      points: [],
    });
    const c = compareRevisions(
      project(1, { configuration: config([], [], { strips: [strip('s-1', 'MDB')] }) }),
      project(2, { configuration: config([], [], { strips: [strip('s-1', 'DB1')] }) }),
    );
    expect(section(c, 'configuration').items.map((i) => [i.type, i.kind, i.where])).toEqual([
      ['strip', 'added', 'DB1'],
      ['strip', 'removed', 'MDB'],
    ]);
  });

  it('patterns and templates by ref: renaming a pattern is removed and added', () => {
    const c = compareRevisions(
      project(1, { configuration: config([pattern('pat/dol', members)], []) }),
      project(2, { configuration: config([pattern('pat/star', members)], []) }),
    );
    expect(section(c, 'configuration').items.map((i) => [i.kind, i.label])).toEqual([
      ['removed', 'pat/dol'],
      ['added', 'pat/star'],
    ]);
  });

  it('stored output: compared as stored, findings counted without generated ids', () => {
    const finding = (device: string) => ({
      source: 'checks',
      code: 'cable_hot',
      severity: 'warning',
      message: 'Too hot',
      device_id: device,
      values: {},
    });
    const c = compareRevisions(
      project(1, { findings: [finding('dev-1')], rules_version: 'r1' }),
      project(2, { findings: [finding('dev-9'), finding('dev-10')], rules_version: 'r2' }),
    );
    const outputs = section(c, 'outputs').items;
    expect(outputs[0]?.changes).toEqual([{ field: 'rules_version', before: 'r1', after: 'r2' }]);
    expect(outputs.find((i) => i.label === 'stored_design')?.kind).toBe('changed');
    expect(outputs.find((i) => i.type === 'finding')?.changes).toEqual([
      { field: 'count', before: 1, after: 2 },
    ]);
  });

  it('a finding whose message names a regenerated wire id is the same finding, not removed and added', () => {
    const unset = (wire: string) => ({
      source: 'integrity',
      code: 'wire_attribute_unset',
      severity: 'warning',
      message: `wire ${wire} has no colour`,
    });
    const c = compareRevisions(
      project(1, { findings: [unset('w-aaa')] }),
      project(2, { findings: [unset('w-bbb')] }),
    );
    expect(section(c, 'outputs').items.filter((i) => i.type === 'finding')).toEqual([]);
  });

  it('pattern nets compare by role, as readable members and connections', () => {
    const c = compareRevisions(
      project(1, { configuration: config([pattern('pat/dol', members)], []) }),
      project(2, {
        configuration: config(
          [
            {
              ...pattern('pat/dol', members),
              nets: [
                {
                  role: 'coil',
                  signal: 'ctrl',
                  scope: 'local',
                  members,
                  connections: [{ from: 0, to: 1, via: 'cable_core', core: 1, label: 'BN' }],
                },
              ],
            },
          ],
          [],
        ),
      }),
    );
    const pat = section(c, 'configuration').items.find((i) => i.type === 'pattern');
    expect(pat?.children?.[0]).toMatchObject({
      type: 'net',
      label: 'coil',
      changes: [{ field: 'connections', before: '1→2 wire', after: '1→2 cable_core core 1 “BN”' }],
    });
  });

  it('decimals stored with different trailing zeros are one value', () => {
    const c = compareRevisions(
      project(1, { boards: [board('MDB', [load('A', { power_kw: '1.50' })])] }),
      project(2, { boards: [board('MDB', [load('A', { power_kw: '1.5' })])] }),
    );
    expect(boardItem(c, 'MDB').kind).toBe('unchanged');
  });
});
