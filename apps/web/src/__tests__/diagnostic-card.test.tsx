import { screen, within } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { DiagnosticCard } from '@/components/diagnostic-card';
import { decideVariant } from '@/components/diagnostic-card/citations';
import { LOCALES } from '@/i18n/config';

import { renderApp } from './helpers';

/**
 * Tests for the diagnostic response card.
 *
 * The card is the product's primary trust signal, so most of what is asserted
 * here is a negative: what must **never** appear on screen. An uncited claim
 * rendered as a confident instruction is the exact failure the cite-or-refuse
 * guardrail exists to prevent, and it is invisible by nature — a step with a
 * broken citation looks identical to a step with a good one.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];
type Citation = components['schemas']['Citation'];
type Severity = components['schemas']['Severity'];

const MANUAL: Citation = {
  document_id: 'doc-1',
  document_title: 'ACS880 Firmware Manual',
  manufacturer: 'ABB',
  page: 214,
  section: '6.3',
};

const SECOND: Citation = {
  document_id: 'doc-2',
  document_title: 'ACS880 Hardware Manual',
  manufacturer: 'ABB',
  page: null,
  section: null,
};

const CONFIDENCE: components['schemas']['ConfidenceBreakdown'] = {
  overall: 0.91,
  retrieval_score: 0.9,
  passage_agreement: 0.92,
  citation_density: 0.9,
};

/**
 * A well-formed response, with overrides.
 *
 * Built from a complete valid payload rather than assembled per test, so a
 * test that changes one field is visibly testing that field.
 */
function response(overrides: Partial<DiagnosticResponse> = {}): DiagnosticResponse {
  return {
    session_id: 'session-1',
    answer: { text: 'Undervoltage trip.', citations: [MANUAL, SECOND] },
    diagnosis: {
      summary: 'The drive tripped on DC bus undervoltage.',
      summary_citation_ids: ['doc-1'],
      severity: 'critical',
      equipment_model: 'ACS880',
      steps: [
        {
          order: 1,
          instruction: 'Measure the supply voltage at the input terminals.',
          rationale: 'An undervoltage trip most often follows a supply fault.',
          citation_ids: ['doc-1'],
          severity: 'critical',
        },
        {
          order: 2,
          instruction: 'Check parameter 21.03 against the nameplate.',
          rationale: 'A mis-set ride-through threshold trips on a healthy supply.',
          citation_ids: ['doc-2'],
          severity: 'warning',
        },
      ],
    },
    confidence: CONFIDENCE,
    low_confidence: false,
    refusal_message: null,
    ...overrides,
  };
}

/**
 * A response together with its diagnosis, already narrowed.
 *
 * Tests mutate the diagnosis to build each malformed payload, and reaching it
 * through `response().diagnosis` costs a non-null assertion at every use.
 * This narrows once, and throws if the fixture ever stops being well-formed —
 * which would otherwise make a test silently assert nothing.
 */
function withDiagnosis(overrides: Partial<DiagnosticResponse> = {}): {
  payload: DiagnosticResponse;
  diagnosis: components['schemas']['StructuredDiagnosis'];
} {
  const payload = response(overrides);
  const { diagnosis } = payload;
  if (!diagnosis) throw new Error('fixture has no diagnosis');
  return { payload, diagnosis };
}

/** One step from the fixture, or a loud failure if the fixture changed. */
function step(
  diagnosis: components['schemas']['StructuredDiagnosis'],
  index: number,
): components['schemas']['DiagnosisStep'] {
  const found = diagnosis.steps[index];
  if (!found) throw new Error(`fixture has no step ${String(index)}`);
  return found;
}

// --- the variant decision, tested without rendering -------------------------

describe('decideVariant', () => {
  it('renders a confident diagnosis when every citation resolves', () => {
    const variant = decideVariant(response());
    expect(variant.kind).toBe('diagnosis');
  });

  it('degrades when a step cites a document that is not present', () => {
    // The core case. The step is well-formed, its instruction is plausible,
    // and the id simply refers to nothing — which on screen is indistinguishable
    // from a cited step unless the card refuses to render it.
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.steps[1] = { ...step(diagnosis, 1), citation_ids: ['doc-missing'] };

    const variant = decideVariant(payload);
    expect(variant.kind).toBe('uncertain');
    expect(variant.kind === 'uncertain' && variant.reason).toBe('unresolved-citation');
  });

  it('degrades when the summary cites a document that is not present', () => {
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.summary_citation_ids = ['doc-missing'];

    const variant = decideVariant(payload);
    expect(variant.kind).toBe('uncertain');
    expect(variant.kind === 'uncertain' && variant.reason).toBe('unresolved-citation');
  });

  it('degrades rather than dropping the unresolvable half of a citation list', () => {
    // Filtering would be the tempting implementation and the dangerous one:
    // the step would render as cited, and the missing source would be
    // invisible precisely because it is missing.
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.steps[0] = { ...step(diagnosis, 0), citation_ids: ['doc-1', 'doc-missing'] };

    expect(decideVariant(payload).kind).toBe('uncertain');
  });

  it('degrades when a step carries no citation at all', () => {
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.steps[0] = { ...step(diagnosis, 0), citation_ids: [] };

    const variant = decideVariant(payload);
    expect(variant.kind).toBe('uncertain');
    expect(variant.kind === 'uncertain' && variant.reason).toBe('no-citations');
  });

  it('degrades when the answer carries no citations to resolve against', () => {
    // A diagnosis whose ids cannot be resolved because the citation list is
    // absent entirely — the null-reference case, which must not render an
    // empty citation block.
    const payload = response({ answer: null });

    expect(decideVariant(payload).kind).toBe('uncertain');
  });

  it('degrades when the model reports low confidence', () => {
    const variant = decideVariant(response({ low_confidence: true }));
    expect(variant.kind).toBe('uncertain');
    expect(variant.kind === 'uncertain' && variant.reason).toBe('low-confidence');
  });

  it('reports the citation failure when a payload is both unsure and uncited', () => {
    // Both are true; the citation failure is the more serious and is the one
    // worth seeing in a log.
    const { payload, diagnosis } = withDiagnosis({ low_confidence: true });
    diagnosis.summary_citation_ids = ['doc-missing'];

    const variant = decideVariant(payload);
    expect(variant.kind === 'uncertain' && variant.reason).toBe('unresolved-citation');
  });

  it('degrades when a diagnosis carries no steps', () => {
    // The backend schema says non-empty, but this renders what arrives over
    // the wire rather than what the schema promised.
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.steps = [];

    const variant = decideVariant(payload);
    expect(variant.kind).toBe('uncertain');
    expect(variant.kind === 'uncertain' && variant.reason).toBe('no-steps');
  });

  it('renders the refusal variant when the backend declined', () => {
    const variant = decideVariant(
      response({ diagnosis: null, refusal_message: 'No supporting passage was found.' }),
    );
    expect(variant.kind).toBe('refusal');
  });
});

// --- what actually reaches the screen ---------------------------------------

describe('DiagnosticCard', () => {
  it('renders all five zones of a confident diagnosis', () => {
    renderApp(<DiagnosticCard response={response()} />);
    const card = screen.getByTestId('diagnostic-card');

    expect(card.getAttribute('data-variant')).toBe('diagnosis');
    expect(card.getAttribute('data-severity')).toBe('critical');
    expect(within(card).getByText(/tripped on DC bus undervoltage/)).toBeTruthy();
    expect(within(card).getByText(/Measure the supply voltage/)).toBeTruthy();
    expect(within(card).getByText(/nameplate/)).toBeTruthy();
    expect(within(card).getAllByText(/ACS880 Firmware Manual/).length).toBeGreaterThan(0);
    expect(within(card).getByText('ACS880')).toBeTruthy();
  });

  it.each(['critical', 'warning', 'info'] as Severity[])(
    'maps %s to its own token colour',
    (severity) => {
      const { payload, diagnosis } = withDiagnosis();
      diagnosis.severity = severity;
      renderApp(<DiagnosticCard response={payload} />);

      const card = screen.getByTestId('diagnostic-card');
      expect(card.getAttribute('data-severity')).toBe(severity);
      // The class is a complete literal from the severity map, so it is in the
      // compiled stylesheet. An interpolated `border-severity-${severity}` is
      // not, and would render unstyled while passing a looser assertion.
      expect(card.className).toContain(`border-severity-${severity}`);
    },
  );

  it('never shows an instruction on an uncertain card', () => {
    // The point of the variant. A card that showed its steps under a "not
    // certain" caption would be read as an answer with a disclaimer, and an
    // engineer in a hurry reads the steps and not the caption.
    const payload = response({ low_confidence: true });
    renderApp(<DiagnosticCard response={payload} />);

    expect(screen.queryByText(/Measure the supply voltage/)).toBeNull();
    expect(screen.queryByText(/nameplate/)).toBeNull();
    expect(screen.queryByText(/tripped on DC bus undervoltage/)).toBeNull();
  });

  it('is visually distinct from a confident card, not merely differently worded', () => {
    // The acceptance criterion says "at a glance". An uncertain card carries
    // info styling regardless of the severity the model claimed, so a
    // critical-severity uncertain payload cannot look like a critical answer.
    const uncertain = response({ low_confidence: true });
    renderApp(<DiagnosticCard response={uncertain} />);
    const card = screen.getByTestId('diagnostic-card');

    expect(card.getAttribute('data-variant')).toBe('uncertain');
    expect(card.className).toContain('border-severity-info');
    expect(card.className).not.toContain('border-severity-critical');
    // And it carries no severity badge claiming urgency it cannot support.
    expect(card.getAttribute('data-severity')).toBeNull();
  });

  it('points at the documentation it could not draw from', () => {
    const payload = response({ low_confidence: true });
    renderApp(<DiagnosticCard response={payload} />);
    expect(screen.getByText(/ACS880 Firmware Manual/)).toBeTruthy();
  });

  it('renders no citation block when nothing resolved', () => {
    // Rather than a heading over an empty list, which reads as "no source
    // needed" instead of "no source found".
    const payload = response({ answer: null });
    renderApp(<DiagnosticCard response={payload} />);

    expect(screen.queryByText(/ACS880 Firmware Manual/)).toBeNull();
    expect(screen.queryByText(/Sources/)).toBeNull();
  });

  it("shows the backend's own refusal wording", () => {
    // Not a paraphrase: the backend explains the specific reason, and a
    // restatement here would drift from it.
    renderApp(
      <DiagnosticCard
        response={response({
          diagnosis: null,
          refusal_message: 'No passage in the indexed manuals supports an answer.',
        })}
      />,
    );
    expect(screen.getByText(/No passage in the indexed manuals/)).toBeTruthy();
  });

  it('still says something when a refusal arrives with no message', () => {
    renderApp(<DiagnosticCard response={response({ diagnosis: null, refusal_message: null })} />);
    const card = screen.getByTestId('diagnostic-card');
    expect(card.getAttribute('data-variant')).toBe('refusal');
    expect(card.textContent.trim()).not.toBe('');
  });

  it('omits the model line rather than rendering an empty one', () => {
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.equipment_model = null;
    renderApp(<DiagnosticCard response={payload} />);
    expect(screen.queryByText('ACS880')).toBeNull();
  });

  it('renders a citation with no page or section without a dangling separator', () => {
    const { payload, diagnosis } = withDiagnosis();
    diagnosis.summary_citation_ids = ['doc-2'];
    diagnosis.steps = [step(diagnosis, 0)];
    renderApp(<DiagnosticCard response={payload} />);

    const hardware = screen.getByText('ACS880 Hardware Manual');
    expect(hardware.parentElement?.textContent).not.toContain('—');
  });

  it('keeps the equipment model LTR in every locale', () => {
    // A model number reordered inside Arabic prose is the wrong model number,
    // exactly as a fault code is.
    for (const locale of LOCALES) {
      const { unmount } = renderApp(<DiagnosticCard response={response()} />, { locale });
      const model = screen.getByText('ACS880');
      expect(model.tagName.toLowerCase(), locale).toBe('bdi');
      expect(model.getAttribute('dir'), locale).toBe('ltr');
      unmount();
    }
  });
});

// --- the severity colours are real, not merely named -------------------------

describe('severity colours', () => {
  it('names every severity class as a complete literal', () => {
    // Tailwind scans source text for whole class names, so an interpolated
    // `border-severity-${severity}` never reaches the compiled stylesheet and
    // renders unstyled — while still passing a DOM assertion that only checks
    // the class *attribute*, since the string is identical either way. A
    // mutation confirmed exactly that blind spot, so the guarantee is pinned
    // at its real source instead: the class names must be literal in the file.
    const source = readFileSync(
      resolve(process.cwd(), 'src/components/diagnostic-card/index.tsx'),
      'utf8',
    );

    for (const severity of ['critical', 'warning', 'info']) {
      expect(source, `${severity} border is not a literal class`).toContain(
        `border-severity-${severity}`,
      );
    }
    // And nothing builds one by interpolation, which is the failure mode.
    // Comments are stripped first: the file documents this hazard by naming
    // it, and a check that cannot tell the warning from the mistake would
    // fail on the explanation of why it exists.
    const code = source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(code).not.toMatch(/severity-\$\{/);
  });
});
