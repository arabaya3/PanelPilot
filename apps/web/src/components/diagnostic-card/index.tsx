'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId } from 'react';

import { useChecklist } from '@/components/chat/checklist-provider';
import { TechnicalToken } from '@/components/technical-token';

import { decideVariant, type CardVariant, type ResolvedStep } from './citations';

type Severity = components['schemas']['Severity'];
type Citation = components['schemas']['Citation'];
type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

/**
 * The diagnostic response card.
 *
 * This card is the product's primary trust signal: the "cite-or-refuse, always
 * show your source" promise made visible. Everything here follows from that.
 *
 * Three variants, and which one renders is decided in `citations.ts` before
 * any markup is chosen — so the rule that an uncited claim is never displayed
 * as a confident one is one testable function, not a condition scattered
 * across five zones of JSX.
 *
 * The uncertain variant is deliberately *not* a confident card with a banner
 * bolted on. It withholds the cause, the steps and the measurements entirely,
 * because a card that shows all of its content plus a caption saying "we are
 * not sure" is read as an answer with a disclaimer. An engineer under time
 * pressure at a panel reads the steps and not the caption. Withholding the
 * content is the only version of this that survives contact with a hurry.
 */

/**
 * Severity to token classes.
 *
 * Spelled out per severity rather than interpolated into a class name: Tailwind
 * scans source text for complete class names, so `bg-severity-${severity}` is
 * not in the compiled stylesheet and silently renders unstyled. This is also
 * the mechanism that keeps the acceptance criterion's "exactly one of three
 * token colours, never an arbitrary colour" true — there is nowhere here to
 * put an arbitrary colour.
 */
const SEVERITY_CLASSES: Record<Severity, { border: string; badge: string; surface: string }> = {
  critical: {
    border: 'border-severity-critical',
    badge: 'bg-severity-critical text-severity-critical-surface',
    surface: 'bg-severity-critical-surface',
  },
  warning: {
    border: 'border-severity-warning',
    badge: 'bg-severity-warning text-severity-warning-surface',
    surface: 'bg-severity-warning-surface',
  },
  info: {
    border: 'border-severity-info',
    badge: 'bg-severity-info text-severity-info-surface',
    surface: 'bg-severity-info-surface',
  },
};

/** One citation, rendered so it can actually be followed back to a document. */
function CitationLine({ citation }: { citation: Citation }) {
  const t = useTranslations('diagnosticCard');
  const locator = [
    citation.section ? t('section', { section: citation.section }) : null,
    citation.page !== null && citation.page !== undefined
      ? t('page', { page: citation.page })
      : null,
  ].filter(Boolean);

  return (
    <li className="text-sm text-text-muted">
      <TechnicalToken>{citation.manufacturer}</TechnicalToken>{' '}
      <TechnicalToken className="font-sans">{citation.document_title}</TechnicalToken>
      {locator.length > 0 ? <span> — {locator.join(', ')}</span> : null}
    </li>
  );
}

/** The citation list, which never renders as an empty shell. */
function Citations({
  citations,
  label,
  level: Heading = 'h4',
}: {
  citations: Citation[];
  label: string;
  /** A step's sources sit inside the step, not beside the step list. */
  level?: 'h4' | 'h5';
}) {
  // An empty citation list would be a heading over nothing, which reads as "no
  // source needed" rather than "no source found". Every path that could get
  // here with an empty list has already been diverted to the uncertain
  // variant, so this is a guard, not a display case.
  if (citations.length === 0) return null;

  return (
    <div className="mt-3 border-t border-border pt-3">
      <Heading className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </Heading>
      <ul className="space-y-1">
        {citations.map((citation) => (
          <CitationLine key={citation.document_id} citation={citation} />
        ))}
      </ul>
    </div>
  );
}

/** One numbered action, with the reasoning and sources behind it. */
function Step({
  step,
  messageId,
  index,
}: {
  step: ResolvedStep;
  // Explicitly `| undefined`: with `exactOptionalPropertyTypes`, an optional
  // prop is not the same as one that may be passed undefined, and these are
  // forwarded from a parent that may not have them.
  messageId?: string | undefined;
  index?: number | undefined;
}) {
  const t = useTranslations('diagnosticCard');
  // Severity labels live under `diagnosis`, which already defined all three
  // before this card existed. A second copy under `diagnosticCard` had
  // already drifted from it in one language, inside one file.
  const severityLabel = useTranslations('diagnosis.severity');
  const classes = SEVERITY_CLASSES[step.severity];

  const checklist = useChecklist();
  const trackable = checklist !== null && messageId !== undefined && index !== undefined;
  const checked = trackable && checklist.isChecked(messageId, index);
  const instructionId = useId();

  return (
    <li className={`rounded-md border-s-4 ${classes.border} bg-surface-raised p-3`}>
      <div className="flex items-baseline gap-2">
        {trackable ? (
          <input
            type="checkbox"
            checked={checked}
            onChange={() => {
              checklist.toggle(messageId, index);
            }}
            data-testid="step-checkbox"
            // Labelled by the instruction itself as well as by the action, so
            // tabbing the list announces what each step *is*. An `aria-label`
            // alone overrides the content, and produced "Mark step 1 done,
            // Mark step 2 done…" with no indication of what any of them were.
            aria-labelledby={`${instructionId} ${instructionId}-action`}
            // A to-do, not a verdict. The label says "done", never "verified"
            // or "correct" — an engineer ticking off work they have carried
            // out is recording what they did, not endorsing the advice, and
            // the two must not be confusable if a verification indicator is
            // added elsewhere. A plain square checkbox stays deliberately
            // unlike any badge or tick used for provenance.
            className="mt-1 h-4 w-4 shrink-0 accent-accent"
          />
        ) : (
          <TechnicalToken className="text-sm font-semibold">{step.order}</TechnicalToken>
        )}
        <p
          id={instructionId}
          className={checked ? 'font-medium text-text-muted line-through' : 'font-medium text-text'}
        >
          {trackable ? (
            <TechnicalToken className="me-2 text-sm font-semibold">{step.order}</TechnicalToken>
          ) : null}
          {step.instruction}
        </p>
        {trackable ? (
          <span id={`${instructionId}-action`} className="sr-only">
            {t('markDone', { step: step.order })}
          </span>
        ) : null}
      </div>
      {/* The severity in words as well as in the border colour. Colour alone
          fails WCAG 1.4.1, and the audience reads these on a sunlit screen in
          a plant room as often as at a desk. */}
      <span className="sr-only">{severityLabel(step.severity)}</span>
      <p className="mt-1 text-sm text-text-muted">{step.rationale}</p>
      <Citations citations={step.citations} label={t('stepSources')} level="h5" />
    </li>
  );
}

/**
 * The uncertain variant.
 *
 * Styled with info severity throughout, so it can never be mistaken at a
 * glance for a confident critical or warning card — which is the acceptance
 * criterion's "visually distinct at a glance, not just a text difference".
 * Where citations resolved, they are still shown: pointing at the manual is
 * the most useful thing this card can do when it cannot answer.
 */
function UncertainCard({ variant }: { variant: Extract<CardVariant, { kind: 'uncertain' }> }) {
  const t = useTranslations('diagnosticCard');
  // Per instance: a transcript renders many cards, and `aria-labelledby`
  // resolves to the first matching id in the document. With one hardcoded id
  // every card is announced as the first card's heading — so an uncertain card
  // would be announced as a confident diagnosis, inverting the whole point of
  // the variant.
  const headingId = useId();
  const classes = SEVERITY_CLASSES.info;

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="uncertain"
      data-reason={variant.reason}
      aria-labelledby={headingId}
      className={`rounded-lg border ${classes.border} ${classes.surface} p-4`}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${classes.badge}`}>
          {t('uncertainBadge')}
        </span>
      </div>
      <h3 id={headingId} className="text-lg font-semibold text-text">
        {t('uncertainHeading')}
      </h3>
      <p className="mt-1 text-text-muted">
        {variant.citations.length > 0 ? t('uncertainWithSources') : t('uncertainNoSources')}
      </p>
      <Citations citations={variant.citations} label={t('checkInstead')} />
    </section>
  );
}

/** The refusal variant: the backend declined, and this states that plainly. */
function RefusalCard({ message }: { message: string }) {
  const t = useTranslations('diagnosticCard');
  const headingId = useId();
  const classes = SEVERITY_CLASSES.info;

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="refusal"
      aria-labelledby={headingId}
      className={`rounded-lg border ${classes.border} ${classes.surface} p-4`}
    >
      <h3 id={headingId} className="text-lg font-semibold text-text">
        {t('refusalHeading')}
      </h3>
      {/* The backend's own wording, not a paraphrase. It is written to explain
          the specific reason, and restating it here would drift from it. */}
      <p className="mt-1 text-text-muted">{message || t('refusalFallback')}</p>
    </section>
  );
}

/** The confident variant: summary, severity, ordered steps, and sources. */
function DiagnosisCard({
  variant,
  messageId,
}: {
  variant: Extract<CardVariant, { kind: 'diagnosis' }>;
  messageId?: string | undefined;
}) {
  const t = useTranslations('diagnosticCard');
  const headingId = useId();
  const severityLabel = useTranslations('diagnosis.severity');
  const { diagnosis, steps, summaryCitations } = variant;
  const classes = SEVERITY_CLASSES[diagnosis.severity];

  const checklist = useChecklist();
  const trackable = checklist !== null && messageId !== undefined;
  const done = trackable ? checklist.countChecked(messageId) : 0;

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="diagnosis"
      data-severity={diagnosis.severity}
      aria-labelledby={headingId}
      className={`rounded-lg border ${classes.border} bg-surface p-4`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${classes.badge}`}>
          {severityLabel(diagnosis.severity)}
        </span>
        {diagnosis.equipment_model ? (
          // Echoed back so an engineer can see which unit was understood —
          // and as a technical token, because a model number reordered inside
          // Arabic prose is the wrong model number.
          <TechnicalToken className="text-sm text-text-muted">
            {diagnosis.equipment_model}
          </TechnicalToken>
        ) : null}
      </div>

      <h3 id={headingId} className="sr-only">
        {t('heading')}
      </h3>
      <p className="text-text">{diagnosis.summary}</p>
      <Citations citations={summaryCitations} label={t('summarySources')} />

      <div className="mb-2 mt-4 flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold text-text">{t('steps')}</h4>
        {/* The reason the checklist exists: an engineer glancing back at a
            phone mid-repair wants to know where they were without re-reading
            the whole card. */}
        {trackable ? (
          <p
            // `role="status"` so ticking a step announces the rollup. The
            // transcript's `role="log"` around this announces *appended*
            // content, not text mutated in place, so without it a screen
            // reader user hears the checkbox flip and never the "2 of 3" —
            // which is the one thing this feature exists to say.
            role="status"
            className="text-xs text-text-muted"
            data-testid="checklist-progress"
          >
            {t('progress', { done, total: steps.length })}
          </p>
        ) : null}
      </div>
      <ol className="space-y-2">
        {steps.map((step, index) => (
          <Step key={step.order} step={step} messageId={messageId} index={index} />
        ))}
      </ol>
    </section>
  );
}

/**
 * Render one diagnostic response.
 *
 * Props are typed against the generated backend schema rather than a local
 * restatement of it, so a backend change breaks this build instead of
 * silently rendering the wrong thing — the `shared types drift` CI job keeps
 * the generated file honest.
 */
export function DiagnosticCard({
  response,
  messageId,
}: {
  response: DiagnosticResponse;
  /**
   * Which turn this card is. Checklist state is keyed by it, so a new
   * question starts unticked without disturbing the cards already on screen.
   * Absent when the card is rendered outside a transcript, in which case the
   * steps are plain text.
   */
  messageId?: string;
}) {
  const variant = decideVariant(response);

  switch (variant.kind) {
    case 'refusal':
      return <RefusalCard message={variant.message} />;
    case 'uncertain':
      return <UncertainCard variant={variant} />;
    case 'diagnosis':
      return <DiagnosisCard variant={variant} messageId={messageId} />;
  }
}
