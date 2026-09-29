'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { signIn } from '@/lib/auth';

/**
 * Email and password, for an account that already exists.
 *
 * Used on the home page, where a returning engineer would otherwise only be
 * offered a new trial, and on the review page, which has nothing to show a
 * visitor who is not signed in as a reviewer.
 */
export function SignInForm({
  onSignedIn,
  onCancel,
  signInImpl = signIn,
}: {
  onSignedIn: (tokens: { accessToken: string; refreshToken: string }) => void;
  /** Offered only where there is something to go back to. */
  onCancel?: () => void;
  signInImpl?: typeof signIn;
}) {
  const t = useTranslations('signIn');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const emailId = useId();
  const passwordId = useId();
  const headingId = useId();
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    emailRef.current?.focus();
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    const outcome = await signInImpl({ email: email.trim(), password });
    setBusy(false);
    if (outcome.kind === 'signed-in') {
      onSignedIn({ accessToken: outcome.accessToken, refreshToken: outcome.refreshToken });
      return;
    }
    setError(t(`error.${outcome.kind}`));
  }

  return (
    <form
      aria-labelledby={headingId}
      data-testid="sign-in-form"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
      className="flex max-w-sm flex-col gap-3 rounded-lg border border-border bg-surface p-4"
    >
      <h2 id={headingId} className="text-lg font-semibold text-text">
        {t('heading')}
      </h2>
      <label htmlFor={emailId} className="text-sm text-text">
        {t('email')}
      </label>
      <input
        ref={emailRef}
        id={emailId}
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(event) => {
          setEmail(event.target.value);
        }}
        className="rounded-md border border-border bg-surface-raised px-3 py-2 text-text"
      />
      <label htmlFor={passwordId} className="text-sm text-text">
        {t('password')}
      </label>
      <input
        id={passwordId}
        type="password"
        autoComplete="current-password"
        required
        value={password}
        onChange={(event) => {
          setPassword(event.target.value);
        }}
        className="rounded-md border border-border bg-surface-raised px-3 py-2 text-text"
      />
      {error !== null && (
        <p role="alert" data-testid="sign-in-error" className="text-sm text-severity-critical">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <button
          type="submit"
          disabled={busy || email.trim() === '' || password === ''}
          className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-contrast disabled:opacity-50"
        >
          {busy ? t('submitting') : t('submit')}
        </button>
        {onCancel !== undefined && (
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md border border-border px-4 py-2 text-sm text-text"
          >
            {t('cancel')}
          </button>
        )}
      </div>
    </form>
  );
}
