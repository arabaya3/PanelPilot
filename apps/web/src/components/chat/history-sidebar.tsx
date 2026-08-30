'use client';

import { useTranslations } from 'next-intl';
import { useCallback, useEffect, useState } from 'react';

import { listSessions, type SessionSummary } from '@/lib/sessions';

/**
 * The conversation history sidebar.
 *
 * Lets an engineer return to yesterday's session without re-describing the
 * problem. Rows are ordered by last activity, which is the order the API
 * returns them in — deliberately not re-sorted here, because the server knows
 * when each conversation was last added to and this component only knows what
 * it was sent.
 *
 * **RTL.** Placement is left to the document's writing direction rather than
 * pinned to a side: the sidebar is a flex sibling and its separator uses the
 * logical `border-e` (inline-end), so in Arabic or Hebrew the whole thing
 * moves to the visual right with no RTL-specific stylesheet. Hardcoding
 * `border-r` would put a rule down the middle of the page in those locales, allow-physical-property (named here only to say not to use it),
 * which is the failure the logical-properties approach exists to prevent.
 *
 * Pagination is by cursor, appended on demand. The list is not loaded in full
 * because a heavy user accumulates hundreds of sessions, and the sidebar only
 * ever shows a screenful.
 */
export function HistorySidebar({
  token,
  activeSessionId,
  onSelect,
  listImpl = listSessions,
  /** Bumped by the parent when a turn completes, so the list can refresh. */
  refreshKey = 0,
}: {
  token: string;
  activeSessionId: string | null;
  onSelect: (sessionId: string) => void;
  listImpl?: typeof listSessions;
  refreshKey?: number;
}) {
  const t = useTranslations('history');
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed' | 'unavailable'>('loading');
  const [loadingMore, setLoadingMore] = useState(false);

  // The first page, and again whenever the parent signals a change. Guarded so
  // a response that arrives after the component is gone — or after a newer
  // request has superseded it — cannot overwrite fresher state.
  useEffect(() => {
    let current = true;
    setStatus('loading');

    void listImpl({ token }).then((result) => {
      if (!current) return;
      if (result.kind === 'loaded') {
        setSessions(result.sessions);
        setCursor(result.nextCursor);
        setStatus('ready');
        return;
      }
      setStatus(result.kind === 'unavailable' ? 'unavailable' : 'failed');
    });

    return () => {
      current = false;
    };
  }, [token, listImpl, refreshKey]);

  const loadMore = useCallback(async () => {
    if (cursor === null || loadingMore) return;
    setLoadingMore(true);
    const result = await listImpl({ token, cursor });
    setLoadingMore(false);
    if (result.kind !== 'loaded') return;
    // Appended, and de-duplicated by id: a session that gained a turn between
    // two page fetches can legitimately appear on both, and rendering it twice
    // would look like duplicated history rather than a race.
    setSessions((previous) => {
      const seen = new Set(previous.map((row) => row.id));
      return [...previous, ...result.sessions.filter((row) => !seen.has(row.id))];
    });
    setCursor(result.nextCursor);
  }, [cursor, loadingMore, listImpl, token]);

  return (
    <nav
      aria-label={t('label')}
      data-testid="history-sidebar"
      className="flex w-64 shrink-0 flex-col gap-2 border-e border-border bg-surface p-3"
    >
      <h2 className="text-sm font-medium text-text-muted">{t('label')}</h2>

      {status === 'loading' && (
        <p className="text-sm text-text-muted" data-testid="history-loading">
          {t('loading')}
        </p>
      )}

      {status === 'unavailable' && (
        <p className="text-sm text-text-muted" data-testid="history-unavailable">
          {t('unavailable')}
        </p>
      )}

      {status === 'failed' && (
        <p className="text-sm text-text-muted" data-testid="history-failed">
          {t('failed')}
        </p>
      )}

      {status === 'ready' && sessions.length === 0 && (
        <p className="text-sm text-text-muted" data-testid="history-empty">
          {t('empty')}
        </p>
      )}

      {sessions.length > 0 && (
        <ul className="flex flex-col gap-1">
          {sessions.map((session) => {
            const active = session.id === activeSessionId;
            return (
              <li key={session.id}>
                <button
                  type="button"
                  onClick={() => {
                    onSelect(session.id);
                  }}
                  // `aria-current` rather than colour alone: which conversation
                  // is open must be available to a screen reader too.
                  aria-current={active ? 'true' : undefined}
                  data-testid={`history-item-${session.id}`}
                  data-active={active ? 'true' : 'false'}
                  className={
                    active
                      ? 'w-full rounded-md border border-border bg-surface-raised p-2 text-start text-sm text-text shadow-sm'
                      : 'w-full rounded-md border border-transparent p-2 text-start text-sm text-text-muted hover:bg-surface-raised'
                  }
                >
                  {/* `line-clamp` rather than a truncated string: the full
                      title stays in the accessible name and in the tooltip,
                      so a long question is shortened visually without being
                      lost to someone who cannot see the clamp. */}
                  <span className="line-clamp-2 break-words" title={session.title}>
                    {session.title}
                  </span>
                  {session.equipmentModel !== null && (
                    <span
                      className="mt-1 block text-xs text-text-muted"
                      data-testid={`history-model-${session.id}`}
                    >
                      {session.equipmentModel}
                    </span>
                  )}
                  <time className="mt-1 block text-xs text-text-muted" dateTime={session.updatedAt}>
                    {formatDay(session.updatedAt)}
                  </time>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {cursor !== null && (
        <button
          type="button"
          onClick={() => {
            void loadMore();
          }}
          disabled={loadingMore}
          data-testid="history-load-more"
          className="rounded-md border border-border p-2 text-sm text-text-muted hover:bg-surface-raised disabled:opacity-50"
        >
          {loadingMore ? t('loading') : t('loadMore')}
        </button>
      )}
    </nav>
  );
}

/**
 * A date the engineer can scan.
 *
 * Rendered from the locale's own formatter rather than a hand-built string, so
 * an Arabic or Hebrew locale gets its own calendar conventions. An unparseable
 * value renders as empty rather than `Invalid Date`, which would be the only
 * text on the row that looked like an error.
 */
function formatDay(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
}
