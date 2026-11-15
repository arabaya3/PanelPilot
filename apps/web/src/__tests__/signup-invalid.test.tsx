import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { TrialLimitModal } from '@/components/chat/trial-limit-modal';
import { signupProblemsOf, signupClaimingTrial } from '@/lib/trial';

import { renderApp } from './helpers';

/** A signup the API refuses as entered (422 `request_invalid`) says why, in the reader's language. */

/** The API's strict 422 for a reserved domain (`*.test`), as FastAPI words it. */
const RESERVED_EMAIL = {
  error: 'RequestValidationError',
  code: 'request_invalid',
  params: { count: 1 },
  detail: [
    {
      type: 'value_error',
      loc: ['body', 'email'],
      msg: 'value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.',
      ctx: { reason: 'The part after the @-sign is a special-use or reserved name.' },
    },
  ],
};

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  }) as unknown as typeof fetch;
}

describe('signupProblemsOf', () => {
  it('names the email, and a password too short with its minimum', () => {
    expect(
      signupProblemsOf({
        detail: [
          ...RESERVED_EMAIL.detail,
          { type: 'string_too_short', loc: ['body', 'password'], ctx: { min_length: 12 } },
          { type: 'string_too_long', loc: ['body', 'full_name'], ctx: { max_length: 200 } },
        ],
      }),
    ).toEqual([
      { field: 'email' },
      { field: 'password', min: 12 },
      { field: 'full_name', max: 200 },
    ]);
  });

  it('is empty for a body that is not a validation list', () => {
    expect(signupProblemsOf(null)).toEqual([]);
    expect(signupProblemsOf({ detail: 'nope' })).toEqual([]);
  });
});

describe('signupClaimingTrial', () => {
  it('reads a 422 as invalid input, not a connection failure', async () => {
    const outcome = await signupClaimingTrial({
      email: 'a@b.test',
      password: 'a-long-password',
      fetchImpl: respond(422, RESERVED_EMAIL),
    });
    expect(outcome).toEqual({ kind: 'invalid', problems: [{ field: 'email' }] });
  });
});

describe('the signup modal', () => {
  function open(signupImpl: typeof signupClaimingTrial, locale?: 'ar' | 'he') {
    renderApp(
      <TrialLimitModal
        trial={null}
        onSignedUp={vi.fn()}
        onDismiss={vi.fn()}
        signupImpl={signupImpl}
      />,
      locale ? { locale } : {},
    );
  }

  it('says the email cannot be used, beside the email, and keeps what was typed', async () => {
    const signupImpl = vi.fn<typeof signupClaimingTrial>().mockResolvedValue({
      kind: 'invalid',
      problems: [{ field: 'email' }],
    });
    open(signupImpl);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'e@x.test' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'a-long-password' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));
    const error = await screen.findByTestId('trial-error');
    expect(error.textContent).toContain('This email address cannot be used');
    expect(error.textContent).not.toContain('Check the connection');
    const email = screen.getByLabelText<HTMLInputElement>('Email');
    expect(email.value).toBe('e@x.test');
    expect(email.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByLabelText<HTMLInputElement>('Password').value).toBe('a-long-password');
  });

  it('says how long the password must be', async () => {
    const signupImpl = vi.fn<typeof signupClaimingTrial>().mockResolvedValue({
      kind: 'invalid',
      problems: [{ field: 'password', min: 12 }],
    });
    open(signupImpl);
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'e@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: /create account/i }));
    expect((await screen.findByTestId('trial-error')).textContent).toContain('12');
  });

  it.each(['ar', 'he'] as const)('says it in %s', async (locale) => {
    const signupImpl = vi.fn<typeof signupClaimingTrial>().mockResolvedValue({
      kind: 'invalid',
      problems: [{ field: 'email' }],
    });
    open(signupImpl, locale);
    const email = document.querySelector<HTMLInputElement>('input[type="email"]');
    const password = document.querySelector<HTMLInputElement>('input[type="password"]');
    if (!email || !password) throw new Error('no form');
    fireEvent.change(email, { target: { value: 'e@x.test' } });
    fireEvent.change(password, { target: { value: 'a-long-password' } });
    fireEvent.submit(email.form as HTMLFormElement);
    const error = await screen.findByTestId('trial-error');
    expect(error.textContent).not.toBe('');
    expect(error.textContent).not.toContain('This email address cannot be used');
  });
});
