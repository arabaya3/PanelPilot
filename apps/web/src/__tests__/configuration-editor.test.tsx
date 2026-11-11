import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { ConfigurationPanel, configurationPayload } from '@/components/design/configuration-panel';
import {
  addComponent,
  addStrip,
  bindingPoints,
  bindingsOf,
  conflictTarget,
  setMarking,
  templateChoices,
} from '@/components/design/configuration-model';
import { terminalOptions } from '@/components/design/feeder-link-model';
import type * as DesignModule from '@/lib/design';
import type {
  listProjects,
  openProject,
  ProjectDesignRequest,
  reviseProject,
  SavedProject,
  SavedRequest,
  V2ModelSummary,
  validateConfiguration,
} from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { LINK_MODEL, row } from './feeder-link-fixtures';
import { renderApp } from './helpers';

/**
 * M7b: the guided configuration editor, its one Save, reopening a revision's
 * stored configuration, conflicts and their recovery, and failed saves that
 * keep every input. All synthetic data: refs, markings and designations are
 * illustrative labels, not engineering values.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  validate: vi.fn<typeof validateConfiguration>(),
  revise: vi.fn<typeof reviseProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    openProject: api.open,
    validateConfiguration: api.validate,
    reviseProject: api.revise,
  };
});

const c = en.configuration;

/** For an item a test has just put there: its absence fails the test. */
function missing(): never {
  throw new Error('expected an item that is not there');
}

function nth<T>(items: readonly (T | undefined)[], index: number): T {
  return items.at(index) ?? missing();
}

const TEMPLATE = {
  ref: 'tenant/synth-relay-with-a-rather-long-reference-name/v1',
  function: 'relay',
  terminals: [{ id: 'a1', marking: 'A1', kind: 'coil', required: true, group: null }],
  contact_groups: [],
  parameters: [],
  provenance: { source: 'synthetic test data', author: 'tests', created: '2026-11-10' },
  review: { status: 'unreviewed', reviewer: null, reviewed_at: null, standards: [] },
  restricted_scope: [],
};

const PATTERN = {
  ref: 'tenant/synth-pattern/v1',
  slots: [],
  strips: ['out'],
  points: [
    { id: 'l', strip: 'out', offset: 1, level: 1, kind: 'power_line' },
    { id: 'n', strip: 'out', offset: 2, level: 1, kind: 'neutral' },
  ],
  nets: [],
  provenance: { source: 'synthetic test data' },
  review: { status: 'unreviewed' },
};

const STORED = {
  name: 'synthetic',
  templates: [TEMPLATE],
  patterns: [PATTERN],
  strips: [
    {
      id: 'X1',
      board: 'MDB',
      designation: { function: null, location: null, product: 'X1' },
      template: null,
    },
  ],
  components: [
    {
      id: 'k1',
      board: 'MDB',
      template: TEMPLATE.ref,
      designation: { function: null, location: null, product: 'K1' },
    },
  ],
  bindings: [
    {
      id: 'b1',
      pattern: PATTERN.ref,
      board: 'MDB',
      circuit: 'c1',
      slots: {},
      strips: { out: 'X1' },
      markings: { l: 'L1' },
      cable: null,
      wires: {},
    },
  ],
};

const REGISTRY_MODEL = {
  definitions: [
    {
      ref: 'global/synth-bar/v1',
      kind: 'template',
      review_status: null,
      provenance_source: null,
      restricted_scope: [],
    },
    {
      ref: 'tenant/synth-pattern/v1',
      kind: 'pattern',
      review_status: 'unreviewed',
      provenance_source: null,
      restricted_scope: [],
    },
  ],
  components: [],
  strips: [],
  terminals: [],
  nets: [],
  connections: [],
  wires: [],
  findings: [],
} as unknown as V2ModelSummary;

const REQUEST = { info: { name: 'Tower' }, boards: [] } as unknown as ProjectDesignRequest;

type PanelProps = Parameters<typeof ConfigurationPanel>[0];

function Harness(props: Partial<PanelProps> & { initial?: Record<string, unknown> }) {
  const { initial, ...rest } = props;
  const [value, setValue] = useState<Record<string, unknown>>(initial ?? {});
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={1}
      request={REQUEST}
      note=""
      value={value}
      onChange={setValue}
      status="none"
      boards={['MDB', 'DB1']}
      model={REGISTRY_MODEL}
      onSaved={vi.fn()}
      onUnauthorized={vi.fn()}
      validateImpl={api.validate}
      reviseImpl={api.revise}
      {...rest}
    />
  );
}

beforeEach(() => {
  api.validate.mockReset();
  api.revise.mockReset();
  api.list.mockReset();
  api.open.mockReset();
});

/* ------------------------------------------------------------- the model --- */

describe('the configuration model', () => {
  it('offers only templates present in the configuration or the registry, with review and provenance as stored', () => {
    const choices = templateChoices(STORED, REGISTRY_MODEL);
    expect(choices).toEqual([
      {
        ref: TEMPLATE.ref,
        function: 'relay',
        reviewStatus: 'unreviewed',
        provenance: 'synthetic test data',
        source: 'configuration',
      },
      {
        ref: 'global/synth-bar/v1',
        function: null,
        reviewStatus: null,
        provenance: null,
        source: 'registry',
      },
    ]);
    // A pattern is not a template, and nothing is offered from nowhere.
    expect(templateChoices({}, null)).toEqual([]);
  });

  it('adds a component and a strip with only what was typed: no marking, rating or value', () => {
    let doc = addComponent(STORED, { board: 'DB1', template: TEMPLATE.ref, designation: ' K2 ' });
    doc = addStrip(doc, { board: 'DB1', template: null, designation: '' });
    const components = doc.components as Record<string, unknown>[];
    const strips = doc.strips as Record<string, unknown>[];
    expect(components.at(-1)).toEqual({
      id: 'component-1',
      board: 'DB1',
      template: TEMPLATE.ref,
      designation: { function: null, location: null, product: 'K2' },
    });
    expect(strips.at(-1)).toEqual({
      id: 'strip-1',
      board: 'DB1',
      template: null,
      designation: null,
    });
    // Everything else is carried untouched.
    expect(doc.templates).toBe(STORED.templates);
    expect(doc.name).toBe('synthetic');
  });

  it('edits a binding’s point markings, leaving a blank one unset', () => {
    const [binding] = bindingsOf(STORED);
    expect(bindingPoints(STORED, nth([binding], 0)).map((p) => [p.id, p.marking])).toEqual([
      ['l', 'L1'],
      ['n', ''],
    ]);
    let doc = setMarking(STORED, 'b1', 'n', 'N1');
    expect(nth(bindingsOf(doc), 0).markings).toEqual({ l: 'L1', n: 'N1' });
    doc = setMarking(doc, 'b1', 'l', '');
    expect(nth(bindingsOf(doc), 0).markings).toEqual({ n: 'N1' });
  });

  it('reads what a conflict names', () => {
    expect(
      conflictTarget({ ref: 'c9', kind: 'circuit', binding: 'b1', reason: 'circuit_missing' }),
    ).toEqual({
      kind: 'binding',
      entry: 'b1',
      ref: 'c9',
      refKind: 'circuit',
      reason: 'circuit_missing',
    });
    expect(conflictTarget({ ref: 'x', kind: 'terminal', link: 'DB1', reason: 'gone' })?.kind).toBe(
      'link',
    );
    expect(conflictTarget(undefined)).toBeNull();
  });

  it('sends the configuration, its explicit removal, or nothing', () => {
    expect(configurationPayload(STORED, 'stored')).toEqual({ configuration: STORED });
    expect(configurationPayload({ templates: [] }, 'stored')).toEqual({
      configurationRemoved: true,
    });
    expect(configurationPayload({}, 'none')).toEqual({});
  });
});

/* ------------------------------------------------------------ the editor --- */

describe('the guided editor', () => {
  it('adds a component from an available template, flagged unreviewed, without any JSON', () => {
    renderApp(<Harness initial={{ templates: [TEMPLATE] }} />);
    const add = within(screen.getByTestId('add-component'));
    const submit = add.getByTestId<HTMLButtonElement>('add-component-submit');
    // A component needs a template chosen; none is preselected.
    expect(submit.disabled).toBe(true);
    const picker = within(add.getByTestId('add-component-template'));
    expect(picker.getAllByTestId('review-unreviewed')).toHaveLength(1);
    // The registry template states no review: it stays flagged as unknown.
    expect(picker.getAllByTestId('review-unknown')).toHaveLength(1);
    expect(picker.getByText(c.provenanceMissing)).toBeTruthy();
    fireEvent.change(add.getByTestId('add-component-board'), { target: { value: 'DB1' } });
    fireEvent.click(picker.getByLabelText(new RegExp('synth-bar')));
    fireEvent.change(add.getByTestId('add-component-designation'), { target: { value: 'W1' } });
    fireEvent.click(submit);
    const entry = within(screen.getByTestId('component-entry'));
    expect(entry.getByTestId<HTMLInputElement>('component-designation').value).toBe('W1');
    expect(entry.getByText('global/synth-bar/v1')).toBeTruthy();
    expect(entry.getByTestId('review-unknown')).toBeTruthy();
    expect(screen.queryByText('configuration-json')).toBeNull();
  });

  it('adds a terminal strip with no template, and offers no templates where none exist', () => {
    renderApp(<Harness model={null} />);
    expect(screen.getByTestId('add-strip-template-none').textContent).toBe(c.editor.noTemplates);
    const add = within(screen.getByTestId('add-strip'));
    fireEvent.change(add.getByTestId('add-strip-designation'), { target: { value: 'X9' } });
    fireEvent.click(add.getByTestId('add-strip-submit'));
    const entry = within(screen.getByTestId('strip-entry'));
    expect(entry.getByText(c.editor.noTemplate)).toBeTruthy();
    expect(entry.getByTestId<HTMLInputElement>('strip-designation').value).toBe('X9');
  });

  it('edits a point marking and removes an entry, by keyboard-operable controls', () => {
    renderApp(<Harness initial={STORED} />);
    const markings = screen.getAllByTestId<HTMLInputElement>('point-marking');
    expect(markings.map((input) => input.value)).toEqual(['L1', '']);
    // Each control has an accessible name.
    expect(screen.getByLabelText(/Marking of point n/)).toBe(markings[1]);
    fireEvent.change(nth(markings, 1), { target: { value: 'N1' } });
    expect(nth(screen.getAllByTestId<HTMLInputElement>('point-marking'), 1).value).toBe('N1');
    const remove = screen.getByTestId('strip-remove');
    expect(remove.tagName).toBe('BUTTON');
    fireEvent.click(remove);
    expect(screen.queryByTestId('strip-entry')).toBeNull();
  });
});

/* -------------------------------------------------------------- one Save --- */

const SAVED_PROJECT = {
  id: 'p1',
  revision: 2,
  revisions: [],
  request: {},
} as unknown as SavedProject;

describe('one Save', () => {
  it('is the only save, and sends the form request plus the configuration as edited', async () => {
    api.revise.mockResolvedValue({ kind: 'saved', project: SAVED_PROJECT });
    renderApp(<Harness initial={STORED} status="stored" note="n" />);
    expect(screen.getAllByRole('button', { name: c.save })).toHaveLength(1);
    // Checking without saving is only an advanced option.
    expect(
      screen
        .getByTestId('configuration-advanced')
        .contains(screen.getByTestId('configuration-validate')),
    ).toBe(true);
    fireEvent.change(nth(screen.getAllByTestId('point-marking'), 1), {
      target: { value: 'N1' },
    });
    fireEvent.click(screen.getByTestId('configuration-save'));
    await screen.findByTestId('configuration-saved');
    const sent = nth(api.revise.mock.calls, 0)[0];
    expect(sent.request).toBe(REQUEST);
    expect(sent.note).toBe('n');
    expect(nth((sent.configuration as typeof STORED).bindings, 0).markings).toEqual({
      l: 'L1',
      n: 'N1',
    });
  });

  it('asks to remove a stored configuration once the editor is emptied', async () => {
    api.revise.mockResolvedValue({ kind: 'saved', project: SAVED_PROJECT });
    renderApp(<Harness initial={{ components: STORED.components }} status="stored" model={null} />);
    fireEvent.click(screen.getByTestId('component-remove'));
    fireEvent.click(screen.getByTestId('configuration-save'));
    await screen.findByTestId('configuration-saved');
    expect(nth(api.revise.mock.calls, 0)[0]).toMatchObject({
      configurationRemoved: true,
    });
  });
});

/* ----------------------------------------------- refusals keep the inputs --- */

describe('a refused or failed save', () => {
  it('names the conflicting reference and removes the binding as recovery', async () => {
    api.revise.mockResolvedValue({
      kind: 'refused',
      detail: 'English detail',
      code: 'configuration_conflict',
      params: { ref: 'c1', kind: 'circuit', binding: 'b1', reason: 'circuit_missing' },
    });
    renderApp(<Harness initial={STORED} status="stored" />);
    fireEvent.click(screen.getByTestId('configuration-save'));
    const error = await screen.findByTestId('configuration-error');
    expect(error.textContent).toContain('binding b1 refers to circuit c1');
    expect(error.textContent).toContain('circuit_missing');
    expect(error.textContent).not.toContain('English detail');
    // Everything entered is still there, and the binding at fault is marked.
    expect(screen.getByTestId('binding-entry').getAttribute('data-conflict')).toBe('true');
    expect(nth(screen.getAllByTestId<HTMLInputElement>('point-marking'), 0).value).toBe('L1');
    fireEvent.click(screen.getByTestId('conflict-edit'));
    expect(document.activeElement).toBe(screen.getByTestId('binding-entry'));
    fireEvent.click(screen.getByTestId('conflict-remove'));
    expect(screen.queryByTestId('binding-entry')).toBeNull();
    expect(screen.getByTestId('component-entry')).toBeTruthy();
  });

  it('points a link conflict to the board’s feeder link', async () => {
    api.revise.mockResolvedValue({
      kind: 'refused',
      detail: 'x',
      code: 'configuration_conflict',
      params: { ref: 'pt-9', kind: 'terminal', link: 'DB1', reason: 'terminal_missing' },
    });
    renderApp(<Harness initial={STORED} />);
    fireEvent.click(screen.getByTestId('configuration-save'));
    expect((await screen.findByTestId('conflict-link')).textContent).toBe(c.conflict.link);
    expect(screen.queryByTestId('conflict-remove')).toBeNull();
  });

  it('keeps every input after a save that did not complete, or a not-recoverable refusal', async () => {
    api.revise.mockResolvedValueOnce({ kind: 'failed' });
    api.revise.mockResolvedValueOnce({
      kind: 'refused',
      detail: 'x',
      code: 'configuration_not_recoverable',
      params: { revision: '3' },
    });
    const onSaved = vi.fn();
    renderApp(<Harness initial={STORED} onSaved={onSaved} />);
    fireEvent.change(screen.getByTestId('component-designation'), { target: { value: 'K7' } });
    fireEvent.click(screen.getByTestId('configuration-save'));
    expect((await screen.findByTestId('configuration-error')).textContent).toBe(c.failed);
    expect(screen.getByTestId<HTMLInputElement>('component-designation').value).toBe('K7');
    fireEvent.click(screen.getByTestId('configuration-save'));
    await waitFor(() => {
      expect(screen.getByTestId('configuration-error').textContent).toContain('revision 3');
    });
    expect(screen.getByTestId<HTMLInputElement>('component-designation').value).toBe('K7');
    expect(nth(screen.getAllByTestId<HTMLInputElement>('point-marking'), 0).value).toBe('L1');
    expect(onSaved).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------- reopening a revision --- */

const SAVED_REQUEST = {
  info: { name: 'Stored tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [
        {
          description: 'Lights',
          load: 'lighting',
          power_kw: '1',
          phases: 1,
          power_factor: null,
          controlled: false,
          starter: null,
          length_m: null,
        },
      ],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function stored(configuration: Record<string, unknown> | null, status: string): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
    revision: 1,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision'],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-11-10T10:00:00Z',
        validated: true,
        export_status: 'draft',
      },
    ],
    request: SAVED_REQUEST,
    findings: [],
    model_v2: null,
    snapshot_schema: 'pp-design/2',
    configuration,
    configuration_status: status,
  } as unknown as SavedProject;
}

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

async function openScreen(project: SavedProject) {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision'],
      projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
    },
  } as never);
  api.open.mockResolvedValue({ kind: 'saved', project });
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      saveImpl={vi.fn()}
    />,
  );
  const listed = await screen.findByTestId('project-p1');
  fireEvent.click(within(listed).getByText('Open'));
  const view = within(screen.getByTestId('view-configuration'));
  await view.findByTestId('configuration-editor');
  return view;
}

describe('reopening a revision', () => {
  it('loads its stored configuration into the editor', async () => {
    const view = await openScreen(stored(STORED, 'stored'));
    await waitFor(() => {
      expect(view.getByTestId<HTMLInputElement>('component-designation').value).toBe('K1');
    });
    expect(view.getByTestId<HTMLInputElement>('strip-designation').value).toBe('X1');
    expect(nth(view.getAllByTestId<HTMLInputElement>('point-marking'), 0).value).toBe('L1');
    expect(view.queryByTestId('configuration-not-recoverable')).toBeNull();
  });

  it('says a configuration that cannot be carried must be entered again', async () => {
    const view = await openScreen(stored(null, 'not_recoverable'));
    expect(view.getByTestId('configuration-not-recoverable')).toBeTruthy();
    expect(view.queryByTestId('component-entry')).toBeNull();
  });

  it('stores an edited configuration with the project’s own Save too', async () => {
    api.revise.mockResolvedValue({ kind: 'saved', project: stored(STORED, 'stored') });
    const view = await openScreen(stored(STORED, 'stored'));
    await waitFor(() => {
      expect(view.getByTestId<HTMLInputElement>('component-designation').value).toBe('K1');
    });
    fireEvent.change(view.getByTestId('component-designation'), { target: { value: 'K5' } });
    const save = within(screen.getByTestId('view-overview')).getByRole('button', {
      name: en.design.projects.saveRevision,
    });
    fireEvent.click(save);
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalled();
    });
    const sent = nth(api.revise.mock.calls, 0)[0];
    expect(nth((sent.configuration as typeof STORED).components, 0).designation).toEqual({
      function: null,
      location: null,
      product: 'K5',
    });
  });
});

/* ------------------------------------------------- feeder-link endpoints --- */

describe('feeder-link endpoints', () => {
  it('are said by designation and board name, never by raw id', () => {
    const model = {
      ...LINK_MODEL,
      terminals: [
        ...LINK_MODEL.terminals,
        { ...row('MDB', 'component', 'cmp-raw', 'x', 't-raw', '7', null), designation: null },
      ],
    };
    const texts = terminalOptions(model, 'MDB', 'Unnamed').map((option) => option.text);
    expect(texts).toEqual(['Q1:2 — MDB', 'X0:1 (outer) — MDB', 'Unnamed:7 — MDB']);
    expect(texts.join(' ')).not.toMatch(/cmp-|t-raw|pt-/);
  });
});

/* ----------------------------------------------------------------- i18n --- */

function keys(value: unknown, prefix = ''): string[] {
  if (typeof value !== 'object' || value === null) return [prefix];
  return Object.entries(value).flatMap(([key, inner]) =>
    keys(inner, prefix ? `${prefix}.${key}` : key),
  );
}

describe('messages', () => {
  it.each([
    ['ar', ar],
    ['he', he],
  ] as const)('has every configuration and feeder-link key in %s', (_locale, messages) => {
    for (const section of ['configuration', 'feederLink'] as const) {
      expect(keys(messages[section]).sort()).toEqual(keys(en[section]).sort());
    }
    expect(messages.configuration.editor.addComponent).not.toBe(
      en.configuration.editor.addComponent,
    );
    expect(messages.configuration.refusal.configuration_conflict).toContain('{ref}');
  });
});
