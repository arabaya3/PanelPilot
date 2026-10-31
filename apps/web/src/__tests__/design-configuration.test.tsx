import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { ConfigurationPanel, parseConfiguration } from '@/components/design/configuration-panel';
import { groupOf } from '@/components/design/configuration-review';
import type * as DesignModule from '@/lib/design';
import type {
  ConfigurationReview,
  exportRevision,
  listProjects,
  openProject,
  ProjectDesignRequest,
  reviseProject,
  SavedProject,
  SavedRequest,
  V2Finding,
  V2ModelSummary,
  validateConfiguration,
} from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * The configuration view: load a configuration, validate it (nothing saved),
 * review what it composes to with every value's source, save it as a
 * configured revision or be told why not, and the review schedules of an
 * opened v2 revision. All synthetic data.
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

function finding(code: string, fields: Partial<V2Finding> = {}): V2Finding {
  return {
    code,
    severity: 'warning',
    message: `${code} message`,
    location: { board_id: 'DB1' },
    unresolved: false,
    blocks_approval: false,
    blocks_execution: false,
    requires_engineering_confirmation: false,
    ...fields,
  };
}

const MODEL: V2ModelSummary = {
  definitions: [
    {
      ref: 'tenant/synth-relay/v1',
      kind: 'template',
      review_status: 'unreviewed',
      provenance_source: 'synthetic test data',
      restricted_scope: [],
    },
    {
      ref: 'tenant/synth-pattern/v1',
      kind: 'pattern',
      review_status: 'rejected',
      provenance_source: null,
      restricted_scope: [],
    },
  ],
  components: [
    {
      id: 'cmp-1',
      board_id: 'DB1',
      designation: 'K1',
      template_ref: 'tenant/synth-relay/v1',
      legacy_device_id: null,
      pattern_refs: ['tenant/synth-pattern/v1'],
      source: 'configuration',
    },
  ],
  strips: [
    {
      id: 'str-1',
      board_id: 'DB1',
      designation: 'X1',
      template_ref: null,
      points: [
        { id: 'pt-1', position: 1, level: 1, marking: '1', kind: 'feed', pattern_ref: null },
      ],
    },
  ],
  nets: [
    {
      id: 'net-1',
      board_id: 'DB1',
      signal: 'L1',
      origin: 'pattern',
      members: ['K1:A1', 'X1:1'],
      pattern_refs: [],
    },
  ],
  connections: [
    {
      id: 'con-1',
      net_id: 'net-1',
      start: 'K1:A1',
      end: 'X1:1',
      via: 'wire',
      cable_core: null,
      pattern_ref: null,
    },
  ],
  wires: [
    {
      board_id: 'DB1',
      wire_id: 'wir-1',
      connection_id: 'con-1',
      start: 'K1:A1',
      end: 'X1:1',
      net_id: 'net-1',
      signal: 'L1',
      number: { value: 'W-7', source: 'user' },
      cross_section_mm2: {
        value: '1.5',
        source: 'assumption',
        assumption_id: 'asm-1',
        assumption_statement: 'Synthetic placeholder pending a rule',
      },
      colour: { value: null, source: 'unresolved' },
      pattern_ref: null,
      flags: ['wire_attribute_unset'],
    },
  ],
  terminals: [
    {
      board_id: 'DB1',
      owner_kind: 'component',
      owner_id: 'cmp-1',
      designation: 'K1',
      terminal_id: 'A1',
      marking: 'A1',
      side: null,
      position: null,
      level: null,
      kind: 'coil',
      required: { value: null, source: 'unresolved' },
      net_id: 'net-1',
      signal: 'L1',
      connected_to: ['X1:1'],
      template_ref: 'tenant/synth-relay/v1',
      pattern_refs: [],
      net_origin: 'pattern',
      flags: ['terminal_requiredness_unresolved'],
    },
    {
      board_id: 'DB1',
      owner_kind: 'component',
      owner_id: 'cmp-1',
      designation: 'K1',
      terminal_id: 'A2',
      marking: 'A2',
      side: null,
      position: null,
      level: null,
      kind: 'coil',
      required: { value: 'required', source: 'data' },
      net_id: null,
      signal: null,
      connected_to: [],
      template_ref: 'tenant/synth-relay/v1',
      pattern_refs: [],
      net_origin: null,
      flags: [],
    },
  ],
  findings: [
    finding('unconnected_required_terminal', {
      severity: 'blocking',
      blocks_approval: true,
    }),
    finding('template_unreviewed', {
      blocks_execution: true,
      requires_engineering_confirmation: true,
    }),
    finding('wire_attribute_assumed', { requires_engineering_confirmation: true }),
    finding('wire_attribute_unset', { unresolved: true }),
    finding('terminal_requiredness_unresolved', {
      unresolved: true,
      requires_engineering_confirmation: true,
    }),
  ],
};

const REVIEW: ConfigurationReview = {
  based_on_revision: 1,
  snapshot_schema: 'pp-design/2',
  written_schema: 'pp-design/2',
  saveable: true,
  snapshot_sha256: 'b'.repeat(64),
  findings: [],
  model: MODEL,
};

const REQUEST = { info: { name: 'Tower' }, boards: [] } as unknown as ProjectDesignRequest;

function renderPanel(
  props: Partial<Parameters<typeof ConfigurationPanel>[0]> = {},
  locale?: 'en' | 'ar' | 'he',
) {
  const onSaved = vi.fn();
  renderApp(
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={1}
      request={REQUEST}
      note=""
      dirty={false}
      onSaved={onSaved}
      onUnauthorized={vi.fn()}
      validateImpl={api.validate}
      reviseImpl={api.revise}
      {...props}
    />,
    locale ? { locale } : {},
  );
  return { onSaved };
}

function enter(text = '{"templates": []}') {
  fireEvent.change(screen.getByTestId('configuration-json'), { target: { value: text } });
}

beforeEach(() => {
  api.validate.mockReset();
  api.revise.mockReset();
});

describe('a configuration as text', () => {
  it('is an object, or empty, or invalid', () => {
    expect(parseConfiguration('')).toBe('empty');
    expect(parseConfiguration('[1]')).toBe('invalid');
    expect(parseConfiguration('{')).toBe('invalid');
    expect(parseConfiguration('{"a": 1}')).toEqual({ a: 1 });
  });
});

describe('validating a configuration', () => {
  it('sends it for a dry run and shows every table with each value’s source', async () => {
    api.validate.mockResolvedValue({ kind: 'reviewed', review: REVIEW });
    renderPanel();
    expect(screen.getByTestId('validation-scope').textContent).toContain(
      'None of this establishes electrical correctness',
    );
    enter();
    fireEvent.click(screen.getByTestId('configuration-validate'));
    await screen.findByTestId('configuration-review');
    expect(api.validate).toHaveBeenCalledWith({
      token: 't',
      id: 'p1',
      configuration: { templates: [] },
    });
    expect(api.revise).not.toHaveBeenCalled();

    for (const table of [
      'definitions',
      'components',
      'strips',
      'terminals',
      'nets',
      'connections',
      'wires',
    ]) {
      expect(screen.getByTestId(`table-${table}`)).toBeTruthy();
    }
    const definitions = within(screen.getByTestId('table-definitions'));
    expect(definitions.getByText(c.review.unreviewed)).toBeTruthy();
    expect(definitions.getByText(c.review.rejected)).toBeTruthy();
    expect(definitions.getByText('synthetic test data')).toBeTruthy();
    expect(definitions.getByText(c.provenanceMissing)).toBeTruthy();

    const wires = within(screen.getByTestId('table-wires'));
    expect(wires.getByText(c.source.user)).toBeTruthy();
    expect(wires.getByText(c.source.assumption)).toBeTruthy();
    expect(wires.getByText(c.source.unresolved)).toBeTruthy();
    expect(wires.getByTestId('assumption-statement').textContent).toContain(
      'Synthetic placeholder pending a rule',
    );
    expect(wires.getByTestId('assumption-statement').textContent).toContain('asm-1');
    expect(
      within(screen.getByTestId('table-components')).getByText(c.source.configuration),
    ).toBeTruthy();

    const terminals = within(screen.getByTestId('table-terminals'));
    expect(terminals.getByTestId('required-unresolved').textContent).toBe(c.required.unresolved);
    expect(terminals.getByTestId('required-required').textContent).toBe(c.required.required);

    expect(screen.getByTestId('definitions-await-review').textContent).toContain(
      'qualified electrical engineer designated by the owner',
    );
  });

  it('groups the structural findings by effect, with codes and messages', async () => {
    api.validate.mockResolvedValue({ kind: 'reviewed', review: REVIEW });
    renderPanel();
    enter();
    fireEvent.click(screen.getByTestId('configuration-validate'));
    await screen.findByTestId('configuration-review');
    const group = (key: string) => screen.getByTestId(`findings-${key}`).textContent;
    expect(group('blocksApproval')).toContain('unconnected_required_terminal');
    expect(group('blocksApproval')).toContain('unconnected_required_terminal message');
    expect(group('blocksExecution')).toContain('template_unreviewed');
    expect(group('needsConfirmation')).toContain('wire_attribute_assumed');
    expect(group('unresolved')).toContain('wire_attribute_unset');
    expect(group('unresolved')).toContain('terminal_requiredness_unresolved');
    expect(group('blocksApproval')).not.toContain('template_unreviewed');
    expect(groupOf(finding('x', { blocks_approval: true, blocks_execution: true }))).toBe(
      'blocksApproval',
    );
  });

  it('claims no approval, compliance or execution readiness anywhere', async () => {
    api.validate.mockResolvedValue({
      kind: 'reviewed',
      review: {
        ...REVIEW,
        model: {
          ...MODEL,
          definitions: [
            ...MODEL.definitions,
            { ref: 'global/legacy-x/v1', kind: 'template', review_status: 'approved' },
          ],
        },
      },
    });
    renderPanel();
    enter();
    fireEvent.click(screen.getByTestId('configuration-validate'));
    const panel = await screen.findByTestId('configuration-panel');
    await screen.findByTestId('configuration-review');
    const text = panel.textContent;
    // The scope note names what validation does not decide; everything else
    // must not affirm an approval, a compliance or execution readiness.
    const scope = screen.getByTestId('validation-scope').textContent;
    expect(text.replace(scope, '')).not.toMatch(
      /approved by|certified|compliant|complies|ready for execution/i,
    );
    expect(text).toContain(c.review.approved);
    expect(text).toContain('No status shown here is an engineering approval');
  });

  it('keeps codes and refs left to right in Arabic', async () => {
    api.validate.mockResolvedValue({ kind: 'reviewed', review: REVIEW });
    renderPanel({}, 'ar');
    enter();
    fireEvent.click(screen.getAllByTestId('configuration-validate')[0] as HTMLElement);
    await screen.findByTestId('configuration-review');
    const ref = screen.getAllByText('tenant/synth-relay/v1')[0];
    expect(ref?.tagName).toBe('BDI');
    expect(ref?.getAttribute('dir')).toBe('ltr');
  });
});

describe('saving a configured revision', () => {
  it('sends the configuration with the request and note', async () => {
    const project = {
      id: 'p1',
      revision: 2,
      revisions: [],
      request: {},
    } as unknown as SavedProject;
    api.revise.mockResolvedValue({ kind: 'saved', project });
    const { onSaved } = renderPanel({ note: 'configured' });
    enter();
    fireEvent.click(screen.getByTestId('configuration-save'));
    await screen.findByTestId('configuration-saved');
    expect(api.revise).toHaveBeenCalledWith({
      token: 't',
      id: 'p1',
      request: REQUEST,
      note: 'configured',
      configuration: { templates: [] },
    });
    expect(onSaved).toHaveBeenCalledWith(project);
  });

  it('says configured revisions are disabled here, and keeps validation available', async () => {
    api.revise.mockResolvedValue({
      kind: 'refused',
      detail: 'v1',
      code: 'configuration_requires_v2',
      params: { written_schema: 'pp-design/1' },
    });
    renderPanel();
    enter();
    fireEvent.click(screen.getByTestId('configuration-save'));
    const notice = await screen.findByTestId('configuration-disabled');
    expect(notice.textContent).toContain('Configured revisions are disabled in this environment');
    expect(notice.textContent).toContain('pp-design/1');
    expect(screen.queryByTestId('configuration-error')).toBeNull();
    expect(screen.getByTestId<HTMLButtonElement>('configuration-validate').disabled).toBe(false);
  });

  it('is held while the form differs from the saved revision', () => {
    renderPanel({ dirty: true });
    enter();
    expect(screen.getByTestId<HTMLButtonElement>('configuration-save').disabled).toBe(true);
    expect(screen.getByTestId('configuration-dirty')).toBeTruthy();
  });

  it.each([
    [
      'configuration_claims_review',
      { kind: 'template', ref: 'tenant/x/v1', fields: 'reviewer,status' },
      'claims a review for tenant/x/v1 (reviewer,status)',
    ],
    [
      'configuration_ref_reserved',
      { kind: 'template', ref: 'global/legacy-x/v1' },
      'The definition global/legacy-x/v1 uses a reserved ref',
    ],
    [
      'configuration_refused',
      { refusal: 'restricted_scope' },
      'cannot be composed as sent (restricted_scope)',
    ],
    [
      'configuration_too_large',
      { limit_of: 'depth', limit: '12' },
      'too large: depth is over its limit of 12',
    ],
  ])('says %s in its own sentence', async (code, params, sentence) => {
    api.validate.mockResolvedValue({ kind: 'refused', detail: 'English detail', code, params });
    renderPanel();
    enter();
    fireEvent.click(screen.getByTestId('configuration-validate'));
    const error = await screen.findByTestId('configuration-error');
    expect(error.textContent).toContain(sentence);
    expect(error.textContent).not.toContain('English detail');
  });
});

/* ------------------------------------------------- the workspace screen --- */

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

function saved(model: V2ModelSummary | null): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
    revision: 1,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-10-02T10:00:00Z',
        validated: true,
        export_status: 'draft',
      },
    ],
    request: SAVED_REQUEST,
    findings: [],
    model_v2: model,
  } as unknown as SavedProject;
}

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function renderScreen(exportRevisionImpl = vi.fn<typeof exportRevision>()) {
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      exportRevisionImpl={exportRevisionImpl}
      saveImpl={vi.fn()}
    />,
  );
  return exportRevisionImpl;
}

async function openStored() {
  const row = await screen.findByTestId('project-p1');
  fireEvent.click(within(row).getByText('Open'));
  const overview = within(screen.getByTestId('view-overview'));
  await waitFor(() => {
    expect(overview.getByLabelText<HTMLInputElement>('Project name').value).toBe('Stored tower');
  });
}

const review = () => within(screen.getByTestId('view-review'));
const configuration = () => within(screen.getByTestId('view-configuration'));

describe('the configuration view and review schedules of a revision', () => {
  beforeEach(() => {
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
        projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
      },
    });
  });

  it('asks for a saved project first', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved(null) });
    renderScreen();
    await screen.findByTestId('project-p1');
    expect(configuration().getByTestId('configuration-not-saved').textContent).toContain(
      c.notSaved,
    );
  });

  it('offers both schedules of a v2 revision as review documents, not for execution', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved(MODEL) });
    const exportRevisionImpl = renderScreen(
      vi
        .fn<typeof exportRevision>()
        .mockResolvedValue({ kind: 'exported', blob: new Blob(['x']), filename: 's.pdf' }),
    );
    await openStored();
    const schedules = within(review().getByTestId('revision-schedules'));
    expect(schedules.getByText(en.design.exports.schedulesTitle)).toBeTruthy();
    expect(review().getByTestId('revision-schedules').textContent).toContain('NOT FOR EXECUTION');
    fireEvent.click(schedules.getByTestId('revision-export-terminal_schedule_pdf'));
    await waitFor(() => {
      expect(exportRevisionImpl).toHaveBeenCalledWith(
        expect.objectContaining({ format: 'terminal_schedule_pdf', purpose: 'review' }),
      );
    });
    expect(schedules.getByTestId('revision-export-wire_schedule_pdf')).toBeTruthy();
    // The execution export is still offered only as the blocked request it was.
    expect(review().getByTestId('execution-export')).toBeTruthy();

    // The configuration view shows the revision's model read-only.
    const opened = within(configuration().getByTestId('opened-configuration'));
    expect(opened.getByTestId('table-wires')).toBeTruthy();
    expect(opened.queryByTestId('configuration-json')).toBeNull();
    expect(configuration().getByTestId('configuration-panel')).toBeTruthy();
  });

  it('offers no schedules for a v1 revision, and says why', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved(null) });
    renderScreen();
    await openStored();
    expect(review().queryByTestId('revision-export-terminal_schedule_pdf')).toBeNull();
    expect(review().queryByTestId('revision-export-wire_schedule_pdf')).toBeNull();
    expect(review().getByTestId('revision-schedules-v1').textContent).toBe(
      en.design.errors.schedule_requires_v2,
    );
    expect(configuration().getByTestId('opened-configuration-v1')).toBeTruthy();
  });
});
