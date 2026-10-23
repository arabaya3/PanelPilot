'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { useOutcomeText } from '@/components/design/note-text';
import type { ApprovalBlocker } from '@/components/design/readiness';
import {
  approveRevision,
  deleteProject,
  listProjects,
  openProject,
  reviseProject,
  saveProject,
  type ProjectDesignRequest,
  type ProjectPage,
  type Refusal,
  type SavedProject,
} from '@/lib/design';

/** The project being worked on, once saved or opened. */
export type OpenedProject = {
  id: string;
  name: string;
  revision: number;
  revisions: number[];
  /** Who approved the revision open, and when (ISO 8601); null while not approved. */
  approval: { by: string; at: string } | null;
};

/** One line of the title block's revision list. */
export type TitleRevision = NonNullable<ProjectDesignRequest['info']['revisions']>[number];

/** Who approved a saved project's open revision, and when; null while not approved. */
export function approvalOf(project: SavedProject): OpenedProject['approval'] {
  const revision = project.revisions.find((entry) => entry.number === project.revision);
  return revision?.approved_by && revision.approved_at
    ? { by: revision.approved_by, at: revision.approved_at }
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
  beforeOpen = (open) => {
    open();
  },
  dirty = false,
  blockers = [],
  blockingCount = 0,
  approvalCovers = true,
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
  impls?: Partial<{
    list: typeof listProjects;
    save: typeof saveProject;
    revise: typeof reviseProject;
    open: typeof openProject;
    remove: typeof deleteProject;
    approve: typeof approveRevision;
  }>;
}) {
  const t = useTranslations('design.projects');
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
  const [note, setNote] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [approver, setApprover] = useState('');
  // Anything besides unsaved changes that keeps the revision from approval.
  const reasons = blockers.filter((blocker) => blocker !== 'dirty' && blocker !== 'unsaved');
  const held = dirty || reasons.length > 0;

  const refresh = useCallback(async () => {
    const outcome = await list({ token });
    if (outcome.kind === 'listed') setPage(outcome.page);
    else setMessage(say(outcome));
  }, [list, token, say]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function store() {
    if (current === null) return;
    setWorking(true);
    setMessage(null);
    onSaveState?.('saving');
    const outcome = opened
      ? await revise({ token, id: opened.id, request: current.request, note })
      : await save({ token, name: current.name, request: current.request, note });
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
      setMessage(outcome.kind === 'refused' ? say(outcome) : t('saveFailed'));
    }
  }

  function load(projectId: string, revision?: number) {
    beforeOpen(() => {
      void fetchAndOpen(projectId, revision);
    });
  }

  async function fetchAndOpen(projectId: string, revision?: number) {
    setMessage(null);
    const outcome = await open({ token, id: projectId, revision: revision ?? null });
    if (outcome.kind === 'saved') onOpened(outcome.project, true);
    else setMessage(say(outcome));
  }

  async function sign() {
    if (opened === null || approver.trim() === '' || held) return;
    setWorking(true);
    setMessage(null);
    const outcome = await approve({
      token,
      id: opened.id,
      revision: opened.revision,
      approver: approver.trim(),
    });
    setWorking(false);
    if (outcome.kind === 'saved') {
      setApprover('');
      setMessage(t('approvedNow', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      return;
    }
    setMessage(say(outcome));
    // Someone else approved it first: show their approval, keeping the form as it is.
    if (outcome.kind === 'refused' && outcome.code === 'revision_already_approved') {
      const reopened = await open({ token, id: opened.id, revision: opened.revision });
      if (reopened.kind === 'saved') onOpened(reopened.project, false);
    }
  }

  async function erase(projectId: string) {
    setConfirming(null);
    const outcome = await remove({ token, id: projectId });
    if (outcome.kind === 'deleted') void refresh();
    else setMessage(say(outcome));
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
                name: opened.approval.by,
                date: opened.approval.at.slice(0, 10),
              })}
            </p>
            {/* An approval never reads as covering what it did not sign. */}
            {!approvalCovers && (
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
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <Field id={`${id}-approver`} label={t('approver')}>
                <input
                  id={`${id}-approver`}
                  value={approver}
                  maxLength={100}
                  onChange={(event) => {
                    setApprover(event.target.value);
                  }}
                  className="input w-full"
                />
              </Field>
              <button
                type="button"
                disabled={approver.trim() === '' || working || held}
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
          disabled={current === null || working}
          onClick={() => {
            void store();
          }}
          className="btn btn-primary btn-sm self-start sm:self-end"
        >
          {opened ? t('saveRevision') : t('save')}
        </button>
      </div>
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
                onClick={() => {
                  load(project.id);
                }}
                className="btn btn-sm btn-secondary"
              >
                {t('open')}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (confirming === project.id) void erase(project.id);
                  else setConfirming(project.id);
                }}
                className="btn btn-sm btn-secondary"
              >
                {confirming === project.id ? t('confirmDelete') : t('delete')}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
