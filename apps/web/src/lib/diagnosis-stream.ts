import type { components } from '@panelpilot/shared-types';

type DiagnosticRequest = components['schemas']['DiagnosticRequest'];
type DiagnosticResponse = components['schemas']['DiagnosticResponse'];

/**
 * Reading BE-008's diagnostic stream.
 *
 * The tracker's sketch describes appending tokens to an in-progress assistant
 * message as they arrive. The backend deliberately does not work that way, and
 * the reason is written into `app/models/schemas/streaming.py`: no partial
 * answer is ever streamed, because "a refusal that arrives after three
 * paragraphs of a confident-sounding draft is not a refusal". What streams is
 * *progress* — `retrieving`, then `generated` or `refused`, then exactly one
 * terminal `result` carrying the complete response.
 *
 * That is the contract this implements. Rendering half-formed diagnostic text
 * to an engineer would defeat the guardrail the whole product is built around,
 * so the acceptance criterion's "streams smoothly" is satisfied by showing the
 * stage promptly, not by showing prose early.
 *
 * `fetch` + `ReadableStream` rather than `EventSource`: the endpoint is a POST
 * carrying a JSON body and requires an `Authorization` header, and
 * `EventSource` can do neither.
 */

/** The stages a turn passes through, mirroring the backend's `EventName`. */
export type StreamStage = 'retrieving' | 'generated' | 'refused' | 'result';

/** What the caller is told as the turn progresses. */
export type StreamEvent =
  | { kind: 'stage'; stage: Exclude<StreamStage, 'result'> }
  | { kind: 'result'; response: DiagnosticResponse }
  /**
   * The stream ended without a `result`.
   *
   * Its own event rather than a thrown error, because the distinction matters
   * to the person watching: a turn that was cut off is not a turn that failed
   * to start, and it must never be left looking like a completed answer. The
   * spec calls for "a resumable/clearly-failed state, not a silently
   * truncated answer".
   */
  | { kind: 'interrupted'; reason: InterruptionReason };

export type InterruptionReason = 'connection-lost' | 'aborted' | 'malformed-frame' | 'server-error';

/** A parsed SSE frame. */
interface Frame {
  event: string;
  data: string;
}

/**
 * Split a buffer into complete frames, returning the unconsumed remainder.
 *
 * A chunk boundary can fall anywhere — including inside a `data:` line — so a
 * frame is only complete once its terminating blank line has arrived. Parsing
 * eagerly would truncate a response at whatever byte the network happened to
 * split on, which is the silent-truncation failure this must not have.
 */
export function parseFrames(buffer: string): { frames: Frame[]; rest: string } {
  const frames: Frame[] = [];
  // Normalise CRLF: an intermediary may rewrite line endings, and a frame
  // terminator that went unrecognised would stall the stream forever.
  const normalised = buffer.replace(/\r\n/g, '\n');
  const parts = normalised.split('\n\n');
  // The final part is either an incomplete frame or an empty string; either
  // way it is not ready to be parsed.
  const rest = parts.pop() ?? '';

  for (const part of parts) {
    if (part.trim() === '') continue;
    let event = 'message';
    const dataLines: string[] = [];
    for (const line of part.split('\n')) {
      if (line.startsWith(':')) continue; // a comment, and a keep-alive
      if (line.startsWith('event:')) event = line.slice('event:'.length).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).trim());
    }
    if (dataLines.length > 0) frames.push({ event, data: dataLines.join('\n') });
  }

  return { frames, rest };
}

/** Everything the caller needs to open the stream. */
export interface StreamOptions {
  request: DiagnosticRequest;
  /** Bearer token for the protected route. */
  token: string;
  signal?: AbortSignal;
  /** Injectable so tests can drive a slow stream without a server. */
  fetchImpl?: typeof fetch;
  endpoint?: string;
}

/**
 * Run one diagnostic turn, yielding each stage as it arrives.
 *
 * Always terminates with exactly one of `result` or `interrupted`, so a caller
 * can never be left showing a spinner over a connection that has gone away.
 */
export async function* streamDiagnosis(options: StreamOptions): AsyncGenerator<StreamEvent> {
  const {
    request,
    token,
    signal,
    fetchImpl = fetch,
    endpoint = '/api/v1/diagnostics/stream',
  } = options;

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(request),
      // Spread rather than passed as `signal: undefined`:
      // `exactOptionalPropertyTypes` distinguishes an absent option from one
      // explicitly set to undefined, and `RequestInit.signal` accepts null,
      // not undefined.
      ...(signal ? { signal } : {}),
    });
  } catch {
    yield { kind: 'interrupted', reason: signal?.aborted ? 'aborted' : 'connection-lost' };
    return;
  }

  if (!response.ok || !response.body) {
    yield { kind: 'interrupted', reason: 'server-error' };
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      // `stream: true` so a multi-byte character split across chunks is held
      // rather than decoded into a replacement character — Arabic and Hebrew
      // text makes that the common case, not an edge case.
      buffer += decoder.decode(value, { stream: true });

      const { frames, rest } = parseFrames(buffer);
      buffer = rest;

      for (const frame of frames) {
        if (frame.event === 'result') {
          let parsed: unknown;
          try {
            parsed = JSON.parse(frame.data);
          } catch {
            yield { kind: 'interrupted', reason: 'malformed-frame' };
            return;
          }
          yield { kind: 'result', response: parsed as DiagnosticResponse };
          return;
        }
        if (
          frame.event === 'retrieving' ||
          frame.event === 'generated' ||
          frame.event === 'refused'
        ) {
          yield { kind: 'stage', stage: frame.event };
        }
        // Anything else is ignored rather than treated as an error: an
        // unfamiliar event name is a backend that has grown a stage this
        // client does not know about yet, which is not a failure.
      }
    }
  } catch {
    yield { kind: 'interrupted', reason: signal?.aborted ? 'aborted' : 'connection-lost' };
    return;
  } finally {
    reader.releaseLock();
  }

  // The body ended without a terminal `result`. This is the mid-stream
  // network interruption the spec singles out, and it must surface as a
  // failure rather than as a turn that quietly stopped.
  //
  // Reaching here *is* the condition: the `result` branch returns, so the
  // loop can only fall through when no result ever arrived.
  yield { kind: 'interrupted', reason: signal?.aborted ? 'aborted' : 'connection-lost' };
}
