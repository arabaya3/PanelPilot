'use client';

import { useCallback, useEffect, useReducer, useRef } from 'react';

import { useLocale } from '@/components/locale-provider';
import { streamDiagnosis, type StreamEvent, type StreamOptions } from '@/lib/diagnosis-stream';

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
export function Chat({
  token,
  streamImpl = streamDiagnosis,
}: {
  token: string;
  /** Injected in tests so a slow stream can be driven without a server. */
  streamImpl?: (options: StreamOptions) => AsyncGenerator<StreamEvent>;
}) {
  const [state, dispatch] = useReducer(chatReducer, INITIAL_STATE);
  const { locale } = useLocale();
  const abortRef = useRef<AbortController | null>(null);
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

      for await (const event of events) {
        dispatch({ type: 'stream', id: assistantId, event });
      }
      abortRef.current = null;
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
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const busy = state.messages.some(
    (message) => message.role === 'assistant' && message.status === 'streaming',
  );

  return (
    <div className="flex h-full flex-col" data-testid="chat">
      <MessageList messages={state.messages} />
      <Composer onSubmit={ask} onStop={stop} busy={busy} />
    </div>
  );
}
