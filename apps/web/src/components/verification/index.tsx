'use client';

import type { components } from '@panelpilot/shared-types';
import { useId, useState } from 'react';

import { EmptyState, Notice, StatusBadge, TableFrame } from '@/components/ui';

import { Labeller } from './labeller';

type QueueItem = components['schemas']['QueueItem'];
type FlaggedAnswerView = components['schemas']['FlaggedAnswerView'];
type VerificationLabel = components['schemas']['VerificationLabel'];

/**
 * The verification console.
 *
 * Ten engineers work this queue, and it is the most safety-critical surface in
 * the product. The design target is therefore not "possible" but "fast, and
 * hard to do carelessly" — which pulls in two directions and is resolved the
 * same way each time: make the careful path the quick one.
 *
 * Split-pane, because the alternative is a verifier opening the manual in
 * another tab. Once that happens the check becomes "does this look like what I
 * remember", which is not verification, and the queue's whole output quality
 * rests on it being verification.
 *
 * The claim race is handled by believing the server. Two verifiers can open
 * the same item — the queue is polled, not locked on render — and the loser
 * must be told rather than allowed a silent double-submit. BE-007's conditional
 * UPDATE decides the winner; this shows the answer.
 */

/** What the caller must supply to talk to the API. */
export interface VerificationApi {
  /** Submit a label. Rejects with a `ClaimConflict` if someone else has it. */
  submitLabel: (itemId: string, label: VerificationLabel, note: string) => Promise<void>;
}

/**
 * Raised when the server reports the item is already someone else's.
 *
 * A distinct type rather than a status code check at the call site, so the UI
 * can tell "you lost the race" from "the network is down" — those need
 * different words and different next steps.
 */
export class ClaimConflict extends Error {
  readonly claimedBy: string;

  constructor(claimedBy: string) {
    super(`already claimed by ${claimedBy}`);
    this.name = 'ClaimConflict';
    this.claimedBy = claimedBy;
  }
}

/** The strings this component renders. */
/** Strings for the queue overview table. */
export interface QueueTableLabels {
  /** Accessible name of the scrolling table region. */
  label: string;
  position: string;
  kind: string;
  location: string;
  status: string;
  kindFlag: string;
  kindContent: string;
  current: string;
  waiting: string;
}

export interface VerificationLabels {
  heading: string;
  /** Absent, the console shows the current item only, with no overview. */
  queue?: QueueTableLabels;
  empty: string;
  itemCount: string;
  proposed: string;
  source: string;
  sourceMissing: string;
  /** Shown when the item's own text could not be loaded. */
  contentMissing: string;
  /** The link that opens the source outside the frame. */
  openSource: string;
  /** Where in the source, e.g. "p. {page}". */
  page: string;
  correct: string;
  incorrect: string;
  uncertain: string;
  notePlaceholder: string;
  noteRequired: string;
  submit: string;
  submitting: string;
  claimedBy: string;
  submitFailed: string;
  /** Heading over an answer an engineer reported, in place of `proposed`. */
  reported: string;
  reportedQuestion: string;
  reportedAnswer: string;
  reportedReason: string;
  /** Shown when the engineer gave no reason. */
  reportedNoReason: string;
  reportedPassages: string;
  /** The answer was built on no passages at all. */
  reportedNoPassages: string;
  /** The stored passages could not be read -- not the same as none. */
  reportedContextMissing: string;
}

/**
 * An answer an engineer reported as wrong, as they saw it.
 *
 * The passages are the ones the answer was built on, captured when it was
 * reported: judging it against a fresh search would be judging an answer
 * nobody was given.
 */
export function ReportedAnswer({
  flag,
  labels,
}: {
  flag: FlaggedAnswerView;
  labels: VerificationLabels;
}) {
  const quote =
    'whitespace-pre-wrap rounded-md border border-border-subtle bg-surface-raised p-3 text-sm leading-relaxed text-text';
  return (
    <div data-testid="reported-answer" className="mb-3 space-y-3">
      <div>
        <h4 className="mb-1 text-sm font-semibold text-text">{labels.reportedQuestion}</h4>
        <p dir="auto" className={quote}>
          {flag.question}
        </p>
      </div>
      <div>
        <h4 className="mb-1 text-sm font-semibold text-text">{labels.reportedAnswer}</h4>
        <p dir="auto" className={`${quote} max-h-96 overflow-y-auto`}>
          {flag.answer}
        </p>
      </div>
      <div>
        <h4 className="mb-1 text-sm font-semibold text-text">{labels.reportedReason}</h4>
        {flag.reason ? (
          <p dir="auto" data-testid="reported-reason" className="text-sm text-text">
            {flag.reason}
          </p>
        ) : (
          <p className="text-sm text-text-muted">{labels.reportedNoReason}</p>
        )}
      </div>
      <div>
        <h4 className="mb-1 text-sm font-semibold text-text">{labels.reportedPassages}</h4>
        {flag.passages === null ? (
          <p data-testid="reported-context-missing" className="text-sm text-severity-warning">
            {labels.reportedContextMissing}
          </p>
        ) : flag.passages.length === 0 ? (
          <p data-testid="reported-no-passages" className="text-sm text-text-muted">
            {labels.reportedNoPassages}
          </p>
        ) : (
          <ol className="space-y-2">
            {flag.passages.map((passage) => (
              <li key={passage.id} data-testid="reported-passage">
                <p className="mb-1 font-mono text-xs text-text-muted">
                  {passage.citation.manufacturer} · {passage.citation.document_title}
                  {typeof passage.citation.page === 'number'
                    ? ` · ${labels.page.replace('{page}', String(passage.citation.page))}`
                    : ''}
                </p>
                <blockquote dir="auto" className={`${quote} max-h-40 overflow-y-auto`}>
                  {passage.text}
                </blockquote>
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

/**
 * One item under review, split between the proposal and its source.
 *
 * @param props - The item, its source, and how to submit.
 * @returns The pane.
 */
function ReviewPane({
  item,
  sourceUrl,
  onSubmit,
  submitting,
  error,
  labels,
}: {
  item: QueueItem;
  sourceUrl: string | null;
  onSubmit: (label: VerificationLabel, note: string) => void;
  submitting: boolean;
  error: string | null;
  labels: VerificationLabels;
}) {
  const proposedId = useId();
  const sourceId = useId();

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <section aria-labelledby={proposedId} className="card p-4 md:p-5">
        <h3 id={proposedId} className="eyebrow mb-2">
          {item.flag ? labels.reported : labels.proposed}
        </h3>
        <p data-testid="chunk-id" className="mb-3 font-mono text-xs text-text-muted">
          {item.chunk_id ?? item.id}
          {item.section ? ` · ${item.section}` : ''}
          {typeof item.page === 'number'
            ? ` · ${labels.page.replace('{page}', String(item.page))}`
            : ''}
        </p>
        {/* The text being judged. The console used to show only the id above,
            which left a verifier to label content they had never seen. */}
        {item.flag ? (
          <ReportedAnswer flag={item.flag} labels={labels} />
        ) : item.content ? (
          <blockquote
            data-testid="proposed-content"
            dir="auto"
            className="mb-3 max-h-96 overflow-y-auto whitespace-pre-wrap rounded-md border border-border-subtle bg-surface-raised p-3 text-sm leading-relaxed text-text"
          >
            {item.content}
          </blockquote>
        ) : (
          <p data-testid="content-missing" className="mb-3 text-sm text-severity-warning">
            {labels.contentMissing}
          </p>
        )}
        <Labeller
          itemId={item.id}
          onSubmit={onSubmit}
          submitting={submitting}
          error={error}
          labels={labels}
        />
      </section>

      <section aria-labelledby={sourceId} className="card p-4 md:p-5">
        <h3 id={sourceId} className="eyebrow mb-2">
          {labels.source}
        </h3>
        {sourceUrl === null ? (
          // Said plainly rather than showing an empty frame. A verifier facing
          // a blank pane cannot tell whether the source failed to load or the
          // item genuinely has none — and one of those means they must not
          // label it `correct`.
          <p data-testid="source-missing" className="text-sm text-severity-warning">
            {labels.sourceMissing}
          </p>
        ) : (
          <>
            <iframe
              data-testid="source-frame"
              src={sourceUrl}
              title={labels.source}
              className="h-96 w-full rounded-md border border-border-subtle bg-surface-raised"
            />
            {/* Many manufacturer sites refuse to be framed, which leaves the
                frame blank; the document must still be one click away. */}
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              data-testid="source-link"
              className="link mt-2 inline-block text-sm"
            >
              {labels.openSource}
            </a>
          </>
        )}
      </section>
    </div>
  );
}

/**
 * The verifier's queue and review surface.
 *
 * @param props - The queue, the API, the source resolver, and the strings.
 * @returns The console.
 */
export function VerificationConsole({
  items,
  api,
  sourceUrlFor,
  labels,
}: {
  items: readonly QueueItem[];
  api: VerificationApi;
  sourceUrlFor: (item: QueueItem) => string | null;
  labels: VerificationLabels;
}) {
  const headingId = useId();
  const [done, setDone] = useState<ReadonlySet<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const remaining = items.filter((item) => !done.has(item.id));
  const current = remaining[0];

  async function submit(label: VerificationLabel, note: string): Promise<void> {
    if (current === undefined) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.submitLabel(current.id, label, note);
      setDone((previous) => new Set(previous).add(current.id));
    } catch (caught) {
      if (caught instanceof ClaimConflict) {
        // The item is gone from this verifier's queue either way — someone
        // else holds it. Removing it and saying why is the honest outcome;
        // leaving it on screen invites a retry that will fail identically.
        setError(labels.claimedBy.replace('{name}', caught.claimedBy));
        setDone((previous) => new Set(previous).add(current.id));
      } else {
        setError(labels.submitFailed);
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby={headingId} className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id={headingId} className="text-xl font-bold text-text">
          {labels.heading}
        </h2>
        <p data-testid="remaining">
          <StatusBadge tone="accent">
            {labels.itemCount.replace('{count}', String(remaining.length))}
          </StatusBadge>
        </p>
      </div>

      {error !== null && current === undefined && (
        <Notice tone="critical" testId="queue-error">
          {error}
        </Notice>
      )}

      {labels.queue && remaining.length > 0 && (
        <QueueTable items={remaining} labels={labels} queue={labels.queue} />
      )}

      {current === undefined ? (
        <div data-testid="queue-empty">
          <EmptyState title={labels.empty} />
        </div>
      ) : (
        <ReviewPane
          key={current.id}
          item={current}
          sourceUrl={sourceUrlFor(current)}
          onSubmit={(label, note) => {
            void submit(label, note);
          }}
          submitting={submitting}
          error={error}
          labels={labels}
        />
      )}
    </section>
  );
}

/**
 * The queue at a glance: what is waiting and in what order. The first row is
 * the item open below; labelling it moves the next one up.
 */
function QueueTable({
  items,
  labels,
  queue,
}: {
  items: readonly QueueItem[];
  labels: VerificationLabels;
  queue: QueueTableLabels;
}) {
  return (
    <TableFrame label={queue.label}>
      <table className="data-table" data-testid="queue-table">
        <thead>
          <tr>
            <th scope="col">{queue.position}</th>
            <th scope="col">{queue.kind}</th>
            <th scope="col">{queue.location}</th>
            <th scope="col">{queue.status}</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const location = [
              item.section ?? null,
              typeof item.page === 'number'
                ? labels.page.replace('{page}', String(item.page))
                : null,
            ].filter((part): part is string => part !== null && part !== '');
            return (
              <tr
                key={item.id}
                data-testid={`queue-row-${item.id}`}
                aria-current={index === 0 ? 'true' : undefined}
              >
                <td className="tabular-nums">{index + 1}</td>
                <td>{item.flag ? queue.kindFlag : queue.kindContent}</td>
                <td dir="auto">{location.length > 0 ? location.join(', ') : '—'}</td>
                <td>
                  {index === 0 ? (
                    <StatusBadge tone="accent">{queue.current}</StatusBadge>
                  ) : (
                    <StatusBadge>{queue.waiting}</StatusBadge>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </TableFrame>
  );
}
