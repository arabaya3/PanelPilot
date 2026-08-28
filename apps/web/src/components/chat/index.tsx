'use client';

import type { components } from '@panelpilot/shared-types';

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import { useLocale } from '@/components/locale-provider';
import { streamDiagnosis, type StreamEvent, type StreamOptions } from '@/lib/diagnosis-stream';
import { fetchSession, listSessions } from '@/lib/sessions';
import type { uploadImage } from '@/lib/recognition';
import type { TrialSession } from '@/lib/trial';

import { ChecklistProvider, useChecklist } from './checklist-provider';
import { ContextChip, contextFromResponse } from './context-chip';
import { HistorySidebar } from './history-sidebar';
import { Composer } from './composer';
import { ImageCapture } from './image-capture';
import { TrialLimitModal } from './trial-limit-modal';
import { MessageList } from './message-list';
import { chatReducer, INITIAL_STATE } from './state';

type EquipmentContext = components['schemas']['EquipmentContext'];

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
  /** Injected the same way, so the capture path can be driven end to end. */
  uploadImpl?: typeof uploadImage;
  /** The anonymous trial this browser holds, if any. */
  trial?: TrialSession | null;
  /** How many free questions are left; `null` when the caller does not know. */
  questionsRemaining?: number | null;
  onSignedUp?: (tokens: { accessToken: string; refreshToken: string }) => void;
  /** Injected in tests so the history list can be driven without a server. */
  listImpl?: typeof listSessions;
  /** Injected the same way, for hydrating a selected session. */
  fetchSessionImpl?: typeof fetchSession;
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
  uploadImpl,
  trial = null,
  questionsRemaining = null,
  onSignedUp,
  listImpl = listSessions,
  fetchSessionImpl = fetchSession,
}: {
  token: string;
  streamImpl?: (options: StreamOptions) => AsyncGenerator<StreamEvent>;
  uploadImpl?: typeof uploadImage;
  trial?: TrialSession | null;
  questionsRemaining?: number | null;
  onSignedUp?: (tokens: { accessToken: string; refreshToken: string }) => void;
  listImpl?: typeof listSessions;
  fetchSessionImpl?: typeof fetchSession;
}) {
  const [state, dispatch] = useReducer(chatReducer, INITIAL_STATE);
  const checklist = useChecklist();
  const [context, setContext] = useState<EquipmentContext | null>(null);
  const [dismissedLimit, setDismissedLimit] = useState(false);
  // Mirrored into a ref so `run` can read the current value without taking it
  // as a dependency — the value that matters is the one current when the
  // request is actually sent, not when the callback was built.
  //
  // Assigned here on every render and nowhere else. Two mutation tests proved
  // the point: removing the assignment in the adopt path, and removing it in
  // the editor's `onChange`, both left every test green *and* left the
  // behaviour correct, because this line had already repaired the ref by the
  // time anything read it. They were equivalent mutants, not gaps in coverage
  // — and a hand-maintained copy that is never actually needed is worse than
  // none, since the next reader has to work out whether it is load-bearing.
  const contextRef = useRef<EquipmentContext | null>(null);
  contextRef.current = context;
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
          // The whole point of the chip: the engineer states the equipment
          // once and every later question carries it.
          equipment: contextRef.current,
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
          if (event.kind === 'result') {
            // Adopt a model the assistant named, but only when the engineer
            // has not set one — see `contextFromResponse`.
            const adopted = contextFromResponse(contextRef.current, event.response);
            if (adopted) setContext(adopted);
          }
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

  // Bumped when a turn completes, so the sidebar re-fetches: a new conversation
  // has to appear in the list, and an existing one has to move to the top.
  const [historyKey, setHistoryKey] = useState(0);
  const completed = state.messages.filter(
    (message) => message.role === 'assistant' && message.status === 'complete',
  ).length;
  useEffect(() => {
    if (completed > 0) setHistoryKey((previous) => previous + 1);
  }, [completed]);

  const openSession = useCallback(
    async (sessionId: string) => {
      // A live turn is abandoned rather than left running: its events would
      // dispatch into a transcript that has since been replaced, appending an
      // answer from the previous conversation to the one just opened.
      abortRef.current?.abort();
      liveRef.current = null;

      const result = await fetchSessionImpl({ token, sessionId });
      if (result.kind !== 'loaded') return;

      dispatch({ type: 'hydrate', sessionId: result.session.id, turns: result.session.turns });

      // The acceptance criterion: the context indicator comes back too, not
      // just the messages. Taken from the last turn that recorded equipment,
      // which is the same rule the live path uses -- and set directly rather
      // than through `contextFromResponse`, because that function's job is to
      // protect an engineer's own entry from being overwritten mid-session,
      // and opening a different conversation is exactly when it should be.
      const restored = lastEquipmentModel(result.session.turns);
      setContext(
        restored === null ? null : { manufacturer: null, model: restored, fault_codes: [] },
      );
    },
    [fetchSessionImpl, token],
  );

  // The limit modal appears only once the free questions are gone *and* no
  // turn is in flight. The spec is explicit that it must never interrupt an
  // answer, and the reason is easy to underrate: cutting off a diagnosis to
  // ask for an email is a worse version of the funnel this flow exists to
  // avoid. `busy` already means "a turn has not finished", so gating on it
  // makes the rule structural rather than a timing hope.
  const outOfQuestions = questionsRemaining !== null && questionsRemaining <= 0;
  const showLimit = outOfQuestions && !busy && !dismissedLimit;

  return (
    // A row, so the sidebar sits beside the conversation. Which side that is
    // follows the document direction rather than being pinned here: in Arabic
    // or Hebrew the same markup puts it on the visual right.
    <div className="flex h-full" data-testid="chat">
      <HistorySidebar
        token={token}
        activeSessionId={state.sessionId}
        onSelect={(id) => void openSession(id)}
        listImpl={listImpl}
        refreshKey={historyKey}
      />
      <div className="flex h-full min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-border p-2">
          <ContextChip context={context} onChange={setContext} />
        </header>
        <MessageList messages={state.messages} onRetry={retry} />
        <ImageCapture token={token} onConfirm={ask} {...(uploadImpl ? { uploadImpl } : {})} />
        <Composer onSubmit={ask} onStop={stop} busy={busy} />
        {showLimit ? (
          <TrialLimitModal
            trial={trial}
            onSignedUp={(tokens) => {
              setDismissedLimit(true);
              onSignedUp?.(tokens);
            }}
            onDismiss={() => {
              setDismissedLimit(true);
            }}
          />
        ) : null}
      </div>
    </div>
  );
}

/**
 * The equipment the conversation most recently identified.
 *
 * Read from the end backwards so a follow-up that names no unit does not blank
 * a context the conversation had already established, and so an engineer who
 * moved to a different machine mid-session gets the machine they moved to.
 */
function lastEquipmentModel(turns: components['schemas']['DiagnosticTurn'][]): string | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const model = turns[index]?.response.diagnosis?.equipment_model;
    if (typeof model === 'string' && model !== '') return model;
  }
  return null;
}
