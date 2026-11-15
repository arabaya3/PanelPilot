import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PlcReview } from '@/components/plc-review';
import { reviewPlc, type ReviewOutcome } from '@/lib/plc';

import { renderApp } from './helpers';

/**
 * The PLC check screen, and its client.
 *
 * The review endpoint and the view both existed with nothing between them, so
 * checking code meant calling the API by hand.
 */

const PROGRAM = [
  'PROGRAM Main',
  '  VAR',
  '    Start : BOOL;',
  '  END_VAR',
  '  StartButtton := TRUE;',
  'END_PROGRAM',
].join('\n');

const REVIEWED: ReviewOutcome = {
  kind: 'reviewed',
  result: {
    status: 'invalid',
    dialect: 'iec-61131-3',
    checked_by: 'lark',
    findings: [
      {
        code: 'undeclared-tag',
        message: "'StartButtton' is used but never declared",
        severity: 'error',
        line: 5,
      },
    ],
  } as never,
};

function submit(source: string) {
  fireEvent.change(screen.getByLabelText('Structured Text'), { target: { value: source } });
  fireEvent.click(screen.getByRole('button', { name: 'Check code' }));
}

describe('the PLC check screen', () => {
  it('sends the program and shows each finding on its line', async () => {
    const reviewImpl = vi.fn().mockResolvedValue(REVIEWED);
    renderApp(<PlcReview reviewImpl={reviewImpl} />);

    submit(PROGRAM);

    expect(reviewImpl).toHaveBeenCalledWith({ source: PROGRAM });
    const finding = await screen.findByTestId('finding-line-5');
    expect(finding.textContent).toContain('StartButtton');
  });

  it('keeps the findings against the program as it was checked', async () => {
    // Editing after checking must not move line 5's error onto new text.
    renderApp(<PlcReview reviewImpl={vi.fn().mockResolvedValue(REVIEWED)} />);
    submit(PROGRAM);
    await screen.findByTestId('finding-line-5');

    fireEvent.change(screen.getByLabelText('Structured Text'), {
      target: { value: 'something else entirely' },
    });

    const view = screen.getByTestId('finding-line-5').closest('section');
    expect(view?.textContent).toContain('StartButtton := TRUE;');
  });

  it('does not send an empty program', () => {
    const reviewImpl = vi.fn();
    renderApp(<PlcReview reviewImpl={reviewImpl} />);

    const button = screen.getByRole('button', { name: 'Check code' });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it('says when the check could not run, rather than showing nothing', async () => {
    renderApp(<PlcReview reviewImpl={vi.fn().mockResolvedValue({ kind: 'failed' })} />);

    submit(PROGRAM);

    await waitFor(() => {
      expect(screen.getByTestId('plc-failed')).toBeTruthy();
    });
  });

  it('shows the server’s reason for refusing a program', async () => {
    renderApp(
      <PlcReview
        reviewImpl={vi.fn().mockResolvedValue({ kind: 'rejected', detail: 'too long' })}
      />,
    );

    submit(PROGRAM);

    expect((await screen.findByTestId('plc-rejected')).textContent).toBe('too long');
  });

  it('gives a clean program an explicit verdict that says what was checked', async () => {
    // As the API answers a declared-and-used program: valid, no findings.
    renderApp(
      <PlcReview
        reviewImpl={vi.fn().mockResolvedValue({
          kind: 'reviewed',
          result: {
            status: 'valid',
            findings: [],
            dialect: 'iec-61131-3',
            checked_by: 'lark-iec61131-3-subset',
          },
        })}
      />,
    );

    submit(PROGRAM);

    const verdict = await screen.findByTestId('verdict-banner');
    expect(verdict.getAttribute('role')).toBe('status');
    expect(verdict.textContent).toContain('Passed — the checker found no problems');
    expect(verdict.textContent).toContain('does not test what the program does when it runs');
  });

  it('says a program over the limit is too long, in characters, without sending it', async () => {
    const reviewImpl = vi.fn();
    renderApp(<PlcReview reviewImpl={reviewImpl} />);

    submit(`PROGRAM Main\n${'x := 1;\n'.repeat(12_500)}END_PROGRAM`);

    expect(screen.queryByTestId('plc-rejected')).toBeNull();
    expect((await screen.findByTestId('plc-too-long')).textContent).toBe(
      'The program is 100,024 characters; the check takes up to 100,000. Check it in parts.',
    );
    expect(reviewImpl).not.toHaveBeenCalled();
  });
});

describe('reviewPlc', () => {
  function respond(status: number, body: unknown): typeof fetch {
    return vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(body),
    });
  }

  it('posts the source to the review endpoint', async () => {
    const fetchImpl = respond(200, { status: 'valid', findings: [] });
    await reviewPlc({ source: 'x', fetchImpl });

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      { method: string; body: string },
    ];
    expect(url).toBe('/api/v1/plc/review');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ source: 'x' });
  });

  it('reads a verdict as reviewed', async () => {
    const outcome = await reviewPlc({
      source: 'x',
      fetchImpl: respond(200, { status: 'valid', findings: [] }),
    });
    expect(outcome.kind).toBe('reviewed');
  });

  it('reads a 422 as rejected, with the server’s reason', async () => {
    expect(await reviewPlc({ source: 'x', fetchImpl: respond(422, { detail: 'empty' }) })).toEqual({
      kind: 'rejected',
      detail: 'empty',
    });
  });

  it.each([
    ['a server error', respond(500, {})],
    ['a response with no verdict', respond(200, { findings: [] })],
    [
      'a network failure',
      vi.fn().mockRejectedValue(new Error('offline')) as unknown as typeof fetch,
    ],
  ])('reads %s as failed', async (_label, fetchImpl) => {
    expect(await reviewPlc({ source: 'x', fetchImpl })).toEqual({ kind: 'failed' });
  });
});
