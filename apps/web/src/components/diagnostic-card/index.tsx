'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';

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
      <span>{citation.document_title}</span>
      {locator.length > 0 ? <span> — {locator.join(', ')}</span> : null}
    </li>
  );
}

/** The citation list, which never renders as an empty shell. */
function Citations({ citations, label }: { citations: Citation[]; label: string }) {
  // An empty citation list would be a heading over nothing, which reads as "no
  // source needed" rather than "no source found". Every path that could get
  // here with an empty list has already been diverted to the uncertain
  // variant, so this is a guard, not a display case.
  if (citations.length === 0) return null;

  return (
    <div className="mt-3 border-t border-border pt-3">
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-text-muted">
        {label}
      </h4>
      <ul className="space-y-1">
        {citations.map((citation) => (
          <CitationLine key={citation.document_id} citation={citation} />
        ))}
      </ul>
    </div>
  );
}

/** One numbered action, with the reasoning and sources behind it. */
function Step({ step }: { step: ResolvedStep }) {
  const t = useTranslations('diagnosticCard');
  const classes = SEVERITY_CLASSES[step.severity];

  return (
    <li className={`rounded-md border-s-4 ${classes.border} bg-surface-raised p-3`}>
      <div className="flex items-baseline gap-2">
        <TechnicalToken className="text-sm font-semibold">{step.order}</TechnicalToken>
        <p className="font-medium text-text">{step.instruction}</p>
      </div>
      <p className="mt-1 text-sm text-text-muted">{step.rationale}</p>
      <Citations citations={step.citations} label={t('stepSources')} />
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
  const classes = SEVERITY_CLASSES.info;

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="uncertain"
      data-reason={variant.reason}
      aria-labelledby="diagnostic-card-heading"
      className={`rounded-lg border ${classes.border} ${classes.surface} p-4`}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${classes.badge}`}>
          {t('uncertainBadge')}
        </span>
      </div>
      <h3 id="diagnostic-card-heading" className="text-lg font-semibold text-text">
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
  const classes = SEVERITY_CLASSES.info;

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="refusal"
      aria-labelledby="diagnostic-card-heading"
      className={`rounded-lg border ${classes.border} ${classes.surface} p-4`}
    >
      <h3 id="diagnostic-card-heading" className="text-lg font-semibold text-text">
        {t('refusalHeading')}
      </h3>
      {/* The backend's own wording, not a paraphrase. It is written to explain
          the specific reason, and restating it here would drift from it. */}
      <p className="mt-1 text-text-muted">{message || t('refusalFallback')}</p>
    </section>
  );
}

/** The confident variant: summary, severity, ordered steps, and sources. */
function DiagnosisCard({ variant }: { variant: Extract<CardVariant, { kind: 'diagnosis' }> }) {
  const t = useTranslations('diagnosticCard');
  const { diagnosis, steps, summaryCitations } = variant;
  const classes = SEVERITY_CLASSES[diagnosis.severity];

  return (
    <section
      data-testid="diagnostic-card"
      data-variant="diagnosis"
      data-severity={diagnosis.severity}
      aria-labelledby="diagnostic-card-heading"
      className={`rounded-lg border ${classes.border} bg-surface p-4`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded px-2 py-0.5 text-xs font-semibold uppercase ${classes.badge}`}>
          {t(`severity.${diagnosis.severity}`)}
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

      <h3 id="diagnostic-card-heading" className="sr-only">
        {t('heading')}
      </h3>
      <p className="text-text">{diagnosis.summary}</p>
      <Citations citations={summaryCitations} label={t('summarySources')} />

      <h4 className="mb-2 mt-4 text-sm font-semibold text-text">{t('steps')}</h4>
      <ol className="space-y-2">
        {steps.map((step) => (
          <Step key={step.order} step={step} />
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
export function DiagnosticCard({ response }: { response: DiagnosticResponse }) {
  const variant = decideVariant(response);

  switch (variant.kind) {
    case 'refusal':
      return <RefusalCard message={variant.message} />;
    case 'uncertain':
      return <UncertainCard variant={variant} />;
    case 'diagnosis':
      return <DiagnosisCard variant={variant} />;
  }
}
