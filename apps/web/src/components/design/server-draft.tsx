'use client';

/**
 * Slice 1 of server-backed drafts: an explicit "Save as draft" for a saved
 * project, kept privately for the signed-in account. Nothing here writes on
 * its own: no autosave, no background or reconnect writes, and the copies
 * kept in this browser are never uploaded or deleted from here.
 *
 * A draft is unsaved work. It is never a revision, never approved or
 * exported; publishing it is the normal Save with every check.
 *
 * Its contents come from the server and are shown as text only.
 */

import { useLocale } from 'next-intl';
import { createPortal } from 'react-dom';
import { useEffect, useId, useRef, useState } from 'react';

import { Dialog } from '@/components/ui';
import { useIsolatingTranslations } from '@/components/design/bidi';
import type { DesignDraft } from '@/components/design/draft';
import { useOutcomeText } from '@/components/design/note-text';
import {
  deleteServerDraft,
  getServerDraft,
  newDraftAttempt,
  putServerDraft,
  type ServerDraftAttempt,
  type ServerDraftBody,
  type ServerDraftState,
} from '@/lib/design';

/** The work as a server draft keeps it: what this browser keeps, without whose or which project. */
export function serverBody(draft: DesignDraft): ServerDraftBody {
  const { session: _session, project: _project, ...work } = draft;
  return work;
}

/**
 * A draft body as text that compares equal however it travelled: the
 * server's JSON reorders keys and spells an absent field as null.
 */
export function draftText(value: unknown): string {
  const plain = (item: unknown): unknown =>
    Array.isArray(item)
      ? item.map(plain)
      : typeof item === 'object' && item !== null
        ? Object.fromEntries(
            Object.entries(item)
              .filter(([, inner]) => inner !== null && inner !== undefined)
              .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
              .map(([key, inner]) => [key, plain(inner)]),
          )
        : item;
  return JSON.stringify(plain(value));
}

/** What keeping the page's work in this browser came to, before it is replaced. */
export type PreserveOutcome = 'kept' | 'nothing' | 'failed' | 'blocked';

type Pending =
  | {
      kind: 'put';
      attempt: ServerDraftAttempt;
      ifMatch: string | null;
      base: number;
      body: ServerDraftBody;
      text: string;
    }
  | { kind: 'delete'; attempt: ServerDraftAttempt; ifMatch: string };

type Note =
  | { kind: 'key'; key: string; values?: Record<string, string | number> }
  | { kind: 'text'; text: string };

export function ServerDraft({
  token,
  project,
  body,
  dirty,
  locallyKept,
  preserveLocal,
  onRestore,
  onUnauthorized,
  impls = {},
}: {
  token: string;
  /** The saved project open: its id, the revision the work is made from, and the latest known. */
  project: { id: string; revision: number; latest: number };
  /** The work on the page as a draft keeps it; null when there is nothing to keep. */
  body: ServerDraftBody | null;
  dirty: boolean;
  /** Whether the work on the page is kept in this browser right now. */
  locallyKept: boolean;
  /** Keep the work on the page in this browser, apart from what replaces it. */
  preserveLocal: () => PreserveOutcome;
  /** Put a server draft on the page (only after `preserveLocal` kept the work there). */
  onRestore: (draft: { body: ServerDraftBody; baseRevision: number }) => void;
  onUnauthorized?: () => void;
  impls?: Partial<{
    get: typeof getServerDraft;
    put: typeof putServerDraft;
    remove: typeof deleteServerDraft;
  }>;
}) {
  const t = useIsolatingTranslations('design.serverDraft');
  const tUi = useIsolatingTranslations('ui');
  const say = useOutcomeText();
  const locale = useLocale();
  const id = useId();
  const { get = getServerDraft, put = putServerDraft, remove = deleteServerDraft } = impls;

  const [loaded, setLoaded] = useState<'loading' | 'ready' | 'unreadable' | 'none'>('loading');
  const [writesEnabled, setWritesEnabled] = useState(false);
  // The latest server state known for this project and account (live or deleted).
  const [known, setKnown] = useState<ServerDraftState | null>(null);
  // The work as last kept on (or restored from) the server, to tell whether it changed since.
  const [keptText, setKeptText] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note | null>(null);
  const [conflict, setConflict] = useState<ServerDraftState | null>(null);
  // Restoring would have replaced work that could not be kept in this browser first.
  const [preserveFailed, setPreserveFailed] = useState(false);
  const [mounted, setMounted] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    setMounted(true);
  }, []);

  // Answers for another project are dropped. Another account or tenant
  // remounts this panel (its key); a renewed token for the same account does
  // not, so an attempt in flight, and its retry, survive a token refresh.
  const scope = project.id;
  const scopeNow = useRef(scope);
  scopeNow.current = scope;
  const latestUnauthorized = useRef(onUnauthorized);
  latestUnauthorized.current = onUnauthorized;

  const bodyText = body === null ? null : draftText(body);

  /** Read the server's state; `quiet` keeps what is shown meanwhile (and focus with it). */
  async function load(quiet = false) {
    const asked = scope;
    if (!quiet) setLoaded('loading');
    const outcome = await get({ token, id: project.id });
    if (scopeNow.current !== asked) return;
    if (outcome.kind === 'found') {
      setWritesEnabled(outcome.lookup.writes_enabled);
      setKnown(outcome.lookup.draft ?? null);
      setLoaded('ready');
      return;
    }
    if (outcome.kind === 'unauthorized') latestUnauthorized.current?.();
    // A trial, a project no longer there, or an API without drafts (an older
    // version answers a bare 404): nothing to show.
    if (
      outcome.kind === 'refused' &&
      (outcome.code === undefined ||
        outcome.code === 'server_drafts_need_account' ||
        outcome.code === 'project_not_found')
    ) {
      setLoaded('none');
      return;
    }
    setLoaded('unreadable');
    // Said in the one live region (the block shown holds the Retry button).
    setNote({ kind: 'key', key: 'unreadable' });
  }

  useEffect(() => {
    setKnown(null);
    setKeptText(null);
    setPending(null);
    // An attempt in flight for the previous scope never answers here.
    setBusy(false);
    setNote(null);
    setConflict(null);
    setPreserveFailed(false);
    void load();
    // Once per project and identity: never on edits.
  }, [scope]);

  function failure(outcome: { kind: string; code?: string }, keep: boolean) {
    if (outcome.kind === 'unauthorized') {
      latestUnauthorized.current?.();
      setNote({ kind: 'key', key: 'sessionExpired' });
      if (!keep) setPending(null);
      return;
    }
    if (outcome.kind === 'failed') {
      // Not known to have reached the server: the same attempt can be sent again.
      setNote({ kind: 'key', key: 'failed' });
      if (!keep) setPending(null);
      return;
    }
    setPending(null);
    if (outcome.code === 'request_expired') {
      setNote({ kind: 'key', key: 'expired' });
    } else if (outcome.code === 'server_drafts_disabled') {
      setWritesEnabled(false);
      setNote({ kind: 'key', key: 'disabled' });
    } else {
      setNote({ kind: 'text', text: say(outcome) });
    }
  }

  async function showConflict(asked: string, mine: string) {
    const outcome = await get({ token, id: project.id });
    if (scopeNow.current !== asked) return;
    if (outcome.kind === 'found') {
      setKnown(outcome.lookup.draft ?? null);
      // The server already holds exactly this work (an earlier attempt that did arrive).
      const server = outcome.lookup.draft;
      if (server?.state === 'live' && server.body && draftText(server.body) === mine) {
        setKeptText(mine);
        setNote({ kind: 'key', key: 'saved' });
        return;
      }
      setConflict(server ?? null);
      return;
    }
    failure(outcome, false);
  }

  async function send(attempt: Pending) {
    const asked = scope;
    setPending(attempt);
    setBusy(true);
    setNote(null);
    const outcome =
      attempt.kind === 'put'
        ? await put({
            token,
            id: project.id,
            attempt: attempt.attempt,
            ifMatch: attempt.ifMatch,
            baseRevision: attempt.base,
            body: attempt.body,
          })
        : await remove({
            token,
            id: project.id,
            attempt: attempt.attempt,
            ifMatch: attempt.ifMatch,
          });
    if (scopeNow.current !== asked) return;
    setBusy(false);
    if (outcome.kind === 'kept') {
      setPending(null);
      if (outcome.draft.replayed) {
        // The first answer to this attempt: its token may be older than the
        // server's now, so the current state is read before the next save.
        // Not known to be what the server holds now, so not marked as kept.
        void load(true);
        setKeptText(null);
      } else {
        setKnown(outcome.draft);
        setKeptText(attempt.kind === 'put' ? attempt.text : null);
      }
      setNote({ kind: 'key', key: attempt.kind === 'put' ? 'saved' : 'deleted' });
      return;
    }
    if (outcome.kind === 'refused' && outcome.code === 'draft_conflict') {
      setPending(null);
      await showConflict(asked, attempt.kind === 'put' ? attempt.text : '');
      return;
    }
    failure(outcome, true);
  }

  function saveDraft(ifMatch: string | null = known?.token ?? null) {
    if (body === null || bodyText === null || busy) return;
    setConflict(null);
    setPreserveFailed(false);
    void send({
      kind: 'put',
      attempt: newDraftAttempt(),
      ifMatch,
      base: project.revision,
      body,
      text: bodyText,
    });
  }

  function deleteDraft() {
    if (known === null || known.state !== 'live' || busy) return;
    void send({ kind: 'delete', attempt: newDraftAttempt(), ifMatch: known.token });
  }

  /** Put `server` on the page, once the page's own work is kept in this browser. */
  /** Restore `server`; a state known only from a write answer (no body) is read first. */
  async function restoreLatest(server: ServerDraftState) {
    if (server.body) {
      restore(server);
      return;
    }
    const asked = scope;
    setBusy(true);
    const outcome = await get({ token, id: project.id });
    if (scopeNow.current !== asked) return;
    setBusy(false);
    if (outcome.kind !== 'found') {
      failure(outcome, false);
      return;
    }
    const latest = outcome.lookup.draft ?? null;
    setKnown(latest);
    // Changed on the server since: what it holds now is shown, not restored unseen.
    if (latest?.token !== server.token) {
      setNote({ kind: 'key', key: 'changedSince' });
      return;
    }
    restore(latest);
  }

  function restore(server: ServerDraftState) {
    if (server.state !== 'live' || !server.body) return;
    const kept = dirty ? preserveLocal() : 'nothing';
    if (kept === 'failed' || kept === 'blocked') {
      // Nothing is replaced: the work stays on the page, and the page says how
      // to keep it: in the dialog when it is open, else in the panel's one status.
      setPreserveFailed(true);
      if (conflict === null) setNote({ kind: 'key', key: 'preserveFailed' });
      return;
    }
    setPreserveFailed(false);
    setConflict(null);
    onRestore({ body: server.body, baseRevision: server.base_revision });
    setKeptText(draftText(server.body));
    setNote({ kind: 'key', key: kept === 'kept' ? 'restoredKept' : 'restored' });
  }

  if (loaded === 'none' || loaded === 'loading') return null;
  const live = known?.state === 'live' ? known : null;
  if (loaded === 'ready' && !writesEnabled && live === null) return null;

  const when = (iso: string) =>
    new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(iso),
    );
  const unchangedSinceKept = keptText !== null && bodyText === keptText;
  // A retry sends the very attempt that failed, only while the work is as it was then.
  const retryable =
    pending !== null && !busy && (pending.kind === 'delete' || pending.text === bodyText);

  const status = busy
    ? t('statusSaving')
    : unchangedSinceKept && live !== null
      ? t('statusSaved', { time: when(live.updated_at) })
      : !dirty
        ? null
        : locallyKept
          ? t('statusLocal')
          : t('statusNotSaved');

  const noteText =
    note === null ? null : note.kind === 'text' ? note.text : t(note.key, note.values ?? {});

  return (
    <section
      aria-labelledby={`${id}-heading`}
      className="mt-3 rounded-md border border-border p-3 text-sm"
      data-testid="server-draft"
    >
      <h3 id={`${id}-heading`} ref={headingRef} tabIndex={-1} className="font-semibold">
        {t('heading')}
      </h3>
      {/* Where the work stands, shown (not announced): the one live region below says what happened. */}
      <p className="mt-1" data-testid="server-draft-status">
        {status}
      </p>
      {loaded === 'unreadable' ? (
        <p className="mt-1" data-testid="server-draft-unreadable">
          <button type="button" className="btn btn-sm btn-secondary" onClick={() => void load()}>
            {t('retry')}
          </button>
        </p>
      ) : null}
      {live !== null && !unchangedSinceKept ? (
        <div className="mt-2" data-testid="server-draft-kept">
          <p>
            {t('kept', { time: when(live.updated_at), revision: live.base_revision })}
            {live.base_revision < project.latest
              ? ` ${t('older', { revision: live.base_revision, latest: project.latest })}`
              : ''}
          </p>
          <div className="mt-1 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={busy}
              onClick={() => {
                void restoreLatest(live);
              }}
              data-testid="server-draft-restore"
            >
              {t('restore')}
            </button>
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={busy}
              onClick={deleteDraft}
              data-testid="server-draft-delete"
            >
              {t('delete')}
            </button>
          </div>
        </div>
      ) : null}
      {live !== null && unchangedSinceKept && live.base_revision < project.latest ? (
        <p className="mt-1">
          {t('older', { revision: live.base_revision, latest: project.latest })}
        </p>
      ) : null}
      {writesEnabled ? (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={busy || body === null || unchangedSinceKept}
            onClick={() => {
              saveDraft();
            }}
            data-testid="server-draft-save"
          >
            {t('save')}
          </button>
          {retryable ? (
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              onClick={() => {
                void send(pending);
              }}
              data-testid="server-draft-retry"
            >
              {t('retry')}
            </button>
          ) : null}
          <span className="text-text-muted">{t('explain')}</span>
        </div>
      ) : (
        <p className="mt-1 text-text-muted">{t('disabled')}</p>
      )}
      {/* The panel's one live region: always present, so each outcome is announced once. */}
      <p role="status" className="mt-1 empty:hidden" data-testid="server-draft-note">
        {noteText}
      </p>
      {mounted
        ? createPortal(
            <Dialog
              open={conflict !== null}
              initialFocus={cancelRef}
              onClosed={(returned) => {
                // What opened it may be gone or disabled now: focus goes to the panel.
                if (!returned) headingRef.current?.focus();
              }}
              onClose={() => {
                setConflict(null);
                setPreserveFailed(false);
              }}
              title={t('conflictTitle')}
              closeLabel={tUi('close')}
              footer={
                <div
                  className="flex flex-wrap justify-end gap-2"
                  data-testid="server-draft-conflict"
                >
                  <button
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={() => {
                      setConflict(null);
                      setPreserveFailed(false);
                    }}
                    ref={cancelRef}
                    data-testid="conflict-cancel"
                  >
                    {t('conflictCancel')}
                  </button>
                  {conflict?.state === 'live' && conflict.body ? (
                    <button
                      type="button"
                      className="btn btn-sm btn-secondary"
                      onClick={() => {
                        restore(conflict);
                      }}
                      data-testid="conflict-use-server"
                    >
                      {t('conflictUseServer')}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    disabled={body === null || !writesEnabled}
                    onClick={() => {
                      saveDraft(conflict?.token ?? null);
                    }}
                    data-testid="conflict-keep-mine"
                  >
                    {t('conflictKeepMine')}
                  </button>
                </div>
              }
            >
              <p>{t('conflictIntro')}</p>
              <ul className="mt-2 list-disc ps-5">
                <li data-testid="conflict-mine">
                  {t('conflictMine', { revision: project.revision })}
                </li>
                <li data-testid="conflict-server">
                  {conflict?.state === 'live'
                    ? t('conflictServer', {
                        time: when(conflict.updated_at),
                        revision: conflict.base_revision,
                        name: conflict.body?.info.name ?? '',
                      })
                    : conflict
                      ? t('conflictDeleted', { time: when(conflict.updated_at) })
                      : null}
                </li>
              </ul>
              <p className="mt-2">{t('conflictKeepMineHint')}</p>
              {preserveFailed ? (
                <p role="alert" className="mt-2" data-testid="conflict-preserve-failed">
                  {t('preserveFailed')}
                </p>
              ) : null}
            </Dialog>,
            document.body,
          )
        : null}
    </section>
  );
}
