import type { AiDecision, AiProposal } from '@/lib/design';

/** Synthetic proposal data for the M6b tests: no customer text. */
export const DESCRIPTION =
  'Main board MDB. Pump P1 7.5 kW three phase, start/stop from the PLC. Lighting circuit on MDB. Fire pump FP1. What cable length?';

function span(text: string): { start: number; end: number } {
  const start = DESCRIPTION.indexOf(text);
  if (start < 0) throw new Error(`no ${text}`);
  return { start, end: start + text.length };
}

export function proposal(overrides: Partial<AiProposal> = {}): AiProposal {
  return {
    id: 'prop-1',
    input_text: DESCRIPTION,
    provider: 'stub',
    model: 'stub-model-1',
    prompt_version: 'm6b.1',
    created_at: '2026-11-04T09:00:00Z',
    items: [
      {
        id: 'i-board',
        kind: 'board',
        source: span('Main board MDB'),
        quote: 'Main board MDB',
        value: { name: 'MDB' },
      },
      {
        id: 'i-pump',
        kind: 'load',
        source: span('Pump P1 7.5 kW three phase'),
        quote: 'Pump P1 7.5 kW three phase',
        value: { board: 'MDB', description: 'Pump P1', kind: 'motor', power_kw: 7.5, phases: 3 },
      },
      {
        id: 'i-light',
        kind: 'load',
        source: span('Lighting circuit on MDB'),
        quote: 'Lighting circuit on MDB',
        // No phases stated: it cannot be placed until an edit gives them.
        value: { board: 'MDB', description: 'Lighting', kind: 'lighting' },
      },
      {
        id: 'i-start',
        kind: 'control_need',
        source: span('start/stop from the PLC'),
        quote: 'start/stop from the PLC',
        value: { load: 'i-pump', need: 'start_stop' },
      },
      {
        id: 'i-q',
        kind: 'question',
        source: span('What cable length?'),
        quote: 'What cable length?',
        value: { text: 'Cable length for P1 is not given.', about: 'i-pump', reason: 'missing' },
      },
      {
        id: 'i-fire',
        kind: 'unsupported',
        source: span('Fire pump FP1'),
        quote: 'Fire pump FP1',
        value: { text: 'Fire pump', reason: 'fire-pump equipment is out of scope' },
      },
    ],
    server_rejected: [
      {
        kind: 'load',
        quote: 'Pump P1',
        value: { power_kw: 11 },
        reason: 'number_not_in_span',
      },
    ],
    decisions: [],
    used_by_revision_id: null,
    proposal_sha256: 'abc',
    ...overrides,
  };
}

export function decided(
  base: AiProposal,
  item: string,
  decision: AiDecision['decision'],
  value?: Record<string, unknown>,
): AiProposal {
  return {
    ...base,
    decisions: [
      ...base.decisions,
      {
        item_id: item,
        decision,
        ...(value ? { value } : {}),
        by_user_id: 'u-1',
        by_name: 'Dana Reviewer',
        at: '2026-11-04T09:05:00Z',
      },
    ],
  };
}
