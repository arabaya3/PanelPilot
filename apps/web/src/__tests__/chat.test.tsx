import { fireEvent, screen, waitFor } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it, vi } from 'vitest';

import { Chat } from '@/components/chat';
import { chatReducer, INITIAL_STATE, type ChatState } from '@/components/chat/state';
import {
  parseFrames,
  streamDiagnosis,
  type StreamEvent,
  type StreamOptions,
} from '@/lib/diagnosis-stream';

import { renderApp } from './helpers';

/**
 * Tests for the chat surface.
 *
 * The spec's headline risk is the one these lean on hardest: "a network
 * interruption mid-stream must leave a resumable/clearly-failed state, not a
 * silently truncated answer". A turn that stops quietly and *looks* finished
 * is the failure mode, and it is invisible by construction — the transcript
 * shows an answer either way.
 *
 * Note the backend does not stream tokens. `app/models/schemas/streaming.py`
 * is explicit that no partial answer is ever sent, because "a refusal that
 * arrives after three paragraphs of a confident-sounding draft is not a
 * refusal". So what streams is progress, and what is asserted below is that
 * each stage appears as it arrives rather than all at once at the end.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

const RESPONSE: DiagnosticResponse = {
  session_id: 'session-9',
  answer: {
    text: 'Undervoltage.',
    citations: [
      {
        document_id: 'doc-1',
        document_title: 'ACS880 Firmware Manual',
        manufacturer: 'ABB',
        page: 214,
        section: '6.3',
      },
    ],
  },
  diagnosis: {
    summary: 'The drive tripped on DC bus undervoltage.',
    summary_citation_ids: ['doc-1'],
    severity: 'critical',
    equipment_model: 'ACS880',
    steps: [
      {
        order: 1,
        instruction: 'Measure the supply voltage at the input terminals.',
        rationale: 'An undervoltage trip most often follows a supply fault.',
        citation_ids: ['doc-1'],
        severity: 'critical',
      },
    ],
  },
  confidence: {
    overall: 0.9,
    retrieval_score: 0.9,
    passage_agreement: 0.9,
    citation_density: 0.9,
  },
  low_confidence: false,
  refusal_message: null,
};

// --- frame parsing ----------------------------------------------------------

describe('parseFrames', () => {
  it('holds an incomplete frame until its terminator arrives', () => {
    // The silent-truncation failure starts here. A chunk boundary can fall
    // anywhere, including inside a `data:` line, and parsing eagerly would
    // truncate the response at whatever byte the network split on.
    const half = 'event: result\ndata: {"session_id":"s1","low_conf';
    const first = parseFrames(half);
    expect(first.frames).toHaveLength(0);
    expect(first.rest).toBe(half);

    const whole = parseFrames(first.rest + 'idence":false}\n\n');
    expect(whole.frames).toHaveLength(1);
    expect(whole.frames[0]?.event).toBe('result');
    expect(JSON.parse(whole.frames[0]?.data ?? '{}')).toMatchObject({ session_id: 's1' });
  });

  it('reads several frames from one chunk', () => {
    const { frames } = parseFrames('event: retrieving\ndata: {}\n\nevent: generated\ndata: {}\n\n');
    expect(frames.map((f) => f.event)).toEqual(['retrieving', 'generated']);
  });

  it('accepts CRLF line endings', () => {
    // An intermediary may rewrite these, and an unrecognised terminator would
    // stall the stream forever rather than fail.
    const { frames } = parseFrames('event: retrieving\r\ndata: {}\r\n\r\n');
    expect(frames).toHaveLength(1);
  });

  it('ignores keep-alive comments', () => {
    const { frames } = parseFrames(': keep-alive\n\nevent: generated\ndata: {}\n\n');
    expect(frames.map((f) => f.event)).toEqual(['generated']);
  });
});

// --- the stream itself ------------------------------------------------------

/** A response whose body yields the given chunks, one read at a time. */
function streamingResponse(chunks: string[], { truncate = false } = {}): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const body = {
    getReader() {
      return {
        read: () =>
          Promise.resolve(
            index < chunks.length
              ? { done: false, value: encoder.encode(chunks[index++]) }
              : { done: true, value: undefined },
          ),
        releaseLock: () => undefined,
      };
    },
  };
  return { ok: true, body: truncate ? body : body } as unknown as Response;
}

async function collect(generator: AsyncGenerator<StreamEvent>): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const event of generator) out.push(event);
  return out;
}

describe('streamDiagnosis', () => {
  it('reports each stage and then the result', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        streamingResponse([
          'event: retrieving\ndata: {}\n\n',
          'event: generated\ndata: {}\n\n',
          `event: result\ndata: ${JSON.stringify(RESPONSE)}\n\n`,
        ]),
      );

    const events = await collect(
      streamDiagnosis({
        request: { symptom: 'x', locale: 'en', session_id: null },
        token: 't',
        fetchImpl,
      }),
    );

    expect(events.map((e) => e.kind)).toEqual(['stage', 'stage', 'result']);
    expect(events[0]).toMatchObject({ stage: 'retrieving' });
    expect(events[2]).toMatchObject({ kind: 'result' });
  });

  it('reports interruption when the body ends without a result', async () => {
    // The spec's named edge case. The stream simply stops after a progress
    // event — no error, no close frame — and the turn must not be left
    // looking finished.
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        streamingResponse(['event: retrieving\ndata: {}\n\n', 'event: generated\ndata: {}\n\n']),
      );

    const events = await collect(
      streamDiagnosis({
        request: { symptom: 'x', locale: 'en', session_id: null },
        token: 't',
        fetchImpl,
      }),
    );

    expect(events.at(-1)).toEqual({ kind: 'interrupted', reason: 'connection-lost' });
  });

  it('discards a damaged result rather than showing part of it', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        streamingResponse(['event: result\ndata: {"session_id": "s1", trunc\n\n']),
      );

    const events = await collect(
      streamDiagnosis({
        request: { symptom: 'x', locale: 'en', session_id: null },
        token: 't',
        fetchImpl,
      }),
    );

    expect(events.at(-1)).toEqual({ kind: 'interrupted', reason: 'malformed-frame' });
  });

  it('reports a server error rather than hanging', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false, body: null });
    const events = await collect(
      streamDiagnosis({
        request: { symptom: 'x', locale: 'en', session_id: null },
        token: 't',
        fetchImpl,
      }),
    );
    expect(events).toEqual([{ kind: 'interrupted', reason: 'server-error' }]);
  });

  it('sends the locale and the bearer token', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(
        streamingResponse([`event: result\ndata: ${JSON.stringify(RESPONSE)}\n\n`]),
      );

    await collect(
      streamDiagnosis({
        request: { symptom: 'x', locale: 'ar', session_id: null },
        token: 'secret',
        fetchImpl,
      }),
    );

    const init = fetchImpl.mock.calls[0]?.[1] as { body: string; headers: Record<string, string> };
    expect(JSON.parse(init.body)).toMatchObject({ locale: 'ar' });
    expect(init.headers.Authorization).toBe('Bearer secret');
  });
});

// --- the reducer ------------------------------------------------------------

describe('chatReducer', () => {
  function asked(): ChatState {
    return chatReducer(INITIAL_STATE, {
      type: 'ask',
      userId: 'u1',
      assistantId: 'a1',
      text: 'Why is it tripping?',
    });
  }

  it('appends a question and a pending answer together', () => {
    const state = asked();
    expect(state.messages.map((m) => m.role)).toEqual(['user', 'assistant']);
    expect(state.messages[1]).toMatchObject({ status: 'streaming', stage: 'retrieving' });
  });

  it('completes only on a terminal result', () => {
    const state = chatReducer(asked(), {
      type: 'stream',
      id: 'a1',
      event: { kind: 'result', response: RESPONSE },
    });
    expect(state.messages[1]).toMatchObject({ status: 'complete' });
    expect(state.sessionId).toBe('session-9');
  });

  it('never leaves an interrupted turn looking finished', () => {
    // The core rule. A turn that reached `generated` and then lost the
    // connection had a stage, and keeping it would render as progress
    // forever; showing it as complete would be worse still.
    const midway = chatReducer(asked(), {
      type: 'stream',
      id: 'a1',
      event: { kind: 'stage', stage: 'generated' },
    });
    const cut = chatReducer(midway, {
      type: 'stream',
      id: 'a1',
      event: { kind: 'interrupted', reason: 'connection-lost' },
    });

    const message = cut.messages[1];
    expect(message).toMatchObject({ status: 'failed', failure: 'connection-lost' });
    expect(message && 'stage' in message ? message.stage : undefined).toBeUndefined();
    expect(message && 'response' in message ? message.response : undefined).toBeUndefined();
  });

  it('keeps the question so a failed turn can be retried as asked', () => {
    const cut = chatReducer(asked(), {
      type: 'stream',
      id: 'a1',
      event: { kind: 'interrupted', reason: 'connection-lost' },
    });
    expect(cut.messages[1]).toMatchObject({ prompt: 'Why is it tripping?' });

    const again = chatReducer(cut, { type: 'retry', id: 'a1' });
    expect(again.messages[1]).toMatchObject({ status: 'streaming', stage: 'retrieving' });
    // And the failure does not survive the retry.
    const message = again.messages[1];
    expect(message && 'failure' in message ? message.failure : undefined).toBeUndefined();
  });

  it('reuses the session id on later turns', () => {
    const first = chatReducer(asked(), {
      type: 'stream',
      id: 'a1',
      event: { kind: 'result', response: RESPONSE },
    });
    const second = chatReducer(first, {
      type: 'ask',
      userId: 'u2',
      assistantId: 'a2',
      text: 'And now?',
    });
    expect(second.sessionId).toBe('session-9');
  });

  it('updates only the turn it names', () => {
    const first = chatReducer(asked(), {
      type: 'stream',
      id: 'a1',
      event: { kind: 'result', response: RESPONSE },
    });
    const second = chatReducer(first, {
      type: 'ask',
      userId: 'u2',
      assistantId: 'a2',
      text: 'And now?',
    });
    const cut = chatReducer(second, {
      type: 'stream',
      id: 'a2',
      event: { kind: 'interrupted', reason: 'connection-lost' },
    });

    expect(cut.messages[1]).toMatchObject({ status: 'complete' });
    expect(cut.messages[3]).toMatchObject({ status: 'failed' });
  });
});

// --- rendering, against a deliberately slow stream --------------------------

/** A stream the test releases one event at a time. */
function controllableStream() {
  const queue: ((value: IteratorResult<StreamEvent, undefined>) => void)[] = [];
  const pending: StreamEvent[] = [];
  let done = false;

  async function* generator(): AsyncGenerator<StreamEvent> {
    for (;;) {
      if (pending.length > 0) {
        yield pending.shift() as StreamEvent;
        continue;
      }
      if (done) return;
      const next = await new Promise<IteratorResult<StreamEvent, undefined>>((resolve) => {
        queue.push(resolve);
      });
      if (next.done) return;
      yield next.value;
    }
  }

  return {
    generator,
    emit(event: StreamEvent) {
      const resolve = queue.shift();
      if (resolve) resolve({ done: false, value: event });
      else pending.push(event);
    },
    end() {
      done = true;
      const resolve = queue.shift();
      if (resolve) resolve({ done: true, value: undefined });
    },
  };
}

describe('Chat', () => {
  it('renders each stage as it arrives, not all at once at the end', async () => {
    // The acceptance criterion. Driven by a stream held open between
    // assertions, so a component that buffered until completion would show
    // nothing here and fail.
    const stream = controllableStream();
    renderApp(<Chat token="t" streamImpl={stream.generator} />);

    fireEvent.change(screen.getByLabelText(/describe the fault/i), {
      target: { value: 'Tripping on start' },
    });
    fireEvent.submit(
      screen.getByRole('button', { name: /send/i }).closest('form') as HTMLFormElement,
    );

    // The question is on screen immediately, before any server response.
    await waitFor(() => {
      expect(screen.getByTestId('user-turn')).toBeTruthy();
    });
    expect(screen.getByTestId('assistant-progress').getAttribute('data-stage')).toBe('retrieving');

    stream.emit({ kind: 'stage', stage: 'generated' });
    await waitFor(() => {
      expect(screen.getByTestId('assistant-progress').getAttribute('data-stage')).toBe('generated');
    });

    stream.emit({ kind: 'result', response: RESPONSE });
    stream.end();
    await waitFor(() => {
      expect(screen.getByTestId('diagnostic-card')).toBeTruthy();
    });
    expect(screen.queryByTestId('assistant-progress')).toBeNull();
  });

  it('shows a clear failure when the stream is cut off', async () => {
    const stream = controllableStream();
    renderApp(<Chat token="t" streamImpl={stream.generator} />);

    fireEvent.change(screen.getByLabelText(/describe the fault/i), {
      target: { value: 'Tripping on start' },
    });
    fireEvent.submit(
      screen.getByRole('button', { name: /send/i }).closest('form') as HTMLFormElement,
    );

    stream.emit({ kind: 'stage', stage: 'generated' });
    stream.emit({ kind: 'interrupted', reason: 'connection-lost' });
    stream.end();

    await waitFor(() => {
      expect(screen.getByTestId('assistant-failure')).toBeTruthy();
    });
    // Not a card, and not a spinner. Either would misrepresent what happened.
    expect(screen.queryByTestId('diagnostic-card')).toBeNull();
    expect(screen.queryByTestId('assistant-progress')).toBeNull();
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('does not submit an empty question', () => {
    const stream = controllableStream();
    const spy = vi.fn(stream.generator);
    renderApp(<Chat token="t" streamImpl={spy} />);

    fireEvent.change(screen.getByLabelText(/describe the fault/i), { target: { value: '   ' } });
    fireEvent.submit(
      screen.getByRole('button', { name: /send/i }).closest('form') as HTMLFormElement,
    );

    // The backend counts free-tier questions server-side; submitting
    // whitespace would spend one on nothing.
    expect(spy).not.toHaveBeenCalled();
  });

  it('offers a stop control only while a turn is running', async () => {
    const stream = controllableStream();
    renderApp(<Chat token="t" streamImpl={stream.generator} />);

    expect(screen.queryByRole('button', { name: /stop/i })).toBeNull();

    fireEvent.change(screen.getByLabelText(/describe the fault/i), { target: { value: 'x' } });
    fireEvent.submit(
      screen.getByRole('button', { name: /send/i }).closest('form') as HTMLFormElement,
    );

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /stop/i })).toBeTruthy();
    });

    stream.emit({ kind: 'result', response: RESPONSE });
    stream.end();
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /stop/i })).toBeNull();
    });
  });

  it('marks the transcript as a live region so a new turn is announced', () => {
    renderApp(<Chat token="t" streamImpl={controllableStream().generator} />);
    // Rendered even when empty, so the region exists before the first turn
    // arrives — a live region created at the same moment as its content is
    // not reliably announced.
    const transcript = screen.getByTestId('transcript');
    expect(transcript.getAttribute('aria-live')).toBe('polite');
    expect(transcript.getAttribute('role')).toBe('log');
  });

  it.each(['en', 'ar', 'he'] as const)(
    'scrolls vertically regardless of direction (%s)',
    (locale) => {
      // Vertical scroll is unaffected by `dir`; the RTL hazard is horizontal
      // offsets, which this list has none of by construction. Asserted per
      // locale so a later change that introduces a horizontal scroller has to
      // confront it here.
      renderApp(<Chat token="t" streamImpl={controllableStream().generator} />, { locale });
      const transcript = screen.getByTestId('transcript');
      expect(transcript.className).toContain('overflow-y-auto');
      expect(transcript.className).not.toContain('overflow-x');
      expect(transcript.getAttribute('data-direction')).toBe(locale === 'en' ? 'ltr' : 'rtl');
    },
  );
});

// --- the locale actually reaches the request --------------------------------

describe('Chat locale', () => {
  it.each(['en', 'ar', 'he'] as const)('asks the backend to answer in %s', async (locale) => {
    // A mutation that hardcoded `locale: 'en'` here passed the whole suite:
    // the stream-level test covers `streamDiagnosis`, and nothing covered the
    // component that supplies it. The failure would be invisible in English
    // and total in Arabic — the answer arrives, it is well-formed, and it is
    // in the wrong language, which is exactly the case the backend refuses to
    // paper over with a server-side default.
    const stream = controllableStream();
    // The request is captured rather than read back out of the mock's call
    // tuple, which `vi.fn` types as empty for a zero-argument generator.
    let sent: StreamOptions | null = null;
    const streamImpl = (options: StreamOptions) => {
      sent = options;
      return stream.generator();
    };
    renderApp(<Chat token="t" streamImpl={streamImpl} />, { locale });

    // By id rather than by label text: the label is translated, so matching
    // on English wording would only ever exercise the English case.
    const input = document.getElementById('chat-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'Tripping' } });
    fireEvent.submit(input.closest('form') as HTMLFormElement);

    await waitFor(() => {
      expect(sent).not.toBeNull();
    });
    expect((sent as unknown as StreamOptions).request.locale).toBe(locale);
  });
});
