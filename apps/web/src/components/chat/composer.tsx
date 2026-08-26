'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';

/**
 * The question input.
 *
 * Controlled, with a submit/stop affordance that swaps rather than sitting
 * beside a disabled twin — a disabled submit button during a long turn gives
 * the engineer nothing to press when they realise they asked the wrong thing.
 */
export function Composer({
  onSubmit,
  onStop,
  busy,
}: {
  onSubmit: (text: string) => void;
  onStop: () => void;
  busy: boolean;
}) {
  const t = useTranslations('chat');
  const [text, setText] = useState('');

  function submit(event: { preventDefault: () => void }) {
    event.preventDefault();
    const trimmed = text.trim();
    // Whitespace is not a question. Submitting one would burn a free-tier
    // question on nothing, which the backend counts server-side.
    if (trimmed === '' || busy) return;
    setText('');
    onSubmit(trimmed);
  }

  return (
    <form onSubmit={submit} className="flex gap-2 border-t border-border p-4">
      <label className="sr-only" htmlFor="chat-input">
        {t('inputLabel')}
      </label>
      <input
        id="chat-input"
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        placeholder={t('placeholder')}
        // The field stays enabled while a turn runs so the next question can
        // be typed; only submission is gated.
        className="flex-1 rounded-md border border-border bg-surface px-3 py-2 text-text placeholder:text-text-muted"
      />
      {busy ? (
        <button
          type="button"
          onClick={onStop}
          className="rounded-md border border-border bg-surface px-4 py-2 text-text"
        >
          {t('stop')}
        </button>
      ) : (
        <button
          type="submit"
          disabled={text.trim() === ''}
          className="rounded-md bg-accent px-4 py-2 text-accent-contrast disabled:opacity-50"
        >
          {t('send')}
        </button>
      )}
    </form>
  );
}
