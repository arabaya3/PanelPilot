'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { useOutcomeText } from '@/components/design/note-text';
import {
  approveRevision,
  deleteProject,
  listProjects,
  openProject,
  reviseProject,
  saveProject,
  type ProjectDesignRequest,
  type ProjectPage,
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
  impls = {},
}: {
  token: string;
  /** The project as entered now, or null while the form is incomplete. */
  current: { name: string; request: ProjectDesignRequest } | null;
  opened: OpenedProject | null;
  /** `fill` is true when the project was opened, so the form takes it. */
  onOpened: (project: SavedProject, fill: boolean) => void;
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
    const outcome = opened
      ? await revise({ token, id: opened.id, request: current.request, note })
      : await save({ token, name: current.name, request: current.request, note });
    setWorking(false);
    if (outcome.kind === 'saved') {
      setNote('');
      setMessage(t('saved', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      void refresh();
    } else {
      setMessage(say(outcome));
    }
  }

  async function load(projectId: string, revision?: number) {
    setMessage(null);
    const outcome = await open({ token, id: projectId, revision: revision ?? null });
    if (outcome.kind === 'saved') onOpened(outcome.project, true);
    else setMessage(say(outcome));
  }

  async function sign() {
    if (opened === null || approver.trim() === '') return;
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
    } else {
      setMessage(say(outcome));
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
                  void load(opened.id, Number(event.target.value));
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
          <p className="text-sm font-medium text-accent" data-testid="project-approval">
            {t('approvedBy', {
              revision: opened.revision,
              name: opened.approval.by,
              date: opened.approval.at.slice(0, 10),
            })}
          </p>
        ) : (
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
              disabled={approver.trim() === '' || working}
              onClick={() => {
                void sign();
              }}
              className="btn btn-secondary btn-sm self-start sm:self-end"
            >
              {t('approve', { revision: opened.revision })}
            </button>
          </div>
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
                  void load(project.id);
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
