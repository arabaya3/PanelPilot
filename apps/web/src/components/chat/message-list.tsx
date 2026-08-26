'use client';

import { useVirtualizer } from '@tanstack/react-virtual';
import { useTranslations } from 'next-intl';
import { useEffect, useRef } from 'react';

import { DiagnosticCard } from '@/components/diagnostic-card';
import { useLocale } from '@/components/locale-provider';

import type { AssistantMessage, Message } from './state';

/**
 * The transcript.
 *
 * Windowed with `@tanstack/react-virtual` because a long troubleshooting
 * session accumulates many turns and each assistant turn is a whole diagnostic
 * card, not a line of text — the DOM cost is real rather than theoretical.
 *
 * Scrolling is the part that needed care in RTL. A common mistake is to treat
 * `scrollTop` as direction-dependent: it is not. Vertical scrolling is
 * unaffected by `dir`, so pinning to the bottom works identically in all three
 * locales — it is *horizontal* scroll offsets that differ between engines, and
 * this list has none by construction. The transcript's own writing direction
 * comes from the document, so nothing here sets `dir` itself.
 */
export function MessageList({
  messages,
  onRetry,
}: {
  messages: Message[];
  onRetry: (id: string) => void;
}) {
  const t = useTranslations('chat');
  const { direction } = useLocale();
  const scrollRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => scrollRef.current,
    // A diagnostic card is much taller than a question. The estimate only
    // needs to be close enough to avoid a visible correction; measured
    // elements replace it as soon as they render.
    estimateSize: () => 160,
    overscan: 4,
  });

  // Pin to the newest turn. An engineer who has scrolled up to re-read an
  // earlier step is deliberately not yanked back down — that would make the
  // transcript unusable during exactly the moment it is most useful.
  const pinnedRef = useRef(true);
  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const onScroll = () => {
      const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
      pinnedRef.current = distance < 80;
    };
    element.addEventListener('scroll', onScroll);
    return () => {
      element.removeEventListener('scroll', onScroll);
    };
  }, []);

  useEffect(() => {
    if (pinnedRef.current && messages.length > 0) {
      virtualizer.scrollToIndex(messages.length - 1, { align: 'end' });
    }
  }, [messages.length, virtualizer]);

  return (
    <div
      ref={scrollRef}
      // `log` rather than `feed`: turns are appended in order and a screen
      // reader should announce a new one without the user hunting for it.
      role="log"
      aria-live="polite"
      aria-label={t('transcript')}
      data-testid="transcript"
      data-direction={direction}
      className="flex-1 overflow-y-auto p-4"
    >
      {/* The empty state lives *inside* the live region rather than replacing
          it. A region that is created at the same moment as its first content
          is not reliably announced — the assistive technology has nothing to
          observe until the mutation has already happened. */}
      {messages.length === 0 ? (
        <p className="flex h-full items-center justify-center p-8 text-center text-text-muted">
          {t('empty')}
        </p>
      ) : null}
      <div
        className="relative w-full"
        style={{ height: `${String(virtualizer.getTotalSize())}px` }}
      >
        {virtualizer.getVirtualItems().map((item) => {
          const message = messages[item.index];
          if (!message) return null;
          return (
            <div
              key={message.id}
              ref={virtualizer.measureElement}
              data-index={item.index}
              className="absolute inset-x-0 top-0 pb-4"
              style={{ transform: `translateY(${String(item.start)}px)` }}
            >
              {message.role === 'user' ? (
                <UserTurn text={message.text} />
              ) : (
                <AssistantTurn message={message} onRetry={onRetry} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function UserTurn({ text }: { text: string }) {
  const t = useTranslations('chat');
  return (
    <div className="flex justify-end">
      <p
        // `ms-auto` and the logical padding flip with the document, so the
        // engineer's own turns sit on the trailing edge in every locale.
        className="ms-auto max-w-[80%] rounded-lg bg-accent px-3 py-2 text-accent-contrast"
        data-testid="user-turn"
      >
        <span className="sr-only">{t('youAsked')}</span>
        {text}
      </p>
    </div>
  );
}

/**
 * One answer.
 *
 * Three visual states, and the distinction between them is load-bearing: a
 * turn that was cut off mid-stream must never look like one that finished.
 */
function AssistantTurn({
  message,
  onRetry,
}: {
  message: AssistantMessage;
  onRetry: (id: string) => void;
}) {
  const t = useTranslations('chat');

  if (message.status === 'streaming') {
    return (
      <div
        className="rounded-lg border border-border bg-surface p-4 text-text-muted"
        data-testid="assistant-progress"
        data-stage={message.stage}
      >
        {/* Naming the stage rather than showing a bare spinner. An engineer
            watching a blank panel cannot tell a slow answer from a hung one,
            which is the entire reason the backend emits progress events. */}
        <span className="inline-flex items-center gap-2">
          <span aria-hidden="true" className="h-2 w-2 animate-pulse rounded-full bg-accent" />
          {t(`stage.${message.stage ?? 'retrieving'}`)}
        </span>
      </div>
    );
  }

  if (message.status === 'failed') {
    return (
      <div
        role="alert"
        className="rounded-lg border border-severity-warning bg-severity-warning-surface p-4"
        data-testid="assistant-failure"
        data-failure={message.failure}
      >
        <p className="font-medium text-text">{t('failed.heading')}</p>
        {/* Says what happened, because "try again" is useless advice if the
            answer was actually delivered and only the connection dropped. */}
        <p className="mt-1 text-sm text-text-muted">
          {t(`failed.${message.failure ?? 'connection-lost'}`)}
        </p>
        {/* The question is kept on the turn precisely so this does not make
            the engineer retype it — a fault description is long, and having to
            rewrite it is what makes people stop reporting the details. */}
        <button
          type="button"
          onClick={() => {
            onRetry(message.id);
          }}
          className="mt-3 rounded-md border border-border bg-surface px-3 py-1.5 text-sm text-text"
        >
          {t('retry')}
        </button>
      </div>
    );
  }

  if (!message.response) return null;
  return <DiagnosticCard response={message.response} />;
}
