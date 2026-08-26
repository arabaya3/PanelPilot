import type { components } from '@panelpilot/shared-types';

import type { InterruptionReason, StreamEvent } from '@/lib/diagnosis-stream';

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

/**
 * The chat transcript, as a reducer.
 *
 * Kept separate from the components so the state machine can be tested
 * directly — the interesting behaviour here is what happens when a stream is
 * interrupted, and driving that through rendered DOM would test React more
 * than it tests the rule.
 *
 * The rule that matters: an assistant turn is only ever `complete` when a
 * terminal `result` actually arrived. Every other ending — a dropped
 * connection, an abort, a malformed frame — lands in `failed`, which renders
 * as a failure. A turn must never be able to *look* finished because the
 * connection went away quietly.
 */

/** A question from the engineer. */
export interface UserMessage {
  id: string;
  role: 'user';
  text: string;
}

/** An answer, in whatever state it has reached. */
export interface AssistantMessage {
  id: string;
  role: 'assistant';
  /**
   * `streaming` covers everything up to the terminal event. The stage is
   * carried alongside so the UI can say *which* stage rather than showing an
   * undifferentiated spinner — the acceptance criterion is that something
   * appears within about a second, and "retrieving" is that something.
   */
  status: 'streaming' | 'complete' | 'failed';
  stage?: 'retrieving' | 'generated' | 'refused';
  response?: DiagnosticResponse;
  failure?: InterruptionReason;
  /** The question this answers, so a failed turn can be retried as-is. */
  prompt: string;
}

export type Message = UserMessage | AssistantMessage;

export interface ChatState {
  sessionId: string | null;
  messages: Message[];
}

export type ChatAction =
  | { type: 'ask'; userId: string; assistantId: string; text: string }
  | { type: 'stream'; id: string; event: StreamEvent }
  | { type: 'retry'; id: string }
  | { type: 'session'; sessionId: string };

export const INITIAL_STATE: ChatState = { sessionId: null, messages: [] };

/** Replace one message, leaving the rest of the transcript untouched. */
function replace(messages: Message[], id: string, update: (m: AssistantMessage) => Message) {
  return messages.map((message) =>
    message.id === id && message.role === 'assistant' ? update(message) : message,
  );
}

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'ask':
      return {
        ...state,
        messages: [
          ...state.messages,
          { id: action.userId, role: 'user', text: action.text },
          {
            id: action.assistantId,
            role: 'assistant',
            status: 'streaming',
            stage: 'retrieving',
            prompt: action.text,
          },
        ],
      };

    case 'session':
      // The session id arrives with the first response and is then reused for
      // every later turn, which is what makes the conversation a conversation
      // rather than a series of unrelated questions.
      return { ...state, sessionId: action.sessionId };

    case 'stream': {
      const { event } = action;
      if (event.kind === 'stage') {
        return {
          ...state,
          messages: replace(state.messages, action.id, (m) => ({
            ...m,
            status: 'streaming',
            stage: event.stage,
          })),
        };
      }
      if (event.kind === 'result') {
        return {
          ...state,
          sessionId: event.response.session_id,
          messages: replace(state.messages, action.id, (m) => ({
            id: m.id,
            role: 'assistant',
            prompt: m.prompt,
            status: 'complete',
            response: event.response,
          })),
        };
      }
      // Interrupted. Deliberately clears any partial state: a turn that was
      // cut off must not keep the last stage it reached and read as progress.
      return {
        ...state,
        messages: replace(state.messages, action.id, (m) => ({
          id: m.id,
          role: 'assistant',
          prompt: m.prompt,
          status: 'failed',
          failure: event.reason,
        })),
      };
    }

    case 'retry':
      // Rebuilt rather than spread: a retried turn must not carry the failure
      // or the stale response of the attempt before it.
      return {
        ...state,
        messages: replace(state.messages, action.id, (m) => ({
          id: m.id,
          role: 'assistant',
          prompt: m.prompt,
          status: 'streaming',
          stage: 'retrieving',
        })),
      };
  }
}
