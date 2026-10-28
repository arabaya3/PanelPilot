import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import {
  executionBlockersOf,
  executionRefusalOf,
  readinessOf,
  rowOf,
  validationOf,
  type RevisionValidation,
} from '@/components/design/readiness';
import {
  exportRevision,
  isUnprotected,
  UNPROTECTED_FORMATS,
  tooLargeOf,
  tooLongOf,
  unknownFieldsOf,
  type approveRevision,
  type BoardDesignResponse,
  type designProject,
  type exportDesign,
  type listProjects,
  type openProject,
  type SavedProject,
  type SavedRequest,
} from '@/lib/design';

import type * as DesignModule from '@/lib/design';

import { MESSAGES, asSignedIn, renderApp } from './helpers';

/**
 * Phase 1, trustworthy export and approval, as the page shows it: a saved
 * revision's way from saved to validated to review approved (execution ready
 * always blocked), its findings and where to fix them, draft and revision
 * exports kept apart, and every refusal said so the engineer can act on it.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  approve: vi.fn<typeof approveRevision>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    openProject: api.open,
    approveRevision: api.approve,
  };
});

const SHA = 'a'.repeat(64);

const UNAVAILABLE =
  'Not available yet: this format cannot keep a visible draft warning once imported. Use the PDF or DXF for review.';

const REQUEST = {
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
        {
          description: 'Heater',
          load: 'heating',
          power_kw: '2',
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

const BLOCKING = {
  code: 'integrity_device_above_cable_capacity',
  severity: 'blocking',
  board_id: 'MDB',
  circuit_id: 'c2',
  device_id: 'c2-breaker',
  values: { rated: '200', capacity: '17.5', cable: '3G1.5', basis: 'method C' },
  message: 'Heater: 200 A protection is above the 17.5 A its 3G1.5 cable carries.',
  requires_engineering_confirmation: false,
  source: 'IEC 60364-5-52',
};

const WARNING = {
  code: 'rules_unconfirmed',
  severity: 'warning',
  board_id: 'MDB',
  circuit_id: null,
  device_id: null,
  values: {},
  message: 'Circuit rules are this software’s defaults.',
  requires_engineering_confirmation: true,
  source: 'policy',
};

function saved(fields: Record<string, unknown> = {}): SavedProject {
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
    request: REQUEST,
    ...({
      validated: true,
      findings: [],
      snapshot_sha256: SHA,
      rules_version: 'r1',
      ...fields,
    } as object),
  };
}

function approved(fields: Record<string, unknown> = {}): SavedProject {
  const project = saved({ approved_snapshot_sha256: SHA, ...fields });
  project.revisions = project.revisions.map((entry) => ({
    ...entry,
    approved_by: 'A. Engineer',
    approver_name: 'A. Engineer',
    approver_account_recorded: true,
    approved_at: '2026-10-03T10:00:00Z',
  }));
  return project;
}

const DESIGNED = {
  project: {
    info: { name: 'Tower' },
    profile: 'iec-default',
    parts: [],
    boards: [{ id: 'MDB', name: 'MDB', devices: [], cables: [], circuits: [], notes: [] }],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function renderScreen(
  extra: {
    exportRevisionImpl?: typeof exportRevision;
    exportImpl?: typeof exportDesign;
    designImpl?: typeof designProject;
    saveImpl?: (blob: Blob, filename: string) => void;
    /** Render as a signed-in account (approving needs one), not a trial. */
    account?: boolean;
  } = {},
) {
  const { account = false, ...impls } = extra;
  const screenUi = (
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      {...impls}
    />
  );
  renderApp(account ? asSignedIn(screenUi) : screenUi);
}

const review = () => within(screen.getByTestId('view-review'));
const overview = () => within(screen.getByTestId('view-overview'));

async function openStored() {
  const row = await screen.findByTestId('project-p1');
  fireEvent.click(within(row).getByText('Open'));
  await waitFor(() => {
    expect(overview().getByLabelText<HTMLInputElement>('Project name').value).toBe('Stored tower');
  });
}

function edit() {
  fireEvent.change(overview().getByLabelText('Customer'), { target: { value: 'Changed' } });
}

function stepState(step: string): string | null {
  return review().getByTestId(`readiness-${step}`).getAttribute('data-state');
}

beforeEach(() => {
  api.list.mockResolvedValue({
    kind: 'listed',
    page: {
      permissions: ['approve_revision', 'delete_project', 'edit_company_settings'],
      projects: [{ id: 'p1', name: 'Stored tower', revisions: 1, updated_at: 'now' }],
    },
  });
  api.open.mockResolvedValue({ kind: 'saved', project: saved() });
});

afterEach(() => {
  vi.clearAllMocks();
  window.history.replaceState(null, '', '/');
});

describe('reading what the server recorded', () => {
  it('lists the findings blocking first', () => {
    const validation = validationOf(saved({ findings: [WARNING, BLOCKING] }));
    expect(validation.findings.map((finding) => finding.code)).toEqual([
      'integrity_device_above_cable_capacity',
      'rules_unconfirmed',
    ]);
    expect(validation.validated).toBe(true);
  });

  it('takes a revision with no validation fields as not validated (saved before Phase 1)', () => {
    const { id, name, revision, revisions, request } = saved();
    const legacy: SavedProject = {
      id,
      name,
      revision,
      revisions,
      request,
      validated: false,
      export_status: 'not_validated',
    };
    expect(validationOf(legacy)).toMatchObject({ validated: false, findings: [] });
  });

  it('finds the load row a circuit or device id is about', () => {
    expect(rowOf({ circuit_id: 'c3', device_id: null })).toBe(2);
    expect(rowOf({ circuit_id: null, device_id: 'c12-breaker' })).toBe(11);
    expect(rowOf({ circuit_id: null, device_id: 'incomer' })).toBeNull();
    expect(rowOf({ circuit_id: 'c0', device_id: null })).toBeNull();
  });

  it('names unknown fields from a strict 422 by path', () => {
    expect(
      unknownFieldsOf({
        detail: [
          { type: 'extra_forbidden', loc: ['body', 'project', 'boards', 0, 'standby_supply'] },
          { type: 'missing', loc: ['body', 'format'] },
          { type: 'extra_forbidden', loc: ['body', 'transfer_switch'] },
        ],
      }),
    ).toEqual(['project.boards[0].standby_supply', 'transfer_switch']);
    expect(unknownFieldsOf({ detail: 'nope' })).toEqual([]);
  });

  it('reads an execution refusal’s gaps, blocking findings and missing approval', () => {
    expect(
      executionRefusalOf({
        kind: 'refused',
        detail: '',
        code: 'execution_package_incomplete',
        params: {
          missing: 'wiring_and_terminal_connectivity',
          approval: 'missing',
          blocking_findings: 'a,b',
          blockers: 'wiring_and_terminal_connectivity,stale,approval,finding:a,finding:b',
        },
      }),
    ).toEqual({
      missing: ['wiring_and_terminal_connectivity'],
      blockingFindings: ['a', 'b'],
      approvalMissing: true,
      notValidated: false,
      stale: true,
    });
    // Present approval and no blockers read as nothing else in the way.
    expect(
      executionRefusalOf({
        kind: 'refused',
        detail: '',
        code: 'execution_package_incomplete',
        params: { missing: 'control_schematics', approval: 'present', blocking_findings: '' },
      }),
    ).toEqual({
      missing: ['control_schematics'],
      blockingFindings: [],
      approvalMissing: false,
      notValidated: false,
      stale: false,
    });
  });
});

describe('the readiness steps', () => {
  const validation = (fields: Partial<RevisionValidation> = {}): RevisionValidation => ({
    validated: true,
    findings: [],
    snapshotSha256: SHA,
    approvedSnapshotSha256: null,
    rulesVersion: 'r1',
    exportStatus: 'draft',
    executionBlockers: [],
    ...fields,
  });
  const by = { by: 'A. Engineer', at: '2026-10-03' };

  it('starts unsaved, with execution always blocked', () => {
    const readiness = readinessOf({ opened: null, validation: null, dirty: false, stale: false });
    expect(readiness).toMatchObject({
      saved: 'pending',
      validated: 'pending',
      approved: 'pending',
      execution: 'blocked',
      canApprove: false,
      approvalBlockers: ['unsaved'],
    });
  });

  it('holds approval while blocking findings stand', () => {
    const blocking = validationOf(saved({ findings: [BLOCKING] }));
    const readiness = readinessOf({
      opened: { approval: null },
      validation: blocking,
      dirty: false,
      stale: false,
    });
    expect(readiness).toMatchObject({
      validated: 'blocked',
      approved: 'blocked',
      canApprove: false,
      approvalBlockers: ['blocking'],
      blockingCount: 1,
    });
  });

  it('shows an approval as covering only the snapshot it signed', () => {
    const covered = readinessOf({
      opened: { approval: by },
      validation: validation({ approvedSnapshotSha256: SHA }),
      dirty: false,
      stale: false,
    });
    expect(covered).toMatchObject({ approved: 'done', approvalCovers: true });
    // A legacy approval, signed before snapshots, is not shown as covering.
    const legacy = readinessOf({
      opened: { approval: by },
      validation: validation(),
      dirty: false,
      stale: false,
    });
    expect(legacy).toMatchObject({ approved: 'pending', approvalCovers: false });
  });

  it('marks validation and approval stale after an edit or a rules change', () => {
    for (const change of [
      { dirty: true, stale: false },
      { dirty: false, stale: true },
    ]) {
      const readiness = readinessOf({
        opened: { approval: by },
        validation: validation({ approvedSnapshotSha256: SHA }),
        ...change,
      });
      expect(readiness).toMatchObject({ validated: 'stale', approved: 'stale' });
    }
  });
});

describe('exporting a revision', () => {
  it('posts the format and a review purpose by default to the revision export endpoint', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('pdf', {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="tower-r1.pdf"' },
      }),
    );
    const outcome = await exportRevision({
      token: 'tok',
      id: 'p 1',
      revision: 2,
      format: 'pdf',
      fetchImpl,
    });
    expect(outcome).toMatchObject({ kind: 'exported', filename: 'tower-r1.pdf' });
    const [url, init] = fetchImpl.mock.calls[0] ?? [];
    expect(url).toBe('/api/v1/design/projects/p%201/revisions/2/export');
    expect(JSON.parse(init?.body as string)).toEqual({ format: 'pdf', purpose: 'review' });
  });

  it('says a 409 by its code, keeping the list params', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: 'conflict',
          detail: 'execution package incomplete',
          code: 'execution_package_incomplete',
          params: { missing: ['control_schematics'], count: 1 },
        }),
        { status: 409 },
      ),
    );
    const outcome = await exportRevision({
      token: 'tok',
      id: 'p1',
      revision: 1,
      format: 'pdf',
      purpose: 'execution',
      fetchImpl,
    });
    expect(outcome).toMatchObject({
      kind: 'refused',
      code: 'execution_package_incomplete',
      params: { count: '1' },
      values: { missing: ['control_schematics'] },
    });
  });
});

describe('the review view', () => {
  it('shows the steps and the findings, and goes to the row a finding is about', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: saved({ findings: [WARNING, BLOCKING] }),
    });
    renderScreen();
    await openStored();
    expect(stepState('saved')).toBe('done');
    expect(stepState('validated')).toBe('blocked');
    expect(stepState('execution')).toBe('blocked');
    const findings = review().getByTestId('findings');
    const [first, second] = Array.from(findings.querySelectorAll('li'));
    expect(first?.textContent).toContain('Blocking');
    expect(first?.textContent).toContain(
      'Heater: 200 A protection is above the 17.5 A its 3G1.5 cable carries (method C); In ≤ Iz does not hold.',
    );
    // A finding made from a design note is said as the note is.
    expect(second?.textContent).toContain(
      'Circuit rules are this software’s defaults, not confirmed',
    );
    expect(first?.textContent).toContain('Board MDB · circuit c2 · device c2-breaker');
    expect(second?.textContent).toContain('Needs an engineer’s confirmation.');

    fireEvent.click(within(findings).getByText('Go to row 2'));
    await waitFor(() => {
      expect(screen.getByTestId('view-schedule').hidden).toBe(false);
    });
    const row = within(screen.getByTestId('design-load-1')).getByLabelText<HTMLInputElement>(
      'Circuit',
    );
    expect(row.value).toBe('Heater');
    await waitFor(() => {
      expect(document.activeElement).toBe(row);
    });

    // Approval is held, said why, and not sent.
    fireEvent.change(overview().getByLabelText('Approval note (optional)'), {
      target: { value: 'A. Engineer' },
    });
    const approve = overview().getByText<HTMLButtonElement>('Approve revision 1');
    expect(approve.disabled).toBe(true);
    expect(overview().getByTestId('approve-held-blocking').textContent).toContain(
      '1 blocking finding must be resolved',
    );
    fireEvent.click(approve);
    expect(api.approve).not.toHaveBeenCalled();
  });

  it('exports the stored revision for review, and shows the execution refusal’s reasons', async () => {
    const exportRevisionImpl = vi
      .fn<typeof exportRevision>()
      .mockResolvedValueOnce({ kind: 'exported', blob: new Blob(['pdf']), filename: 'r1.pdf' })
      .mockResolvedValueOnce({
        kind: 'refused',
        detail: 'incomplete',
        code: 'execution_package_incomplete',
        params: {
          missing:
            'wiring_and_terminal_connectivity,control_schematics,orderable_parts,mechanical_layout',
          approval: 'missing',
        },
      });
    const saveImpl = vi.fn();
    renderScreen({ exportRevisionImpl, saveImpl });
    await openStored();

    fireEvent.click(review().getByTestId('revision-export-pdf'));
    await waitFor(() => {
      expect(saveImpl).toHaveBeenCalledWith(expect.any(Blob), 'r1.pdf');
    });
    expect(exportRevisionImpl.mock.calls[0]?.[0]).toMatchObject({
      id: 'p1',
      revision: 1,
      format: 'pdf',
      purpose: 'review',
    });

    fireEvent.click(review().getByTestId('execution-export'));
    const refused = await review().findByTestId('execution-refused');
    expect(exportRevisionImpl.mock.calls[1]?.[0]).toMatchObject({ purpose: 'execution' });
    for (const text of [
      'Wiring and terminal connectivity',
      'Control schematics',
      'Orderable parts with order numbers',
      'A dimensioned mechanical layout',
      'Review approval of this revision',
    ]) {
      expect(refused.textContent).toContain(text);
    }
    expect(saveImpl).toHaveBeenCalledTimes(1);
  });

  it('shows the formats whose draft mark an import strips as unavailable, and why', async () => {
    const exportRevisionImpl = vi.fn<typeof exportRevision>();
    renderScreen({ exportRevisionImpl });
    await openStored();
    const reason = review().getByTestId('revision-export-unavailable');
    expect(reason.textContent).toBe(UNAVAILABLE);
    for (const format of UNPROTECTED_FORMATS) {
      if (format === 'quotation_csv' || format === 'plc_io_csv' || format === 'plc_st') continue;
      const button = review().getByTestId<HTMLButtonElement>(`revision-export-${format}`);
      expect(button.disabled, format).toBe(true);
      expect(button.getAttribute('aria-describedby')).toBe(reason.id);
      fireEvent.click(button);
    }
    for (const format of ['pdf', 'calculations_pdf', 'dxf', 'json'] as const) {
      expect(review().getByTestId<HTMLButtonElement>(`revision-export-${format}`).disabled).toBe(
        false,
      );
    }
    expect(exportRevisionImpl).not.toHaveBeenCalled();
  });

  it('says a 409 export_format_unprotected as the unavailable reason', async () => {
    const exportRevisionImpl = vi.fn<typeof exportRevision>().mockResolvedValue({
      kind: 'refused',
      detail: 'circuits_csv cannot keep a visible draft mark once imported',
      code: 'export_format_unprotected',
      params: {
        format: 'circuits_csv',
        reason: 'draft_mark_not_persistent',
        allowed: 'pdf,dxf,quotation_pdf,calculations_pdf,json',
      },
    });
    const saveImpl = vi.fn();
    renderScreen({ exportRevisionImpl, saveImpl });
    await openStored();
    fireEvent.click(review().getByTestId('revision-export-pdf'));
    await waitFor(() => {
      expect(review().getAllByText(UNAVAILABLE)).toHaveLength(2);
    });
    expect(saveImpl).not.toHaveBeenCalled();
  });

  it('says a stale revision needs a new save, and holds approval', async () => {
    const exportRevisionImpl = vi.fn<typeof exportRevision>().mockResolvedValue({
      kind: 'refused',
      detail: 'rules changed',
      code: 'revision_stale',
    });
    renderScreen({ exportRevisionImpl });
    await openStored();
    fireEvent.click(review().getByTestId('revision-export-pdf'));
    expect((await review().findByTestId('readiness-stale')).textContent).toBe(
      'The rules or company profile changed since this revision — save a new revision.',
    );
    expect(stepState('validated')).toBe('stale');
    expect(overview().getByTestId('approve-held-stale')).toBeTruthy();
  });

  it('asks for an older revision to be saved again to validate it', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved({ validated: false }) });
    renderScreen();
    await openStored();
    expect(review().getByTestId('revision-export-not-validated').textContent).toBe(
      'Revision 1 was saved before validation: save again to validate it.',
    );
    expect(review().queryByTestId('revision-export-pdf')).toBeNull();
    expect(overview().getByTestId('approve-held-notValidated')).toBeTruthy();
  });

  it('never lets an approval read as covering edits made since', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: approved({ findings: [WARNING] }) });
    renderScreen();
    await openStored();
    expect(stepState('approved')).toBe('done');
    expect(screen.getAllByTestId('context-approval')[0]?.textContent).toBe('Approved');

    edit();
    expect(stepState('saved')).toBe('stale');
    expect(stepState('approved')).toBe('stale');
    expect(screen.getAllByTestId('context-approval')[0]?.textContent).toBe(
      'Approval does not cover this',
    );
    expect(overview().getByTestId('approval-changed')).toBeTruthy();
    expect(review().getByTestId('findings-outdated')).toBeTruthy();
    // The revision export would not hold the edits, so it is not offered.
    expect(review().getByTestId('revision-export-dirty')).toBeTruthy();
    expect(review().queryByTestId('revision-export-pdf')).toBeNull();
  });

  it('does not show a legacy approval as covering the revision', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: approved({ approved_snapshot_sha256: null }),
    });
    renderScreen();
    await openStored();
    expect(stepState('approved')).toBe('pending');
    expect(overview().getByTestId('approval-not-covering')).toBeTruthy();
    expect(screen.getAllByTestId('context-approval')[0]?.textContent).toBe(
      'Approval does not cover this',
    );
  });
});

describe('the draft export', () => {
  it('is labelled DRAFT — NOT FOR EXECUTION and keeps the inputs on an unknown-field refusal', async () => {
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    const exportImpl = vi.fn<typeof exportDesign>().mockResolvedValue({
      kind: 'refused',
      detail: 'Extra inputs are not permitted',
      unknownFields: ['project.boards[0].standby_supply'],
    });
    renderScreen({ designImpl, exportImpl });
    await openStored();
    fireEvent.click(screen.getByText('Design the board', { selector: 'button' }));
    const draft = await review().findByTestId('draft-export');
    expect(draft.textContent).toContain('DRAFT — NOT FOR EXECUTION');

    fireEvent.click(within(draft).getByTestId('design-export-pdf'));
    expect(await within(draft).findByText(/project\.boards\[0\]\.standby_supply/)).toBeTruthy();
    expect(draft.textContent).toContain('The server does not accept the field');
    expect(exportImpl).toHaveBeenCalledTimes(1);
    expect(overview().getByLabelText<HTMLInputElement>('Project name').value).toBe('Stored tower');
  });

  it('offers only the formats that keep a visible mark, and says a 409 as the reason', async () => {
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    const exportImpl = vi.fn<typeof exportDesign>().mockResolvedValue({
      kind: 'refused',
      detail: 'qet cannot keep a visible draft mark once imported',
      code: 'export_format_unprotected',
      params: { format: 'qet', reason: 'draft_mark_not_persistent', allowed: 'pdf' },
    });
    renderScreen({ designImpl, exportImpl });
    await openStored();
    fireEvent.click(screen.getByText('Design the board', { selector: 'button' }));
    const draft = await review().findByTestId('draft-export');
    expect(within(draft).getByTestId('design-export-unavailable').textContent).toBe(UNAVAILABLE);
    for (const format of ['qet', 'aml', 'devices_csv', 'circuits_csv', 'ace_terminals_csv']) {
      const button = within(draft).getByTestId<HTMLButtonElement>(`design-export-${format}`);
      expect(button.disabled, format).toBe(true);
      fireEvent.click(button);
    }
    expect(exportImpl).not.toHaveBeenCalled();
    fireEvent.click(within(draft).getByTestId('design-export-dxf'));
    await waitFor(() => {
      expect(within(draft).getAllByText(UNAVAILABLE)).toHaveLength(2);
    });
    expect(exportImpl.mock.calls[0]?.[0].format).toBe('dxf');
  });
});

describe('the formats whose draft mark an import strips', () => {
  it('are every CSV, QElectroTech, AutomationML and the PLC program, as the API refuses them', () => {
    expect([...UNPROTECTED_FORMATS].sort()).toEqual(
      [
        'ace_components_csv',
        'ace_terminals_csv',
        'aml',
        'cables_csv',
        'circuits_csv',
        'devices_csv',
        'eplan_devices_csv',
        'parts_csv',
        'plc_io_csv',
        'plc_st',
        'qet',
        'quotation_csv',
        'terminals_csv',
      ].sort(),
    );
    for (const format of ['pdf', 'calculations_pdf', 'quotation_pdf', 'dxf', 'json'] as const) {
      expect(isUnprotected(format), format).toBe(false);
    }
  });
});

describe('translations', () => {
  const KEYS = [
    'design.readiness.steps.execution',
    'design.readiness.executionBlocked',
    'design.findings.severity.blocking',
    'design.findings.jumpRow',
    'design.exports.draftMark',
    'design.exports.gaps.control_schematics',
    'design.errors.revision_stale',
    'design.errors.revision_not_validated',
    'design.errors.revision_has_blocking_findings',
    'design.errors.unknown_fields',
    'design.errors.export_format_unprotected',
    'design.projects.approveHeld.blocking',
    'workspace.context.approvalOutdated',
  ];

  function lookup(bundle: Record<string, unknown>, key: string): unknown {
    return key
      .split('.')
      .reduce<unknown>(
        (node, part) =>
          typeof node === 'object' && node !== null
            ? (node as Record<string, unknown>)[part]
            : undefined,
        bundle,
      );
  }

  it.each(Object.entries(MESSAGES))('%s has every new key, translated', (locale, bundle) => {
    for (const key of KEYS) {
      const value = lookup(bundle, key);
      expect(typeof value, `${locale} ${key}`).toBe('string');
      if (locale !== 'en') expect(value, `${locale} ${key}`).not.toBe(lookup(MESSAGES.en, key));
    }
  });

  it('says the draft mark in Arabic, keeping the sheet left-to-right', async () => {
    const ar = (key: string) => String(lookup(MESSAGES.ar, key));
    const designImpl = vi
      .fn<typeof designProject>()
      .mockResolvedValue({ kind: 'designed', response: DESIGNED });
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        marketsImpl={vi.fn().mockResolvedValue(null)}
        designImpl={designImpl}
      />,
      { locale: 'ar' },
    );
    const row = await screen.findByTestId('project-p1');
    fireEvent.click(within(row).getByText(ar('design.projects.open')));
    await waitFor(() => {
      expect(review().getByTestId('readiness-saved').getAttribute('data-state')).toBe('done');
    });
    expect(review().getByTestId('readiness-execution').textContent).toContain(
      ar('design.readiness.steps.execution'),
    );
    fireEvent.click(screen.getByText(ar('design.submit'), { selector: 'button' }));
    const draft = await review().findByTestId('draft-export');
    expect(draft.textContent).toContain('مسودة — ليست للتنفيذ');
    // The sheet stays left-to-right in a right-to-left page.
    expect(screen.getByTestId('sheet-frame').getAttribute('dir')).toBe('ltr');
  });
});

describe('a revision the server reads as stale', () => {
  it('says so on opening, before any export is tried', async () => {
    api.open.mockResolvedValue({ kind: 'saved', project: saved({ export_status: 'stale' }) });
    renderScreen();
    await openStored();
    expect(review().getByTestId('readiness-stale').textContent).toBe(
      'The rules or company profile changed since this revision — save a new revision.',
    );
    expect(stepState('validated')).toBe('stale');
    expect(overview().getByTestId('approve-held-stale')).toBeTruthy();
  });
});

describe('the backend’s finding codes and approval refusals', () => {
  const CODES = [
    'integrity_device_above_cable_capacity',
    'integrity_unknown_device',
    'integrity_unknown_cable',
    'integrity_protection_unchecked',
    'integrity_cable_untabulated',
    'integrity_drive_cable_unchecked',
  ];

  it.each(Object.entries(MESSAGES))('%s translates every finding code', (_locale, bundle) => {
    const design = bundle.design as {
      finding: Record<string, unknown>;
      errors: Record<string, unknown>;
    };
    for (const code of CODES) expect(typeof design.finding[code], code).toBe('string');
    expect(typeof design.errors.design_refused).toBe('string');
  });

  it('says a refused design with its refusal, and the approval refusals by code', async () => {
    const refused = {
      code: 'design_refused',
      severity: 'blocking',
      board_id: null,
      values: { refusal: 'three_phase_on_single_phase' },
      message: 'The design was refused: ...',
      source: 'policy',
    };
    api.open.mockResolvedValue({ kind: 'saved', project: saved({ findings: [refused] }) });
    api.approve
      .mockResolvedValueOnce({
        kind: 'refused',
        detail: 'conflict',
        code: 'revision_conflict',
      })
      .mockResolvedValueOnce({
        kind: 'refused',
        detail: 'already',
        code: 'revision_already_approved',
        params: { revision: '1', approver: 'B. Other' },
      });
    renderScreen({ account: true });
    await openStored();
    expect(review().getByTestId('findings').textContent).toContain(
      'The design was refused, so this revision holds no validated design. Correct the inputs and save again. A three-phase load cannot be fed from a single-phase board.',
    );

    // With nothing blocking, the approval refusals the server sends are said.
    api.open.mockResolvedValue({ kind: 'saved', project: saved() });
    fireEvent.click(within(await screen.findByTestId('project-p1')).getByText('Open'));
    await waitFor(() => {
      expect(review().queryByTestId('findings')).toBeNull();
    });
    fireEvent.change(overview().getByLabelText('Approval note (optional)'), {
      target: { value: 'A. Engineer' },
    });
    fireEvent.click(overview().getByText('Approve revision 1'));
    expect((await overview().findByTestId('projects-message')).textContent).toBe(
      'Another revision was saved at the same time; save again.',
    );
    fireEvent.click(overview().getByText('Approve revision 1'));
    await waitFor(() => {
      expect(overview().getByTestId('projects-message').textContent).toBe(
        'Revision 1 is approved already, by B. Other.',
      );
    });
  });
});

describe('execution blockers, a second approver, and over-long fields', () => {
  const BLOCKERS = [
    'wiring_and_terminal_connectivity',
    'control_schematics',
    'orderable_parts',
    'mechanical_layout',
    'approval',
    'finding:integrity_device_above_cable_capacity',
  ];

  it('reads a saved project’s execution blockers', () => {
    const validation = validationOf(saved({ execution_blockers: [...BLOCKERS, 'not_validated'] }));
    expect(executionBlockersOf(validation)).toEqual({
      missing: BLOCKERS.slice(0, 4),
      blockingFindings: ['integrity_device_above_cable_capacity'],
      approvalMissing: true,
      notValidated: true,
      stale: false,
    });
  });

  it('shows why execution is blocked before any export is tried', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: saved({ execution_blockers: BLOCKERS }),
    });
    const exportRevisionImpl = vi.fn<typeof exportRevision>();
    renderScreen({ exportRevisionImpl });
    await openStored();
    const blockers = await review().findByTestId('execution-blockers');
    expect(blockers.textContent).toContain('Execution export blocked');
    for (const text of [
      'Wiring and terminal connectivity',
      'Control schematics',
      'Orderable parts with order numbers',
      'A dimensioned mechanical layout',
      '1 blocking finding resolved',
      'Review approval of this revision',
    ]) {
      expect(blockers.textContent).toContain(text);
    }
    expect(exportRevisionImpl).not.toHaveBeenCalled();
    expect(review().queryByTestId('execution-refused')).toBeNull();
  });

  it('shows the first approval when a second approver is refused', async () => {
    api.open
      .mockResolvedValueOnce({ kind: 'saved', project: saved() })
      .mockResolvedValue({ kind: 'saved', project: approved() });
    api.approve.mockResolvedValue({
      kind: 'refused',
      detail: 'already',
      code: 'revision_already_approved',
      params: { revision: '1', approver: 'A. Engineer' },
    });
    renderScreen({ account: true });
    await openStored();
    fireEvent.change(overview().getByLabelText('Approval note (optional)'), {
      target: { value: 'B. Second' },
    });
    fireEvent.click(overview().getByText('Approve revision 1'));
    expect((await overview().findByTestId('project-approval')).textContent).toContain(
      'A. Engineer',
    );
    expect(overview().getByTestId('projects-message').textContent).toBe(
      'Revision 1 is approved already, by A. Engineer.',
    );
    // The form is kept as it was, not replaced by the reopened revision.
    expect(api.open).toHaveBeenCalledTimes(2);
    expect(overview().getByLabelText<HTMLInputElement>('Project name').value).toBe('Stored tower');
  });

  it('reads over-long fields from a strict 422, by path and limit', () => {
    expect(
      tooLongOf({
        detail: [
          {
            type: 'string_too_long',
            loc: ['body', 'request', 'info', 'customer'],
            msg: 'String should have at most 200 characters',
            ctx: { max_length: 200 },
          },
          { type: 'missing', loc: ['body', 'format'], msg: 'Field required' },
        ],
      }),
    ).toEqual([{ path: 'request.info.customer', max: 200 }]);
    // The same errors inside an envelope are read too.
    expect(
      unknownFieldsOf({
        error: 'validation_error',
        detail: 'invalid',
        errors: [{ type: 'extra_forbidden', loc: ['body', 'colour'], msg: 'Extra inputs' }],
      }),
    ).toEqual(['colour']);
  });

  it('says an over-long project field at the field, keeping what was typed', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'refused',
      detail: 'String should have at most 200 characters',
      tooLong: [{ path: 'info.customer', max: 200 }],
    });
    renderScreen({ designImpl });
    await openStored();
    const long = 'x'.repeat(250);
    fireEvent.change(overview().getByLabelText('Customer'), { target: { value: long } });
    fireEvent.click(screen.getByText('Design the board', { selector: 'button' }));
    const field = overview().getByLabelText<HTMLInputElement>('Customer');
    await waitFor(() => {
      expect(field.getAttribute('aria-invalid')).toBe('true');
    });
    expect(overview().getByText('Too long: at most 200 characters.')).toBeTruthy();
    expect(field.value).toBe(long);
  });
});

describe('the strict 422 envelope (request_invalid)', () => {
  const ENVELOPE = {
    error: 'RequestValidationError',
    code: 'request_invalid',
    params: { count: 3 },
    detail: [
      {
        loc: ['body', 'request', 'info', 'customer'],
        msg: 'String should have at most 200 characters',
        type: 'string_too_long',
        ctx: { max_length: 200 },
      },
      {
        loc: ['body', 'request', 'boards', 0, 'loads', 1, 'length_m'],
        msg: 'Input should be less than or equal to 2000',
        type: 'less_than_equal',
        ctx: { le: 2000 },
      },
      {
        loc: ['body', 'request', 'info', 'standby_supply'],
        msg: 'Extra inputs are not permitted',
        type: 'extra_forbidden',
      },
    ],
  };

  it('reads each field at fault from the envelope', () => {
    expect(tooLongOf(ENVELOPE)).toEqual([{ path: 'request.info.customer', max: 200 }]);
    expect(tooLargeOf(ENVELOPE)).toEqual([
      { path: 'request.boards[0].loads[1].length_m', max: '2000' },
    ]);
    expect(unknownFieldsOf(ENVELOPE)).toEqual(['request.info.standby_supply']);
  });

  it('reads a refused save’s field limits from the exact 422 response', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ ...ENVELOPE, detail: ENVELOPE.detail.slice(0, 2) }), {
        status: 422,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const actual = await vi.importActual<typeof DesignModule>('@/lib/design');
    const outcome = await actual.reviseProject({
      token: 't',
      id: 'p1',
      request: REQUEST,
      fetchImpl,
    });
    expect(outcome).toMatchObject({
      kind: 'refused',
      code: 'request_invalid',
      tooLong: [{ path: 'request.info.customer', max: 200 }],
      tooLarge: [{ path: 'request.boards[0].loads[1].length_m', max: '2000' }],
    });
  });
});
