'use client';

import type { components } from '@panelpilot/shared-types';
import { useId, useState } from 'react';

type VerificationLabel = components['schemas']['VerificationLabel'];

/**
 * The three-button label control, with the note requirement enforced.
 *
 * The buttons are deliberately separated rather than a segmented control or a
 * dropdown. This is the most safety-critical work in the product, and the
 * failure mode worth designing against is a verifier clicking through a queue
 * on autopilot — adjacent identical-looking buttons are exactly what makes
 * that easy. Separation costs a moment per item and buys a moment of
 * deliberation.
 *
 * `incorrect` and `uncertain` require a note before the submit is possible,
 * and the requirement is enforced by disabling submission rather than by
 * validating afterwards. A lead receiving "incorrect" with no note has to redo
 * the verification from scratch to find out what was wrong, which is precisely
 * the work the label was supposed to save.
 */

/** Labels that cannot be submitted without a note. */
const REQUIRES_NOTE = new Set<VerificationLabel>(['incorrect', 'uncertain']);

/**
 * Report whether a label needs a note.
 *
 * @param label - The label under consideration.
 * @returns Whether a note is mandatory.
 *
 * Exported so the rule has one definition and the test can assert on it
 * directly rather than inferring it from a disabled attribute.
 */
export function requiresNote(label: VerificationLabel): boolean {
  return REQUIRES_NOTE.has(label);
}

/**
 * Report whether a submission may proceed.
 *
 * @param label - The chosen label, or null when none is chosen.
 * @param note - What the verifier has typed.
 * @returns Whether submitting is allowed.
 *
 * A pure function rather than a condition inline in the JSX: this is the rule
 * that stops an unexplained escalation reaching a lead, and it should be
 * testable without rendering anything.
 */
export function canSubmit(label: VerificationLabel | null, note: string): boolean {
  if (label === null) return false;
  if (!requiresNote(label)) return true;
  return note.trim().length > 0;
}

/**
 * The label control for one queue item.
 *
 * @param props - The item's id, the submit handler, and whether one is in flight.
 * @returns The control.
 */
export function Labeller({
  itemId,
  onSubmit,
  submitting = false,
  error,
  labels,
}: {
  itemId: string;
  onSubmit: (label: VerificationLabel, note: string) => void;
  submitting?: boolean;
  error?: string | null;
  labels: {
    correct: string;
    incorrect: string;
    uncertain: string;
    notePlaceholder: string;
    noteRequired: string;
    submit: string;
    submitting: string;
  };
}) {
  const [label, setLabel] = useState<VerificationLabel | null>(null);
  const [note, setNote] = useState('');
  const noteId = useId();
  const hintId = useId();

  const noteNeeded = label !== null && requiresNote(label);
  const allowed = canSubmit(label, note);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3" role="group" aria-label={labels.submit}>
        {(['correct', 'incorrect', 'uncertain'] as const).map((option) => (
          <button
            key={option}
            type="button"
            data-testid={`label-${option}`}
            aria-pressed={label === option}
            onClick={() => {
              setLabel(option);
            }}
            className={`rounded-md border-2 px-4 py-2 text-sm font-semibold ${
              label === option
                ? option === 'correct'
                  ? 'border-severity-info bg-severity-info-surface text-severity-info'
                  : option === 'incorrect'
                    ? 'border-severity-critical bg-severity-critical-surface text-severity-critical'
                    : 'border-severity-warning bg-severity-warning-surface text-severity-warning'
                : 'border-border bg-surface text-text'
            }`}
          >
            {labels[option]}
          </button>
        ))}
      </div>

      {noteNeeded && (
        <div>
          <label htmlFor={noteId} className="block text-sm font-medium text-text">
            {labels.noteRequired}
          </label>
          <textarea
            id={noteId}
            data-testid="note"
            aria-describedby={hintId}
            required
            value={note}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            placeholder={labels.notePlaceholder}
            rows={3}
            className="mt-1 w-full rounded-md border border-border bg-surface-raised p-2 font-sans text-sm text-text"
          />
          <p id={hintId} className="mt-1 text-xs text-text-muted">
            {labels.notePlaceholder}
          </p>
        </div>
      )}

      {error !== null && error !== undefined && error.length > 0 && (
        <p role="alert" data-testid="submit-error" className="text-sm text-severity-critical">
          {error}
        </p>
      )}

      <button
        type="button"
        data-testid="submit"
        disabled={!allowed || submitting}
        onClick={() => {
          if (label !== null) onSubmit(label, note);
        }}
        className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-contrast disabled:opacity-50"
        data-item={itemId}
      >
        {submitting ? labels.submitting : labels.submit}
      </button>
    </div>
  );
}
