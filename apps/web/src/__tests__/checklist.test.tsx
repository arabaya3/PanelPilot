import { act, fireEvent, screen, within } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ChecklistProvider, useChecklist } from '@/components/chat/checklist-provider';
import { DiagnosticCard } from '@/components/diagnostic-card';
import { LOCALES } from '@/i18n/config';

import { renderApp } from './helpers';

/**
 * Tests for the interactive solution checklist.
 *
 * Two things are being defended. The first is the acceptance criterion: ticks
 * survive a re-render and a card scrolling out of view, because the transcript
 * is windowed and an unmounted card would lose component-local state silently
 * — the engineer scrolls back and their progress is simply gone.
 *
 * The second is the spec's warning that a tick must not read as "this step is
 * verified correct". It records what the engineer *did*, not a judgement on
 * the advice. That distinction is invisible in a screenshot and easy to erode
 * later, so it is asserted rather than left to a comment.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

const RESPONSE: DiagnosticResponse = {
  session_id: 'session-1',
  answer: {
    text: 'Undervoltage.',
    citations: [
      {
        document_id: 'doc-1',
        document_title: 'ACS880 Firmware Manual',
        manufacturer: 'ABB',
        page: 214,
        section: '6.3',
      },
    ],
  },
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
        citation_ids: ['doc-1'],
        severity: 'warning',
      },
      {
        order: 3,
        instruction: 'Inspect the DC bus capacitors for bulging.',
        rationale: 'End-of-life capacitors sag under load.',
        citation_ids: ['doc-1'],
        severity: 'info',
      },
    ],
  },
  confidence: {
    overall: 0.9,
    retrieval_score: 0.9,
    passage_agreement: 0.9,
    citation_density: 0.9,
  },
  low_confidence: false,
  refusal_message: null,
};

/** The step checkboxes, typed so `.checked` is readable. */
function boxes(container?: HTMLElement): HTMLInputElement[] {
  const scope = container ? within(container) : screen;
  // Narrowed per element rather than cast wholesale: the assertion also
  // checks these really are inputs, so a control that stopped being a real
  // checkbox fails here rather than reading `undefined` from `.checked` and
  // quietly comparing false to false.
  return scope.getAllByTestId('step-checkbox').map((element) => {
    if (!(element instanceof HTMLInputElement)) {
      throw new Error('step checkbox is not an input');
    }
    return element;
  });
}

function renderCards(ids: string[], locale?: (typeof LOCALES)[number]) {
  return renderApp(
    <ChecklistProvider>
      {ids.map((id) => (
        <div key={id} data-testid={`card-${id}`}>
          <DiagnosticCard response={RESPONSE} messageId={id} />
        </div>
      ))}
    </ChecklistProvider>,
    locale ? { locale } : {},
  );
}

describe('the solution checklist', () => {
  it('renders one checkbox per step', () => {
    renderCards(['m1']);
    expect(screen.getAllByTestId('step-checkbox')).toHaveLength(3);
  });

  it('records a tick', () => {
    renderCards(['m1']);
    expect(boxes()[0]?.checked).toBe(false);

    fireEvent.click(boxes()[0] as HTMLInputElement);
    expect(boxes()[0]?.checked).toBe(true);
    // And only that step.
    expect(boxes()[1]?.checked).toBe(false);
  });

  it('un-ticks on a second click', () => {
    renderCards(['m1']);
    fireEvent.click(boxes()[0] as HTMLInputElement);
    fireEvent.click(boxes()[0] as HTMLInputElement);
    expect(boxes()[0]?.checked).toBe(false);
  });

  it('survives the card unmounting and coming back', () => {
    // The acceptance criterion, and the case the windowed transcript makes
    // real: a card scrolled out of view is unmounted, so state held inside it
    // would be discarded and the ticks would be gone on the way back.
    const { rerender } = renderApp(
      <ChecklistProvider>
        <DiagnosticCard response={RESPONSE} messageId="m1" />
      </ChecklistProvider>,
    );
    fireEvent.click(boxes()[1] as HTMLInputElement);
    expect(boxes()[1]?.checked).toBe(true);

    // Card gone…
    rerender(
      <ChecklistProvider>
        <span />
      </ChecklistProvider>,
    );
    expect(screen.queryByTestId('step-checkbox')).toBeNull();

    // …and back.
    rerender(
      <ChecklistProvider>
        <DiagnosticCard response={RESPONSE} messageId="m1" />
      </ChecklistProvider>,
    );
    expect(boxes()[1]?.checked).toBe(true);
  });

  it('keeps two cards in one session independent', () => {
    // The spec's reason for keying on the message rather than the session.
    renderCards(['m1', 'm2']);
    fireEvent.click(boxes(screen.getByTestId('card-m1'))[0] as HTMLInputElement);

    expect(boxes(screen.getByTestId('card-m1'))[0]?.checked).toBe(true);
    expect(boxes(screen.getByTestId('card-m2'))[0]?.checked).toBe(false);
  });

  it('starts a new question unticked without disturbing the old card', () => {
    // "Resets cleanly on a new question" — a new turn has a new message id and
    // so begins unticked, which must not mean wiping the card the engineer is
    // still working from.
    const { rerender } = renderApp(
      <ChecklistProvider>
        <div data-testid="card-m1">
          <DiagnosticCard response={RESPONSE} messageId="m1" />
        </div>
      </ChecklistProvider>,
    );
    fireEvent.click(boxes(screen.getByTestId('card-m1'))[0] as HTMLInputElement);

    rerender(
      <ChecklistProvider>
        <div data-testid="card-m1">
          <DiagnosticCard response={RESPONSE} messageId="m1" />
        </div>
        <div data-testid="card-m2">
          <DiagnosticCard response={RESPONSE} messageId="m2" />
        </div>
      </ChecklistProvider>,
    );

    expect(boxes(screen.getByTestId('card-m1'))[0]?.checked).toBe(true);
    expect(boxes(screen.getByTestId('card-m2'))[0]?.checked).toBe(false);
  });

  it('reports progress across the card', () => {
    renderCards(['m1']);
    fireEvent.click(boxes()[0] as HTMLInputElement);
    fireEvent.click(boxes()[2] as HTMLInputElement);

    expect(screen.getByTestId('checklist-progress').textContent).toContain('2');
    expect(screen.getByTestId('checklist-progress').textContent).toContain('3');
  });

  it('renders as plain steps with no provider around it', () => {
    // The card is rendered outside a transcript elsewhere — a gallery, a
    // future printed view — and must not require a checklist to be readable.
    renderApp(<DiagnosticCard response={RESPONSE} messageId="m1" />);
    expect(screen.queryByTestId('step-checkbox')).toBeNull();
    expect(screen.getByText(/Measure the supply voltage/)).toBeTruthy();
  });

  it('renders as plain steps when the card has no message id', () => {
    renderApp(
      <ChecklistProvider>
        <DiagnosticCard response={RESPONSE} />
      </ChecklistProvider>,
    );
    expect(screen.queryByTestId('step-checkbox')).toBeNull();
  });
});

// --- a to-do, not a verdict --------------------------------------------------

describe('the checklist is not a verification indicator', () => {
  it('labels a tick as work done, never as advice verified', () => {
    // The spec is explicit that checking a step must not read as "this step is
    // verified correct". The engineer is recording what they carried out; they
    // are not endorsing the diagnosis, and conflating the two would turn a
    // personal to-do list into a false provenance signal.
    // Including the Arabic and Hebrew roots. The Latin-only pattern could
    // never match either script, so those two assertions passed
    // unconditionally and would have kept passing against a bundle that said
    // "verified" in the language most of the audience reads.
    const forbidden =
      /verif|valid|correct|approved|confirmed|accurate|تحقق|تأكيد|مؤكد|صحيح|אימות|מאומת|אושר|נכון/i;

    for (const locale of LOCALES) {
      const bundle = JSON.parse(
        readFileSync(resolve(process.cwd(), `src/messages/${locale}.json`), 'utf8'),
      ) as { diagnosticCard: Record<string, string> };

      expect(bundle.diagnosticCard.markDone, `${locale} markDone`).toBeTruthy();
      expect(bundle.diagnosticCard.markDone, `${locale} markDone reads as a verdict`).not.toMatch(
        forbidden,
      );
      expect(bundle.diagnosticCard.progress, `${locale} progress reads as a verdict`).not.toMatch(
        forbidden,
      );
    }
  });

  it('uses a plain checkbox rather than the citation visual language', () => {
    // Provenance in this card is carried by the Sources block. A tick that
    // borrowed that vocabulary would blur "I did this" into "this is
    // sourced", so the control stays an ordinary checkbox.
    renderCards(['m1']);
    const box = boxes()[0];
    expect(box?.tagName.toLowerCase()).toBe('input');
    expect(box?.getAttribute('type')).toBe('checkbox');
    // And it is a real checkbox, so it is keyboard-operable and announced as
    // one for free.
    expect(box?.getAttribute('role')).toBeNull();
  });

  it('does not alter the citations when a step is ticked', () => {
    renderCards(['m1']);
    const before = screen.getAllByText(/ACS880 Firmware Manual/).length;
    fireEvent.click(boxes()[0] as HTMLInputElement);
    expect(screen.getAllByText(/ACS880 Firmware Manual/).length).toBe(before);
  });
});

// --- retrying a turn ---------------------------------------------------------

describe('retrying a turn', () => {
  /** The same response shape with a given number of steps. */
  function withSteps(count: number): DiagnosticResponse {
    return {
      ...RESPONSE,
      diagnosis: {
        summary: 'The drive tripped on DC bus undervoltage.',
        summary_citation_ids: ['doc-1'],
        severity: 'critical',
        equipment_model: 'ACS880',
        steps: Array.from({ length: count }, (_, i) => ({
          order: i + 1,
          instruction: `Step ${String(i + 1)} of ${String(count)}`,
          rationale: 'r',
          citation_ids: ['doc-1'],
          severity: 'info' as const,
        })),
      },
    };
  }

  it('does not carry ticks onto a different set of steps', () => {
    // A retry re-asks under the *same* message id and can return different
    // advice. Ticks are positional, so without an explicit clear they rebind:
    // step 2 of an answer the engineer has never read renders pre-ticked and
    // struck through, which reads as "I already did this" and invites
    // skipping it. In an electrical repair that is a safety consequence.
    const checklist = { current: null as ReturnType<typeof useChecklist> };
    function Probe() {
      checklist.current = useChecklist();
      return null;
    }

    const { rerender } = renderApp(
      <ChecklistProvider>
        <Probe />
        <DiagnosticCard response={withSteps(3)} messageId="a1" />
      </ChecklistProvider>,
    );
    fireEvent.click(boxes()[1] as HTMLInputElement);
    expect(boxes()[1]?.checked).toBe(true);

    // What `retry` does before re-running the turn.
    act(() => {
      checklist.current?.clear('a1');
    });

    rerender(
      <ChecklistProvider>
        <Probe />
        <DiagnosticCard response={withSteps(2)} messageId="a1" />
      </ChecklistProvider>,
    );

    expect(boxes().map((b) => b.checked)).toEqual([false, false]);
    expect(screen.getByTestId('checklist-progress').textContent).toContain('0');
  });

  it('clears only the turn being retried', () => {
    const checklist = { current: null as ReturnType<typeof useChecklist> };
    function Probe() {
      checklist.current = useChecklist();
      return null;
    }

    renderApp(
      <ChecklistProvider>
        <Probe />
        <div data-testid="card-a1">
          <DiagnosticCard response={RESPONSE} messageId="a1" />
        </div>
        <div data-testid="card-a2">
          <DiagnosticCard response={RESPONSE} messageId="a2" />
        </div>
      </ChecklistProvider>,
    );

    fireEvent.click(boxes(screen.getByTestId('card-a1'))[0] as HTMLInputElement);
    fireEvent.click(boxes(screen.getByTestId('card-a2'))[0] as HTMLInputElement);

    act(() => {
      checklist.current?.clear('a1');
    });

    expect(boxes(screen.getByTestId('card-a1'))[0]?.checked).toBe(false);
    expect(boxes(screen.getByTestId('card-a2'))[0]?.checked).toBe(true);
  });

  it('keeps ids with a colon in them distinct', () => {
    // Ids are minted locally today and contain no colon, so a flat
    // `id:index` key happened to be unambiguous. Nothing enforced that, and a
    // server-supplied id — a resumed session, a shared transcript — would
    // have collided silently.
    renderApp(
      <ChecklistProvider>
        <div data-testid="card-x">
          <DiagnosticCard response={RESPONSE} messageId="s:1" />
        </div>
        <div data-testid="card-y">
          <DiagnosticCard response={RESPONSE} messageId="s" />
        </div>
      </ChecklistProvider>,
    );

    // Under the old scheme both of these resolved to the key "s:1".
    fireEvent.click(boxes(screen.getByTestId('card-y'))[1] as HTMLInputElement);

    expect(boxes(screen.getByTestId('card-y'))[1]?.checked).toBe(true);
    expect(boxes(screen.getByTestId('card-x'))[0]?.checked).toBe(false);
    expect(boxes(screen.getByTestId('card-x'))[1]?.checked).toBe(false);
  });
});

// --- what a screen reader is told --------------------------------------------

describe('announcements', () => {
  it('names the step each checkbox belongs to', () => {
    // A mutation that hardcoded the step number left every checkbox announcing
    // "Mark step 1 done" and passed the whole suite, because every test
    // reached the boxes positionally and none ever read a label.
    renderCards(['m1']);

    const second = screen.getByRole('checkbox', {
      name: /Check parameter 21\.03 against the nameplate/,
    });
    expect(second).toBeTruthy();
    // And the action, so the control says what it will do as well as what it
    // refers to.
    expect(screen.getByRole('checkbox', { name: /Mark step 2 done/ })).toBe(second);
  });

  it('announces the progress rollup when a step is ticked', () => {
    // The transcript around this is a `log`, which announces appended content
    // rather than text mutated in place — so without its own live role the
    // rollup is the one thing a screen reader user never hears.
    renderCards(['m1']);
    expect(screen.getByTestId('checklist-progress').getAttribute('role')).toBe('status');
  });
});
