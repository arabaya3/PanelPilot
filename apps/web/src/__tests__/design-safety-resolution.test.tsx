import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { renderApp } from './helpers';

import { ProjectsPanel, type OpenedProject } from '@/components/design/projects-panel';
import { readinessOf } from '@/components/design/readiness';
import { ApprovalScope, SafetyConcerns } from '@/components/design/safety-concerns';
import type { listProjects, openProject } from '@/lib/design';
import {
  approvalHeldOf,
  approveRevision,
  resolveSafetyConcern,
  safetyConcernsOf,
  safetyResolutionOf,
  type ProjectDesignRequest,
  type SafetyConcern,
  type SavedProject,
} from '@/lib/design';

/** M6d: safety concerns hold approval; two resolutions; the approval scope and its confirmation. */

const MENTION: SafetyConcern = {
  kind: 'mention',
  target_id: 'm0123456789abcdef',
  level: 'life_safety',
  code: 'ai_life_safety_mentioned',
  label: 'fire pump',
  start: 10,
  end: 19,
  resolved: false,
  resolution: null,
  false_positive_refusal: 'life_safety_item_overlaps',
  carried: false,
};
const ITEM: SafetyConcern = {
  ...MENTION,
  kind: 'item',
  target_id: 'i1',
  code: 'ai_life_safety_item',
  label: 'A fire pump of 30 kW',
  false_positive_refusal: 'item',
};
const ROAD: SafetyConcern = {
  ...MENTION,
  target_id: 'm1111111111111111',
  level: 'possibly',
  code: 'ai_possibly_life_safety',
  label: 'fire',
  false_positive_refusal: null,
};
const EXCLUDED: SafetyConcern = {
  ...MENTION,
  resolved: true,
  resolution: {
    target_kind: 'mention',
    target_id: MENTION.target_id,
    resolution: 'excluded_from_scope',
    reason: 'Designed separately by the fire-protection contractor.',
    by_user_id: 'u2',
    by_name: 'Rana Haddad',
    at: '2026-11-04T10:00:00Z',
  },
};
const FALSE_POSITIVE: SafetyConcern = {
  ...ROAD,
  resolved: true,
  resolution: {
    target_kind: 'mention',
    target_id: ROAD.target_id,
    resolution: 'false_positive',
    reason: 'A street name in the site address.',
    by_user_id: 'u2',
    by_name: 'Rana Haddad',
    at: '2026-11-04T10:00:00Z',
  },
};

describe('the safety concerns list', () => {
  function show(
    concerns: SafetyConcern[],
    eligible = true,
    reason: string | null = null,
    onResolve = vi.fn().mockResolvedValue(null),
    locale?: 'en' | 'ar' | 'he',
  ) {
    renderApp(
      <SafetyConcerns
        concerns={concerns}
        eligibility={{ eligible, reason }}
        onResolve={onResolve}
      />,
      locale ? { locale } : {},
    );
    return onResolve;
  }

  it('says how many concerns hold approval and that detection is not complete', () => {
    show([MENTION, ITEM]);
    expect(screen.getByText(/2 unresolved safety concerns hold approval/)).toBeTruthy();
    expect(screen.getByText(/cannot find every life-safety requirement/)).toBeTruthy();
  });

  it('offers only exclusion where the proposal reads the text as life-safety', () => {
    show([MENTION, ITEM]);
    expect(screen.getByTestId(`resolve-exclude-mention-${MENTION.target_id}`)).toBeTruthy();
    expect(screen.queryByTestId(`resolve-fp-mention-${MENTION.target_id}`)).toBeNull();
    expect(screen.getByTestId(`resolve-no-fp-mention-${MENTION.target_id}`).textContent).toMatch(
      /only be excluded from scope/,
    );
    expect(screen.getByTestId('resolve-no-fp-item-i1').textContent).toMatch(/never a false/);
  });

  it('records an exclusion with a required reason of 10 to 500 characters', async () => {
    const onResolve = show([MENTION]);
    fireEvent.click(screen.getByTestId(`resolve-exclude-mention-${MENTION.target_id}`));
    expect(screen.getByText(/never becomes a load/)).toBeTruthy();
    const submit = screen.getByTestId<HTMLButtonElement>(
      `resolve-submit-mention-${MENTION.target_id}`,
    );
    fireEvent.change(screen.getByTestId(`resolve-reason-mention-${MENTION.target_id}`), {
      target: { value: 'short' },
    });
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByTestId(`resolve-reason-mention-${MENTION.target_id}`), {
      target: { value: '  Designed separately by the fire-protection contractor.  ' },
    });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        MENTION,
        'excluded_from_scope',
        'Designed separately by the fire-protection contractor.',
      );
    });
    expect(await screen.findByText('Resolution recorded.')).toBeTruthy();
  });

  it('records a false positive of a possibly-safety word', async () => {
    const onResolve = show([ROAD]);
    fireEvent.click(screen.getByTestId(`resolve-fp-mention-${ROAD.target_id}`));
    expect(screen.getByText(/a street name/)).toBeTruthy();
    fireEvent.change(screen.getByTestId(`resolve-reason-mention-${ROAD.target_id}`), {
      target: { value: 'A street name in the site address.' },
    });
    fireEvent.click(screen.getByTestId(`resolve-submit-mention-${ROAD.target_id}`));
    await waitFor(() => {
      expect(onResolve).toHaveBeenCalledWith(
        ROAD,
        'false_positive',
        'A street name in the site address.',
      );
    });
  });

  it('shows the server’s refusal and keeps the form', async () => {
    show([ROAD], true, null, vi.fn().mockResolvedValue('Refused by the server.'));
    fireEvent.click(screen.getByTestId(`resolve-fp-mention-${ROAD.target_id}`));
    fireEvent.change(screen.getByTestId(`resolve-reason-mention-${ROAD.target_id}`), {
      target: { value: 'A street name in the site address.' },
    });
    fireEvent.click(screen.getByTestId(`resolve-submit-mention-${ROAD.target_id}`));
    expect(await screen.findByText('Refused by the server.')).toBeTruthy();
    expect(screen.getByTestId(`resolve-form-mention-${ROAD.target_id}`)).toBeTruthy();
  });

  it.each([
    ['proposal_author', /created this AI proposal/],
    ['revision_author', /saved this revision/],
    ['not_permitted', /may approve revisions/],
  ])('says why an ineligible member cannot resolve (%s)', (reason, text) => {
    show([MENTION], false, reason);
    expect(screen.queryByTestId(`resolve-exclude-mention-${MENTION.target_id}`)).toBeNull();
    expect(
      screen.getByTestId(`resolve-ineligible-mention-${MENTION.target_id}`).textContent,
    ).toMatch(text);
  });

  it('shows who resolved, when and why', () => {
    show([EXCLUDED, FALSE_POSITIVE]);
    expect(screen.getByText(/Every safety concern is resolved/)).toBeTruthy();
    expect(screen.getByTestId(`resolved-mention-${MENTION.target_id}`).textContent).toBe(
      'Excluded from scope by Rana Haddad on 2026-11-04: Designed separately by the fire-protection contractor.',
    );
    expect(screen.getByTestId(`resolved-mention-${ROAD.target_id}`).textContent).toMatch(
      /not engineer-validated/,
    );
  });

  it.each(['ar', 'he'] as const)('is translated (%s)', (locale) => {
    show([MENTION, ROAD], true, null, vi.fn().mockResolvedValue(null), locale);
    const text = screen.getByTestId('safety-concerns').textContent;
    expect(text).not.toMatch(/Exclude from scope|unresolved safety/);
    expect(text).toMatch(locale === 'ar' ? /استبعاد من النطاق/ : /החרג מההיקף/);
  });
});

describe('the approval scope', () => {
  it.each([
    ['en', /not engineer validation/, /control circuits/],
    ['ar', /ليس تحققًا هندسيًا/, /دوائر التحكم/],
    ['he', /אינו אימות הנדסי/, /מעגלי פיקוד/],
  ] as const)('says what an approval covers and does not (%s)', (locale, validation, control) => {
    const onChange = vi.fn();
    renderApp(
      <ApprovalScope
        concerns={[EXCLUDED, FALSE_POSITIVE]}
        confirmed={false}
        onConfirmedChange={onChange}
        disabled={false}
      />,
      { locale },
    );
    const text = screen.getByTestId('approval-scope').textContent;
    expect(text).toMatch(validation);
    expect(text).toMatch(control);
    expect(screen.getByTestId('scope-excluded').textContent).toMatch(/fire-protection contractor/);
    expect(screen.getByTestId('scope-false-positives').textContent).toMatch(/street name/);
    fireEvent.click(screen.getByTestId('scope-confirm'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('the approval panel', () => {
  const REQUEST = { info: { name: 'Tower' }, boards: [] } as unknown as ProjectDesignRequest;
  const OPENED: OpenedProject = {
    id: 'p1',
    name: 'Tower',
    revision: 1,
    revisions: [1],
    approval: null,
    approvingAs: 'Rana Haddad',
    canApprove: true,
    isAuthor: false,
    authorEstablished: true,
    safetyConcerns: [ROAD],
    safetyResolution: { eligible: true, reason: null },
  };

  function panel(opened: OpenedProject, blockers: Parameters<typeof ProjectsPanel>[0]['blockers']) {
    const list = vi.fn<typeof listProjects>().mockResolvedValue({
      kind: 'listed',
      page: { permissions: ['approve_revision'], projects: [] },
    });
    const approve = vi
      .fn<typeof approveRevision>()
      .mockResolvedValue({ kind: 'refused', detail: '', code: 'approval_blocked_by_safety' });
    const resolve = vi.fn<typeof resolveSafetyConcern>().mockResolvedValue({
      kind: 'recorded',
      requirements: {} as never,
    });
    const reopened = { id: 'p1' } as unknown as SavedProject;
    const open = vi
      .fn<typeof openProject>()
      .mockResolvedValue({ kind: 'saved', project: reopened });
    const onOpened = vi.fn();
    renderApp(
      <ProjectsPanel
        token="tok"
        current={{ name: 'Tower', request: REQUEST }}
        opened={opened}
        onOpened={onOpened}
        blockers={blockers ?? []}
        impls={{ list, approve, resolve, open }}
      />,
    );
    return { approve, resolve, open, onOpened, reopened };
  }

  it('holds approval while a safety concern is unresolved, and says so', () => {
    panel({ ...OPENED, safetyConcerns: [ROAD, MENTION, FALSE_POSITIVE] }, ['safety']);
    const held = screen.getByTestId('approve-held-safety').textContent;
    expect(held).toMatch(/excluded from scope or judged a false positive/);
    // M6e: the safety hold counts the unresolved concerns, not blocking findings.
    expect(held).toMatch(/^2 safety concerns are unresolved/);
    fireEvent.click(screen.getByTestId('scope-confirm'));
    expect(
      screen.getByRole<HTMLButtonElement>('button', { name: 'Approve revision 1' }).disabled,
    ).toBe(true);
  });

  it('approves only once the scope is confirmed, and sends the confirmation', async () => {
    const { approve } = panel({ ...OPENED, safetyConcerns: [FALSE_POSITIVE] }, []);
    const button = screen.getByRole<HTMLButtonElement>('button', { name: 'Approve revision 1' });
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByTestId('scope-confirm'));
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await waitFor(() => {
      expect(approve).toHaveBeenCalledTimes(1);
    });
    expect(approve.mock.calls[0]?.[0]).toMatchObject({ scopeConfirmed: true, revision: 1 });
    expect(await screen.findByText(/unresolved safety concerns/)).toBeTruthy();
  });

  it('records a resolution and reads the revision again', async () => {
    const { resolve, open, onOpened, reopened } = panel(OPENED, ['safety']);
    fireEvent.click(screen.getByTestId(`resolve-fp-mention-${ROAD.target_id}`));
    fireEvent.change(screen.getByTestId(`resolve-reason-mention-${ROAD.target_id}`), {
      target: { value: 'A street name in the site address.' },
    });
    fireEvent.click(screen.getByTestId(`resolve-submit-mention-${ROAD.target_id}`));
    await waitFor(() => {
      expect(onOpened).toHaveBeenCalledWith(reopened, false);
    });
    expect(resolve.mock.calls[0]?.[0]).toMatchObject({
      id: 'p1',
      revision: 1,
      concern: ROAD,
      resolution: 'false_positive',
    });
    expect(open).toHaveBeenCalledTimes(1);
  });
});

describe('the server’s flags', () => {
  it('reads the hold, the concerns and the eligibility, and holds readiness', () => {
    const project = {
      approval_held: { code: 'approval_blocked_by_safety', count: 2, codes: ['a', 'b'] },
      safety_concerns: [MENTION],
      safety_resolution: { eligible: false, reason: 'revision_author' },
    };
    expect(approvalHeldOf(project)).toEqual({ count: 2, codes: ['a', 'b'] });
    expect(safetyConcernsOf(project)).toEqual([MENTION]);
    expect(safetyResolutionOf(project)).toEqual({ eligible: false, reason: 'revision_author' });
    expect(approvalHeldOf({})).toBeNull();
    const readiness = readinessOf({
      opened: { approval: null },
      validation: {
        validated: true,
        findings: [],
        snapshotSha256: 'a',
        approvedSnapshotSha256: null,
        rulesVersion: 'r',
        exportStatus: 'draft',
        executionBlockers: [],
        approvalCurrent: null,
        approvalChanged: [],
        approvalHeld: { count: 2, codes: ['a'] },
      },
      dirty: false,
      stale: false,
    });
    expect(readiness.approvalBlockers).toEqual(['safety']);
    expect(readiness.canApprove).toBe(false);
  });

  it('sends the body the server expects', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ requirements: { schema: 'pp-req/1' } }), { status: 200 }),
      );
    await resolveSafetyConcern({
      token: 't',
      fetchImpl,
      id: 'p1',
      revision: 2,
      concern: ITEM,
      resolution: 'excluded_from_scope',
      reason: 'Designed separately by the contractor.',
    });
    const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/\/revisions\/2\/requirements\/safety-resolutions$/);
    expect(JSON.parse(init.body as string)).toEqual({
      item_id: 'i1',
      resolution: 'excluded_from_scope',
      reason: 'Designed separately by the contractor.',
    });
    await approveRevision({ token: 't', fetchImpl, id: 'p1', revision: 2, scopeConfirmed: true });
    const [, approval] = fetchImpl.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(approval.body as string)).toEqual({ note: '', scope_confirmed: true });
  });
});

describe('M6e: request and carried concerns', () => {
  const LOAD: SafetyConcern = {
    kind: 'request',
    target_id: 'r0123456789abcdef',
    level: 'life_safety',
    code: 'request_life_safety',
    label: 'fire pump',
    start: 0,
    end: 9,
    resolved: false,
    resolution: null,
    false_positive_refusal: 'life_safety',
    exclusion_refusal: 'in_request',
    carried: false,
    where: 'MDB › Fire pump FP-1',
  };
  const CARRIED: SafetyConcern = {
    ...MENTION,
    proposal_id: 'p-old',
    carried: true,
    raised_on: 1,
    false_positive_refusal: null,
  };

  it('says a life-safety load in the request is removed, never resolved', () => {
    renderApp(
      <SafetyConcerns
        concerns={[LOAD]}
        eligibility={{ eligible: true, reason: null }}
        onResolve={vi.fn()}
      />,
    );
    const key = `request-${LOAD.target_id}`;
    expect(screen.getByTestId(`resolve-remove-${key}`).textContent).toMatch(
      /remove it from the request/,
    );
    expect(screen.queryByTestId(`resolve-exclude-${key}`)).toBeNull();
    expect(screen.getByTestId(`safety-concern-${key}`).textContent).toMatch(/MDB › Fire pump FP-1/);
  });

  it.each(['en', 'ar', 'he'] as const)(
    'says on which revision a carried concern was raised (%s)',
    (locale) => {
      renderApp(
        <SafetyConcerns
          concerns={[CARRIED]}
          eligibility={{ eligible: true, reason: null }}
          onResolve={vi.fn()}
        />,
        { locale },
      );
      const text = screen.getByTestId(`carried-mention-p-old-${MENTION.target_id}`).textContent;
      expect(text).toMatch(/1/);
      expect(text).not.toMatch(locale === 'en' ? /^$/ : /raised on/);
    },
  );

  it('sends a request concern by request_id, a carried one with its proposal', async () => {
    const fetchImpl = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ requirements: { schema: 'pp-req/1' } }), { status: 200 }),
        ),
      );
    const base = { token: 't', fetchImpl, id: 'p1', revision: 2, reason: 'Designed elsewhere.' };
    await resolveSafetyConcern({ ...base, concern: LOAD, resolution: 'false_positive' });
    await resolveSafetyConcern({ ...base, concern: CARRIED, resolution: 'excluded_from_scope' });
    const bodies = fetchImpl.mock.calls.map(
      (call) => JSON.parse((call as [string, RequestInit])[1].body as string) as object,
    );
    expect(bodies[0]).toMatchObject({ request_id: LOAD.target_id });
    expect(bodies[0]).not.toHaveProperty('proposal_id');
    expect(bodies[1]).toMatchObject({ mention_id: MENTION.target_id, proposal_id: 'p-old' });
  });
});

describe('M6e: the per-load confirmation of a carried exclusion', () => {
  const CANDIDATES = [
    { board: 'MDB', load_index: 0, description: 'Booster pump', power_kw: '5.5', kind: 'motor' },
    { board: 'DB-2', load_index: 0, description: 'P-7', power_kw: '18.5', kind: 'motor' },
  ];
  const CARRIED_LOAD: SafetyConcern = {
    kind: 'request',
    target_id: 'r1111111111111111',
    level: 'life_safety',
    code: 'request_life_safety',
    label: 'fire pump',
    start: 0,
    end: 9,
    resolved: false,
    resolution: null,
    false_positive_refusal: 'life_safety',
    exclusion_refusal: null,
    carried: true,
    raised_on: 1,
    where: 'MDB › Fire pump',
    confirmation_candidates: CANDIDATES,
  };

  it.each(['en', 'ar', 'he'] as const)(
    'lists every candidate load and waits until all are ticked (%s)',
    async (locale) => {
      const onResolve = vi.fn().mockResolvedValue(null);
      renderApp(
        <SafetyConcerns
          concerns={[CARRIED_LOAD]}
          eligibility={{ eligible: true, reason: null }}
          onResolve={onResolve}
        />,
        { locale },
      );
      const key = `request-${CARRIED_LOAD.target_id}`;
      fireEvent.click(screen.getByTestId(`resolve-exclude-${key}`));
      const box = screen.getByTestId(`resolve-confirm-${key}`);
      expect(box.textContent).toMatch(/P-7/);
      expect(box.textContent).toMatch(/18\.5/);
      if (locale !== 'en') expect(box.textContent).not.toMatch(/is not that equipment/);
      fireEvent.change(screen.getByTestId(`resolve-reason-${key}`), {
        target: { value: 'Designed separately by the fire-protection contractor.' },
      });
      const submit = screen.getByTestId<HTMLButtonElement>(`resolve-submit-${key}`);
      expect(submit.disabled).toBe(true);
      fireEvent.click(screen.getByTestId('confirm-load-MDB-0'));
      expect(submit.disabled).toBe(true);
      fireEvent.click(screen.getByTestId('confirm-load-DB-2-0'));
      expect(submit.disabled).toBe(false);
      fireEvent.click(submit);
      await waitFor(() => {
        expect(onResolve).toHaveBeenCalledWith(
          CARRIED_LOAD,
          'excluded_from_scope',
          'Designed separately by the fire-protection contractor.',
          [
            { board: 'MDB', load_index: 0 },
            { board: 'DB-2', load_index: 0 },
          ],
        );
      });
    },
  );

  it('sends the confirmed loads to the server', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ requirements: { schema: 'pp-req/1' } }), { status: 200 }),
      );
    await resolveSafetyConcern({
      token: 't',
      fetchImpl,
      id: 'p1',
      revision: 2,
      concern: CARRIED_LOAD,
      resolution: 'excluded_from_scope',
      reason: 'Designed elsewhere by others.',
      notThisEquipment: [{ board: 'DB-2', load_index: 0 }],
    });
    const [, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(init.body as string)).toMatchObject({
      request_id: CARRIED_LOAD.target_id,
      not_this_equipment: [{ board: 'DB-2', load_index: 0 }],
    });
  });
});
