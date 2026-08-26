'use client';

import { useCallback, useEffect, useReducer, useRef } from 'react';

import { useLocale } from '@/components/locale-provider';
import { streamDiagnosis, type StreamEvent, type StreamOptions } from '@/lib/diagnosis-stream';

import { ChecklistProvider, useChecklist } from './checklist-provider';
import { Composer } from './composer';
import { MessageList } from './message-list';
import { chatReducer, INITIAL_STATE } from './state';

/**
 * The chat surface.
 *
 * Every other feature attaches here — image input, the solution checklist, the
 * session context indicator — so this owns the session and the turn lifecycle
 * and nothing else.
 *
 * The session itself lives server-side and is fetched by id; this holds only
 * the id and the turns of the current view. Deliberately not persisted to the
 * browser: a diagnostic transcript describes a specific machine in a specific
 * plant, and leaving it in `localStorage` on a shared workshop terminal is a
 * disclosure nobody asked for.
 */
export function Chat(props: {
  token: string;
  /** Injected in tests so a slow stream can be driven without a server. */
  streamImpl?: (options: StreamOptions) => AsyncGenerator<StreamEvent>;
}) {
  return (
    <ChecklistProvider>
      <ChatSurface {...props} />
    </ChecklistProvider>
  );
}

/**
 * The surface itself, inside the checklist provider.
 *
 * Split from `Chat` only so it can *read* the checklist it is wrapped in —
 * retrying a turn has to forget that turn's ticks, and a component cannot
 * consume a context it renders.
 */
function ChatSurface({
  token,
  streamImpl = streamDiagnosis,
}: {
  token: string;
  streamImpl?: (options: StreamOptions) => AsyncGenerator<StreamEvent>;
}) {
  const [state, dispatch] = useReducer(chatReducer, INITIAL_STATE);
  const checklist = useChecklist();
  const { locale } = useLocale();
  const abortRef = useRef<AbortController | null>(null);
  // The turn the abort belongs to. `stop()` needs it because aborting alone
  // is not enough to end a turn — see the comment on `stop`.
  const liveRef = useRef<string | null>(null);
  const idRef = useRef(0);

  // Abort any live turn when the surface goes away, so a stream does not
  // outlive the component and dispatch into a dead reducer.
  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const run = useCallback(
    async (assistantId: string, text: string) => {
      const controller = new AbortController();
      abortRef.current = controller;
      liveRef.current = assistantId;

      const events = streamImpl({
        request: {
          session_id: state.sessionId,
          symptom: text,
          // Explicit on every request. The backend refuses to default it,
          // because a default means a caller that forgot one gets English —
          // which for an Arabic-speaking engineer looks like working software
          // until someone reads the answer.
          locale,
        },
        token,
        signal: controller.signal,
      });

      try {
        for await (const event of events) {
          dispatch({ type: 'stream', id: assistantId, event });
        }
      } finally {
        // `return()` rather than abandoning the iterator: an abandoned async
        // generator never runs its `finally`, so the response body would
        // never be cancelled and the connection would leak.
        await events.return(undefined);
        if (liveRef.current === assistantId) {
          abortRef.current = null;
          liveRef.current = null;
        }
      }
    },
    [locale, state.sessionId, streamImpl, token],
  );

  const ask = useCallback(
    (text: string) => {
      idRef.current += 1;
      const n = idRef.current;
      const assistantId = `a${String(n)}`;
      dispatch({ type: 'ask', userId: `u${String(n)}`, assistantId, text });
      void run(assistantId, text);
    },
    [run],
  );

  const stop = useCallback(() => {
    // Aborting is necessary but not sufficient, and assuming otherwise wedged
    // this surface completely.
    //
    // `AbortController.abort()` can only interrupt a generator suspended
    // *inside* `reader.read()`. Whenever one chunk carries more than one
    // frame — the routine case, since the backend emits `retrieving` and
    // `generated` back to back and any proxy or TCP segment coalesces them —
    // the generator is instead suspended at a `yield` in userland, where an
    // abort signal cannot reach it. Nothing rejected, nothing resumed, no
    // terminal event was ever produced: the turn kept its spinner forever,
    // `busy` stayed true because it is derived from that status, and the
    // composer kept offering Stop instead of Send. Pressing the one control
    // offered for a hung turn was what hung it, permanently.
    //
    // So the terminal state is dispatched here, where it does not depend on
    // where the generator happens to be parked.
    const id = liveRef.current;
    abortRef.current?.abort();
    abortRef.current = null;
    liveRef.current = null;
    if (id !== null) {
      dispatch({ type: 'stream', id, event: { kind: 'interrupted', reason: 'aborted' } });
    }
  }, []);

  const retry = useCallback(
    (id: string) => {
      const message = state.messages.find((m) => m.id === id);
      if (!message || message.role !== 'assistant') return;
      // Forget this turn's ticks before re-asking. A retry reuses the same
      // message id and can return a different set of steps, and ticks are
      // positional — so without this, step 2 of advice the engineer has never
      // read comes back pre-ticked and struck through. That reads as "I
      // already did this", and a skipped step in an electrical repair is a
      // safety consequence rather than a cosmetic one.
      checklist?.clear(id);
      dispatch({ type: 'retry', id });
      void run(id, message.prompt);
    },
    [checklist, run, state.messages],
  );

  const busy = state.messages.some(
    (message) => message.role === 'assistant' && message.status === 'streaming',
  );

  return (
    <div className="flex h-full flex-col" data-testid="chat">
      <MessageList messages={state.messages} onRetry={retry} />
      <Composer onSubmit={ask} onStop={stop} busy={busy} />
    </div>
  );
}
