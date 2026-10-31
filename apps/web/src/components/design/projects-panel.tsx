'use client';

import { useLocale } from 'next-intl';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { useIsolatingTranslations } from '@/components/design/bidi';
import { useOutcomeText } from '@/components/design/note-text';
import {
  listOf,
  type ApprovalBlocker,
  type ApprovalPolicyPart,
} from '@/components/design/readiness';
import {
  approveRevision,
  deleteProject,
  listProjects,
  openProject,
  permits,
  reviseProject,
  saveProject,
  type ProjectDesignRequest,
  type ProjectPage,
  type Refusal,
  type SavedProject,
} from '@/lib/design';

/** Approval refusals after which the revision is read again, so the page shows the server's state. */
const REOPEN_ON = new Set([
  'revision_already_approved',
  'revision_stale',
  'revision_not_validated',
  // The account's permissions changed since the revision was opened.
  'approval_not_permitted',
]);

/** The project being worked on, once saved or opened. */
export type OpenedProject = {
  id: string;
  name: string;
  revision: number;
  revisions: number[];
  /**
   * Who approved the revision open, and when (ISO 8601); null while not
   * approved. `by` is the approving account's display name as the server
   * resolved it (from the approver's token, never from the page), or null
   * when it has none, which reads as "a team member". Its email is never
   * sent. `note` is the note added at approval, never who approved.
   */
  approval: { by: string | null; at: string; note: string | null } | null;
  /**
   * The signed-in account's display name, as an approval made now would name
   * it (from the server); null reads as "a team member".
   */
  approvingAs: string | null;
  /**
   * Whether the signed-in account may approve (the tenant's owner, or a
   * reviewer), as the server said; the server checks again when it is asked.
   */
  canApprove: boolean;
};

/** One line of the title block's revision list. */
export type TitleRevision = NonNullable<ProjectDesignRequest['info']['revisions']>[number];

/** Who approved a saved project's open revision, and when; null while not approved. */
export function approvalOf(project: SavedProject): OpenedProject['approval'] {
  const revision = project.revisions.find((entry) => entry.number === project.revision);
  return revision?.approved_at
    ? {
        by: revision.approver_name ?? null,
        at: revision.approved_at,
        note: revision.approval_note ?? null,
      }
    : null;
}

/**
 * The engineer's saved projects: save what is on the page (a new project, or
 * a new revision of the one open), open one or an earlier revision of it, or
 * delete one. Saving keeps what was entered; opening designs it afresh. The
 * engineer approves the revision open by name, once; its title block then
 * says so, and a project with an approved revision cannot be deleted.
 */
export function ProjectsPanel({
  token,
  current,
  opened,
  onOpened,
  onSaveState,
  onRefused,
  onUnauthorized,
  beforeOpen = (open) => {
    open();
  },
  dirty = false,
  blockers = [],
  blockingCount = 0,
  approvalCovers = true,
  approvalOutdated = [],
  trial = false,
  note: heldNote,
  onNoteChange,
  impls = {},
}: {
  token: string;
  /** The project as entered now, or null while the form is incomplete. */
  current: { name: string; request: ProjectDesignRequest } | null;
  opened: OpenedProject | null;
  /** `fill` is true when the project was opened, so the form takes it. */
  onOpened: (project: SavedProject, fill: boolean) => void;
  /** Told when a save starts and how it ended, so the page can show where the work stands. */
  onSaveState?: (state: 'saving' | 'saved' | 'failed') => void;
  /** Told what a refused save objected to, so the page can mark the fields at fault. */
  onRefused?: (refusal: Refusal) => void;
  /** Told when the server no longer accepts the token, so the page can renew it or ask to sign in. */
  onUnauthorized?: () => void;
  /** Asked before opening a project replaces what is on the page; calls `open` to go ahead. */
  beforeOpen?: (open: () => void) => void;
  /** Whether the page has changes not saved, which an approval would not include. */
  dirty?: boolean;
  /**
   * Why the revision open cannot be approved besides unsaved changes (not
   * validated, blocking findings, rules changed since). The server refuses
   * these too; the button says so before it is pressed.
   */
  blockers?: ApprovalBlocker[];
  /** How many blocking findings the revision open has. */
  blockingCount?: number;
  /** False when the stored approval does not sign the revision's validated snapshot. */
  approvalCovers?: boolean;
  /** What changed since the approval was granted, when it no longer counts. */
  approvalOutdated?: readonly ApprovalPolicyPart[];
  /** Whether the page is a trial's: a trial has no account, so it cannot approve. */
  trial?: boolean;
  /**
   * The revision note, when the page keeps it (so it outlives this panel);
   * kept here otherwise. Both or neither.
   */
  note?: string;
  onNoteChange?: (note: string) => void;
  impls?: Partial<{
    list: typeof listProjects;
    save: typeof saveProject;
    revise: typeof reviseProject;
    open: typeof openProject;
    remove: typeof deleteProject;
    approve: typeof approveRevision;
  }>;
}) {
  const t = useIsolatingTranslations('design.projects');
  const locale = useLocale();
  const ta = useIsolatingTranslations('design.account');
  const say = useOutcomeText();
  const id = useId();
  const {
    list = listProjects,
    save = saveProject,
    revise = reviseProject,
    open = openProject,
    remove = deleteProject,
    approve = approveRevision,
  } = impls;
  const [page, setPage] = useState<ProjectPage | null>(null);
  const [ownNote, setOwnNote] = useState('');
  const note = heldNote ?? ownNote;
  const setNote = onNoteChange ?? setOwnNote;
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [approvalNote, setApprovalNote] = useState('');
  // Anything besides unsaved changes that keeps the revision from approval.
  const reasons = blockers.filter((blocker) => blocker !== 'dirty' && blocker !== 'unsaved');
  // Not the owner or a reviewer: the server would refuse (`approval_not_permitted`).
  const notPermitted = !trial && opened !== null && !opened.canApprove;
  const held = trial || notPermitted || dirty || reasons.length > 0;
  const canDelete = permits(page, 'delete_project');
  // Saving the revision open again, unchanged, is how it is designed and
  // approved under today's rules and renderer: when its approval no longer
  // counts or covers it, or it is stale or not validated (the page tells the
  // user to do exactly that). The old revision and its approval stay as they are.
  const saveAgainHelps =
    approvalOutdated.length > 0 ||
    (opened?.approval != null && !approvalCovers) ||
    reasons.includes('stale') ||
    reasons.includes('notValidated');
  // The revision open, unchanged and current: saving it again would only store a copy.
  const unchanged = opened !== null && !dirty && !saveAgainHelps;

  // Read through refs: the page hands a new `onUnauthorized` (and the
  // translator a new `say`) on every render, and listing again on each one
  // turned every answer into another request, a 429 included, until the
  // design budget for the address was spent.
  const latestSay = useRef(say);
  latestSay.current = say;
  const latestOnUnauthorized = useRef(onUnauthorized);
  latestOnUnauthorized.current = onUnauthorized;
  // Rate limited: no list is asked for again until the server's Retry-After
  // has passed, whatever else changes meanwhile.
  const quietUntil = useRef(0);
  // Each save, open and approval takes the next number; only the latest one's
  // answer is applied. A save that answers after an open began would
  // otherwise attach the form now on the page to the project it saved.
  const sequence = useRef(0);

  const refresh = useCallback(async () => {
    if (Date.now() < quietUntil.current) return;
    const outcome = await list({ token });
    if (outcome.kind === 'listed') {
      setPage(outcome.page);
      return;
    }
    if (outcome.kind === 'unauthorized') latestOnUnauthorized.current?.();
    if (outcome.kind === 'refused' && outcome.code === 'rate_limited') {
      quietUntil.current = Date.now() + (outcome.retryAfterSeconds ?? 60) * 1000;
    }
    setMessage(latestSay.current(outcome));
  }, [list, token]);

  // Once per identity (token): not per render.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function store() {
    if (current === null || working || unchanged) return;
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    onSaveState?.('saving');
    const outcome = opened
      ? await revise({ token, id: opened.id, request: current.request, note })
      : await save({ token, name: current.name, request: current.request, note });
    // Overtaken by a later open: what is on the page now is not what was
    // saved, so neither the page's baseline nor its project is touched.
    if (ticket !== sequence.current) {
      if (outcome.kind === 'saved') void refresh();
      return;
    }
    setWorking(false);
    onSaveState?.(outcome.kind === 'saved' ? 'saved' : 'failed');
    if (outcome.kind === 'saved') {
      setNote('');
      setMessage(t('saved', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      void refresh();
    } else {
      // A refusal says what the API objected to; anything else is a save that
      // did not happen, not a design that failed. What was entered stays.
      if (outcome.kind === 'refused') onRefused?.(outcome);
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setMessage(
        outcome.kind === 'refused'
          ? say(outcome)
          : outcome.kind === 'unauthorized'
            ? ta('saveUnauthorized')
            : t('saveFailed'),
      );
    }
  }

  function load(projectId: string, revision?: number) {
    beforeOpen(() => {
      void fetchAndOpen(projectId, revision);
    });
  }

  async function fetchAndOpen(projectId: string, revision?: number) {
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    const outcome = await open({ token, id: projectId, revision: revision ?? null });
    if (ticket !== sequence.current) return;
    setWorking(false);
    if (outcome.kind === 'saved') onOpened(outcome.project, true);
    else {
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setMessage(say(outcome));
    }
  }

  async function sign() {
    if (opened === null || held || working) return;
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    const outcome = await approve({
      token,
      id: opened.id,
      revision: opened.revision,
      note: approvalNote.trim(),
    });
    if (ticket !== sequence.current) return;
    setWorking(false);
    if (outcome.kind === 'saved') {
      setApprovalNote('');
      setMessage(t('approvedNow', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    setMessage(say(outcome));
    // Someone else approved it first, or the server no longer holds it as
    // validated or current: read it again so the readiness shown is the
    // server's, keeping the form as it is.
    if (outcome.kind === 'refused' && outcome.code !== undefined && REOPEN_ON.has(outcome.code)) {
      const reopened = await open({ token, id: opened.id, revision: opened.revision });
      if (ticket !== sequence.current) return;
      if (reopened.kind === 'saved') onOpened(reopened.project, false);
    }
  }

  async function erase(projectId: string) {
    setConfirming(null);
    if (working) return;
    const outcome = await remove({ token, id: projectId });
    if (outcome.kind === 'deleted') void refresh();
    else {
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setMessage(say(outcome));
      // No longer the owner: list again, so delete is no longer offered.
      if (outcome.kind === 'refused' && outcome.code === 'project_delete_not_permitted') {
        void refresh();
      }
    }
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="projects-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      {opened && (
        <div className="flex flex-wrap items-end gap-3 text-sm" data-testid="project-opened">
          <span>{t('opened', { name: opened.name, revision: opened.revision })}</span>
          {opened.revisions.length > 1 && (
            <Field id={`${id}-revision`} label={t('revision')}>
              <select
                id={`${id}-revision`}
                value={opened.revision}
                disabled={working}
                onChange={(event) => {
                  load(opened.id, Number(event.target.value));
                }}
                className="input w-full sm:w-64"
              >
                {opened.revisions.map((number) => (
                  <option key={number} value={number}>
                    {number}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </div>
      )}
      {opened &&
        (opened.approval ? (
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium text-accent" data-testid="project-approval">
              {t('approvedBy', {
                revision: opened.revision,
                name: opened.approval.by ?? t('teamMember'),
                date: opened.approval.at.slice(0, 10),
              })}
            </p>
            {opened.approval.note && (
              <p className="text-sm text-text-muted" data-testid="project-approval-note">
                {t('approvalNoteShown', { note: opened.approval.note })}
              </p>
            )}
            {/* An approval never reads as covering what it did not sign, nor
                as counting once the policy it was granted under changed. */}
            {approvalOutdated.length > 0 && (
              <p className="text-sm text-text-muted" data-testid="approval-not-current">
                {t('approvalNotCurrent', {
                  revision: opened.revision,
                  changes: listOf(
                    locale,
                    approvalOutdated.map((part) => t(`approvalParts.${part}`)),
                  ),
                })}
              </p>
            )}
            {!approvalCovers && approvalOutdated.length === 0 && (
              <p className="text-sm text-text-muted" data-testid="approval-not-covering">
                {t('approvalNotCovering', { revision: opened.revision })}
              </p>
            )}
            {dirty && (
              <p className="text-sm text-text-muted" data-testid="approval-changed">
                {t('approvalChanged', { revision: opened.revision })}
              </p>
            )}
          </div>
        ) : (
          <>
            {!trial && (
              <p className="text-sm text-text-muted" data-testid="approving-as">
                {opened.approvingAs === null
                  ? t('approvingAsUnnamed')
                  : t('approvingAs', { name: opened.approvingAs })}
              </p>
            )}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <Field id={`${id}-approval-note`} label={t('approvalNote')}>
                <input
                  id={`${id}-approval-note`}
                  value={approvalNote}
                  maxLength={200}
                  disabled={trial || notPermitted}
                  onChange={(event) => {
                    setApprovalNote(event.target.value);
                  }}
                  className="input w-full"
                />
              </Field>
              <button
                type="button"
                disabled={working || held}
                aria-describedby={held ? `${id}-approve-held` : undefined}
                onClick={() => {
                  void sign();
                }}
                className="btn btn-secondary btn-sm self-start sm:self-end"
              >
                {t('approve', { revision: opened.revision })}
              </button>
            </div>
            {held && (
              <div id={`${id}-approve-held`} className="flex flex-col gap-1">
                {trial && (
                  <p className="text-sm text-text-muted" data-testid="approve-held-trial">
                    {t('approveTrial')}
                  </p>
                )}
                {notPermitted && (
                  <p className="text-sm text-text-muted" data-testid="approve-not-permitted">
                    {t('approveNotPermitted')}
                  </p>
                )}
                {dirty && (
                  <p className="text-sm text-text-muted" data-testid="approve-dirty">
                    {t('approveDirty', { revision: opened.revision })}
                  </p>
                )}
                {reasons.map((reason) => (
                  <p
                    key={reason}
                    className="text-sm text-text-muted"
                    data-testid={`approve-held-${reason}`}
                  >
                    {t(`approveHeld.${reason}`, {
                      revision: opened.revision,
                      count: blockingCount,
                    })}
                  </p>
                ))}
              </div>
            )}
          </>
        ))}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field id={`${id}-note`} label={t('note')}>
          <input
            id={`${id}-note`}
            value={note}
            maxLength={500}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            className="input w-full"
          />
        </Field>
        <button
          type="button"
          disabled={current === null || working || unchanged}
          aria-describedby={unchanged ? `${id}-save-unchanged` : undefined}
          onClick={() => {
            void store();
          }}
          className="btn btn-primary btn-sm self-start sm:self-end"
        >
          {opened ? t('saveRevision') : t('save')}
        </button>
      </div>
      {opened && unchanged && (
        <p
          id={`${id}-save-unchanged`}
          className="text-sm text-text-muted"
          data-testid="save-unchanged"
        >
          {t('saveUnchanged', { revision: opened.revision })}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm" data-testid="projects-message">
          {message}
        </p>
      )}
      {page && page.projects.length === 0 && <p className="text-sm text-text-muted">{t('none')}</p>}
      {page && page.projects.length > 0 && (
        <ul className="flex flex-col divide-y divide-border-subtle text-sm">
          {page.projects.map((project) => (
            <li
              key={project.id}
              data-testid={`project-${project.id}`}
              className="flex flex-wrap items-center gap-2 py-2"
            >
              <span className="min-w-0 flex-1 truncate font-medium" dir="auto">
                {project.name}
              </span>
              <span className="text-text-muted">
                {t('revisions', { count: project.revisions })}
              </span>
              <button
                type="button"
                disabled={working}
                onClick={() => {
                  load(project.id);
                }}
                className="btn btn-sm btn-secondary"
              >
                {t('open')}
              </button>
              {/* Only the owner deletes; the server refuses anyone else. */}
              {canDelete && (
                <button
                  type="button"
                  disabled={working}
                  onClick={() => {
                    if (confirming === project.id) void erase(project.id);
                    else setConfirming(project.id);
                  }}
                  className="btn btn-sm btn-secondary"
                >
                  {confirming === project.id ? t('confirmDelete') : t('delete')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
