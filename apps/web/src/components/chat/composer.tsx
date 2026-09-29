'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { SendIcon, StopIcon } from '@/components/icons';

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
    <form
      onSubmit={submit}
      // The focus ring is drawn around the whole field-and-button pill rather
      // than the bare input inside it, where it crowded the button. Same
      // colour and weight as every other ring.
      className="flex items-center gap-2 rounded-lg border border-border bg-surface p-1 ps-3 shadow-sm transition-colors has-[input:focus-visible]:outline has-[input:focus-visible]:outline-2 has-[input:focus-visible]:outline-offset-2 has-[input:focus-visible]:outline-focus"
    >
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
        // The border is the form's, so the field and its button read as one
        // control; the focus ring stays on the field itself.
        className="min-w-0 flex-1 rounded-md bg-transparent px-1 py-2 text-base text-text placeholder:text-text-muted focus-visible:shadow-none focus-visible:outline-none"
      />
      {busy ? (
        <button type="button" onClick={onStop} className="btn btn-secondary">
          <StopIcon width="16" height="16" />
          {t('stop')}
        </button>
      ) : (
        <button type="submit" disabled={text.trim() === ''} className="btn btn-primary">
          <SendIcon width="16" height="16" />
          <span className="max-sm:sr-only">{t('send')}</span>
        </button>
      )}
    </form>
  );
}
