import { fireEvent, screen, waitFor } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it, vi } from 'vitest';

import { PlcReview } from '@/components/plc-review';
import { generatePlc } from '@/lib/plc';

import { renderApp } from './helpers';

/**
 * Generating PLC code from a description.
 *
 * `POST /plc/generate` answered 422 "not yet wired" and had no page.
 */

type PlcGenerationResult = components['schemas']['PlcGenerationResult'];

const SOURCE = `PROGRAM Conveyor
  VAR
    Start : BOOL;
    Stop : BOOL;
    Motor : BOOL;
  END_VAR
  Motor := (Start OR Motor) AND Stop;
END_PROGRAM`;

const GENERATED: PlcGenerationResult = {
  language: 'structured-text',
  dialect: 'siemens-scl',
  source: SOURCE,
  rungs: [],
  validation: { status: 'valid', dialect: 'siemens-scl', findings: [], checked_by: 'lark' },
};

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function renderGenerate(
  generateImpl: typeof generatePlc,
  acquireImpl = vi.fn().mockResolvedValue(READY),
) {
  renderApp(<PlcReview generateImpl={generateImpl} acquireImpl={acquireImpl} />);
  fireEvent.click(screen.getByTestId('plc-tab-generate'));
  return acquireImpl;
}

function describe_(text: string) {
  fireEvent.change(screen.getByLabelText('What should the program do?'), {
    target: { value: text },
  });
}

describe('generating PLC code', () => {
  it('sends the description, target and output, and shows the code with its check', async () => {
    const generateImpl = vi.fn().mockResolvedValue({ kind: 'generated', result: GENERATED });
    renderGenerate(generateImpl);

    describe_('  Start/stop a conveyor with a seal-in.  ');
    fireEvent.change(screen.getByLabelText('Target'), { target: { value: 'siemens-scl' } });
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));

    expect(await screen.findByTestId('plc-generated-note')).toBeTruthy();
    expect(generateImpl).toHaveBeenCalledWith({
      token: 'tok',
      description: 'Start/stop a conveyor with a seal-in.',
      dialect: 'siemens-scl',
      language: 'structured-text',
    });
    expect(document.body.textContent).toContain('PROGRAM Conveyor');
  });

  it('starts a session only when a program is asked for, and reuses it', async () => {
    const generateImpl = vi.fn().mockResolvedValue({ kind: 'generated', result: GENERATED });
    const acquireImpl = renderGenerate(generateImpl);
    expect(acquireImpl).not.toHaveBeenCalled();

    describe_('Conveyor');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await screen.findByTestId('plc-generated-note');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    await waitFor(() => {
      expect(generateImpl).toHaveBeenCalledTimes(2);
    });
    expect(acquireImpl).toHaveBeenCalledTimes(1);
  });

  it('will not send an empty description', () => {
    const generateImpl = vi.fn();
    renderGenerate(generateImpl);
    describe_('   ');
    const button = screen.getByRole('button', { name: 'Generate' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows the server’s reason when the model could not write it', async () => {
    renderGenerate(
      vi.fn().mockResolvedValue({ kind: 'rejected', detail: 'the model returned no program' }),
    );
    describe_('Conveyor');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    expect((await screen.findByTestId('plc-generate-rejected')).textContent).toBe(
      'the model returned no program',
    );
  });

  it.each([
    ['unavailable', /unavailable/],
    ['rate-limited', /Too many/],
    ['unauthorized', /expired/],
  ] as const)('says so when %s', async (kind, text) => {
    renderGenerate(vi.fn().mockResolvedValue({ kind }));
    describe_('Conveyor');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    expect((await screen.findByTestId('plc-generate-error')).textContent).toMatch(text);
  });

  it('says so when no session could start', async () => {
    const generateImpl = vi.fn();
    renderGenerate(generateImpl, vi.fn().mockResolvedValue({ kind: 'rate-limited' }));
    describe_('Conveyor');
    fireEvent.click(screen.getByRole('button', { name: 'Generate' }));
    expect((await screen.findByTestId('plc-generate-error')).textContent).toMatch(/session/);
    expect(generateImpl).not.toHaveBeenCalled();
  });
});

describe('generatePlc', () => {
  function respond(status: number, body: unknown = {}): typeof fetch {
    return vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    });
  }

  it('posts with the token and returns the result', async () => {
    const fetchImpl = respond(200, GENERATED);
    expect(
      await generatePlc({
        token: 't',
        description: 'd',
        dialect: 'iec-61131-3',
        language: 'ladder',
        fetchImpl,
      }),
    ).toEqual({ kind: 'generated', result: GENERATED });
    expect(fetchImpl).toHaveBeenCalledWith('/api/v1/plc/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer t' },
      body: JSON.stringify({ description: 'd', dialect: 'iec-61131-3', language: 'ladder' }),
    });
  });

  it.each([
    [401, { kind: 'unauthorized' }],
    [429, { kind: 'rate-limited' }],
    [503, { kind: 'unavailable' }],
    [500, { kind: 'failed' }],
  ])('maps %i', async (status, outcome) => {
    expect(
      await generatePlc({
        token: 't',
        description: 'd',
        dialect: 'iec-61131-3',
        language: 'structured-text',
        fetchImpl: respond(status),
      }),
    ).toEqual(outcome);
  });

  it('carries a 422’s detail', async () => {
    expect(
      await generatePlc({
        token: 't',
        description: 'd',
        dialect: 'iec-61131-3',
        language: 'structured-text',
        fetchImpl: respond(422, { detail: 'cut off' }),
      }),
    ).toEqual({ kind: 'rejected', detail: 'cut off' });
  });
});
