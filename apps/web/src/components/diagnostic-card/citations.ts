import type { components } from '@panelpilot/shared-types';

type Citation = components['schemas']['Citation'];
type StructuredDiagnosis = components['schemas']['StructuredDiagnosis'];
type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

/**
 * Resolving citation ids to the citations behind them.
 *
 * The backend sends these apart: a step carries `citation_ids`, and the
 * `Citation` objects those ids refer to live on `answer.citations`. Rendering
 * the card therefore means joining the two, and the join is the whole point of
 * this file rather than an implementation detail — an id that resolves to
 * nothing is an **uncited claim**, which is exactly what the cite-or-refuse
 * guardrail exists to prevent reaching an engineer.
 *
 * The failure this guards against is specific. A step reading "Replace the DC
 * bus capacitors" with a citation the reader can follow is a recommendation.
 * The same sentence with a citation that silently resolved to nothing is an
 * assertion from an authority that does not exist, and it looks identical on
 * screen. So the card never renders a step whose citations do not resolve; it
 * degrades to the uncertain variant instead.
 */

/** A step joined to the citations its ids actually resolve to. */
export interface ResolvedStep {
  order: number;
  instruction: string;
  rationale: string;
  severity: components['schemas']['Severity'];
  citations: Citation[];
}

/** What the card should render, decided once rather than at each zone. */
export type CardVariant =
  | { kind: 'refusal'; message: string }
  | { kind: 'uncertain'; reason: UncertainReason; citations: Citation[] }
  | {
      kind: 'diagnosis';
      diagnosis: StructuredDiagnosis;
      steps: ResolvedStep[];
      summaryCitations: Citation[];
    };

/**
 * Why a payload could not be rendered as a confident diagnosis.
 *
 * Kept distinct rather than collapsed to a boolean because they are not the
 * same event: `low-confidence` is the model telling us it is unsure, while
 * `unresolved-citation` is the model asserting something it attributed to a
 * source that is not there. The second is the more serious of the two and is
 * worth being able to count separately in logs.
 */
export type UncertainReason =
  'low-confidence' | 'no-citations' | 'unresolved-citation' | 'no-steps';

/** Index citations by id for the join. */
function citationsById(citations: Citation[]): Map<string, Citation> {
  return new Map(citations.map((citation) => [citation.document_id, citation]));
}

/**
 * Resolve every id, or report that one could not be resolved.
 *
 * Returns `null` rather than a filtered list on any miss. Filtering would be
 * the dangerous choice: a step citing two sources where one resolves would
 * render as a cited step, and the missing half would be invisible precisely
 * because it is missing.
 */
function resolveAll(ids: string[], index: Map<string, Citation>): Citation[] | null {
  const resolved: Citation[] = [];
  for (const id of ids) {
    const citation = index.get(id);
    if (!citation) return null;
    resolved.push(citation);
  }
  return resolved;
}

/**
 * Decide what the card renders from one API response.
 *
 * All of the branching lives here, so the component is a renderer and the rule
 * "an uncited claim is never displayed as a confident one" is stated in a
 * single place that can be tested without rendering anything.
 */
export function decideVariant(response: DiagnosticResponse): CardVariant {
  // A refusal is the backend having already decided. The frontend does not
  // second-guess it or try to salvage a partial answer out of one.
  if (!response.diagnosis) {
    return {
      kind: 'refusal',
      message: response.refusal_message ?? '',
    };
  }

  const diagnosis = response.diagnosis;
  const index = citationsById(response.answer?.citations ?? []);
  const summaryCitations = resolveAll(diagnosis.summary_citation_ids, index);

  // Order matters below only in which reason gets reported; every branch
  // degrades to the same variant, so a payload failing two checks is still
  // safe. The reason is reported for the most serious failure first.
  if (summaryCitations === null) {
    return { kind: 'uncertain', reason: 'unresolved-citation', citations: [] };
  }
  if (diagnosis.summary_citation_ids.length === 0) {
    return { kind: 'uncertain', reason: 'no-citations', citations: [] };
  }

  const steps: ResolvedStep[] = [];
  for (const step of diagnosis.steps) {
    const citations = resolveAll(step.citation_ids, index);
    if (citations === null) {
      return { kind: 'uncertain', reason: 'unresolved-citation', citations: summaryCitations };
    }
    if (step.citation_ids.length === 0) {
      return { kind: 'uncertain', reason: 'no-citations', citations: summaryCitations };
    }
    steps.push({
      order: step.order,
      instruction: step.instruction,
      rationale: step.rationale,
      severity: step.severity,
      citations,
    });
  }

  // A diagnosis with no action is not a diagnosis. The backend's own schema
  // says steps are non-empty, but the check is here rather than assumed: this
  // renders whatever arrives over the wire, not whatever the schema promised.
  if (steps.length === 0) {
    return { kind: 'uncertain', reason: 'no-steps', citations: summaryCitations };
  }

  // Checked last, so a low-confidence answer that *also* has an unresolved
  // citation is reported as the citation failure — the more serious of the two
  // and the one worth investigating.
  if (response.low_confidence) {
    return { kind: 'uncertain', reason: 'low-confidence', citations: summaryCitations };
  }

  return { kind: 'diagnosis', diagnosis, steps, summaryCitations };
}
