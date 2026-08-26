'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { signupClaimingTrial, type SignupOutcome, type TrialSession } from '@/lib/trial';

/**
 * The one step between a finished trial and a working account.
 *
 * Deliberately not a gate in front of the product: it appears only after the
 * free questions are used, and the conversation behind it is carried into the
 * new account rather than restarted. Email and password, no approval, no wait.
 *
 * It also never interrupts an answer in progress — the caller is responsible
 * for that, and this component is only ever rendered once a turn has
 * completed. Cutting off a diagnosis to ask for an email would be a worse
 * version of the funnel this exists to avoid.
 */
export function TrialLimitModal({
  trial,
  onSignedUp,
  onDismiss,
  signupImpl = signupClaimingTrial,
}: {
  trial: TrialSession | null;
  onSignedUp: (tokens: { accessToken: string; refreshToken: string }) => void;
  onDismiss: () => void;
  signupImpl?: typeof signupClaimingTrial;
}) {
  const t = useTranslations('trial');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailId = useId();
  const passwordId = useId();
  const headingId = useId();
  const emailRef = useRef<HTMLInputElement>(null);

  // Focus lands in the first field, and Escape dismisses — the modal is a
  // request, not a trap.
  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);

    const outcome: SignupOutcome = await signupImpl({
      email: email.trim(),
      password,
      trial,
    });

    setBusy(false);
    if (outcome.kind === 'signed-up') {
      onSignedUp({ accessToken: outcome.accessToken, refreshToken: outcome.refreshToken });
      return;
    }
    // Named rather than generic. "Something went wrong" on a signup form is
    // the moment someone gives up, and each of these has a different remedy.
    setError(t(`error.${outcome.kind}`));
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
      data-testid="trial-limit-modal"
      className="fixed inset-0 z-50 flex items-center justify-center bg-bg/80 p-4"
      onKeyDown={(event) => {
        if (event.key === 'Escape') onDismiss();
      }}
    >
      <div className="w-full max-w-sm rounded-lg border border-border bg-surface p-4">
        <h2 id={headingId} className="text-lg font-semibold text-text">
          {t('heading')}
        </h2>
        {/* Says what happens to the conversation, because the fear this
            answers is losing it. */}
        <p className="mt-1 text-sm text-text-muted">
          {trial ? t('keepsConversation') : t('startsFresh')}
        </p>

        <form
          className="mt-3 flex flex-col gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <label htmlFor={emailId} className="text-xs text-text-muted">
            {t('email')}
          </label>
          <input
            id={emailId}
            ref={emailRef}
            type="email"
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
            className="rounded-md border border-border bg-surface px-2 py-1 text-text"
          />

          <label htmlFor={passwordId} className="text-xs text-text-muted">
            {t('password')}
          </label>
          <input
            id={passwordId}
            type="password"
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
            }}
            className="rounded-md border border-border bg-surface px-2 py-1 text-text"
          />

          {error ? (
            <p role="alert" data-testid="trial-error" className="text-sm text-severity-critical">
              {error}
            </p>
          ) : null}

          <div className="mt-2 flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-md bg-accent px-3 py-1 text-sm text-accent-contrast disabled:opacity-50"
            >
              {busy ? t('creating') : t('create')}
            </button>
            <button
              type="button"
              onClick={onDismiss}
              className="rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
            >
              {t('later')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
