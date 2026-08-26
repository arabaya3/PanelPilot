/**
 * Uploading a display photo and reading what was recognised.
 *
 * **The recognition half is not reachable yet.** `POST /api/v1/images` stores
 * the image and returns `{image_id}` and nothing else. AI-008's recogniser is
 * complete — `app/ai/recognition.py`, with a verdict, per-field confidence and
 * an off-topic rejection path — but it is wired to no route, so there is
 * nothing to call. The task's `{recognizedCode, brand, model, confidence}` does
 * not exist over the wire today.
 *
 * This is written against the shape AI-008 actually produces rather than the
 * task's paraphrase of it, so wiring a route is the only thing left to do.
 * `recognise` posts to the endpoint the route will occupy and reports
 * `unavailable` when it is not there, which is what happens now — a state the
 * UI shows honestly rather than a spinner that never resolves.
 */

/** Mirrors `DisplayVerdict` in `app/models/schemas/recognition.py`. */
export type DisplayVerdict = 'fault_display' | 'not_a_fault_display' | 'unreadable';

/**
 * One field the model claims to have read.
 *
 * `confidence` is meaningless without `value`; the backend rejects that
 * combination outright, and `trusted` mirrors its `trusted_at`.
 */
export interface RecognisedField {
  value: string | null;
  confidence: number;
}

/** Mirrors `FaultRecognitionResult`. */
export interface FaultRecognitionResult {
  verdict: DisplayVerdict;
  fault_code: RecognisedField;
  brand: RecognisedField;
  model: RecognisedField;
  note: string | null;
}

/**
 * The confidence floor, matching `MIN_FIELD_CONFIDENCE` in AI-008.
 *
 * Duplicated rather than imported because it lives in Python. It is asserted
 * against the backend constant in the tests, so the two cannot drift silently.
 */
export const MIN_FIELD_CONFIDENCE = 0.8;

/** What the upload step produced. */
export type UploadOutcome =
  | { kind: 'recognised'; imageId: string; result: FaultRecognitionResult }
  | { kind: 'stored'; imageId: string }
  | { kind: 'failed'; reason: UploadFailure };

export type UploadFailure =
  'too-large' | 'rejected' | 'network' | 'unavailable' | 'timeout' | 'aborted';

/**
 * A field may be used without asking the engineer to confirm it.
 *
 * The verdict is checked first, deliberately. A model that decided the photo
 * is a wiring diagram and reported a code anyway has invented the code, and a
 * caller reading fields before the verdict would use it — which is why the
 * backend schema refuses that combination outright.
 */
export function trusted(
  result: FaultRecognitionResult,
  field: keyof FaultRecognitionResult,
): boolean {
  if (result.verdict !== 'fault_display') return false;
  const value = result[field];
  if (typeof value !== 'object' || value === null) return false;
  const recognised: RecognisedField = value;
  return recognised.value !== null && recognised.confidence >= MIN_FIELD_CONFIDENCE;
}

/**
 * Should the engineer be asked to confirm before a diagnosis is run?
 *
 * High confidence on a readable display pre-fills the message for a one-tap
 * send; anything else surfaces the confirmation prompt instead. The asymmetry
 * is deliberate: a wrong fault code sends the engineer to the wrong procedure,
 * and one extra tap is a much smaller cost than that.
 */
export function needsConfirmation(result: FaultRecognitionResult): boolean {
  return !trusted(result, 'fault_code');
}

export interface UploadOptions {
  file: File;
  token: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  endpoint?: string;
  /**
   * How long before the UI stops claiming progress.
   *
   * The spec asks for explicit slow-upload messaging rather than a silent
   * spinner: on a factory-floor connection a large upload can legitimately
   * take a while, and an engineer cannot tell that from a hung request.
   */
  slowAfterMs?: number;
  onSlow?: () => void;
}

/** Upload one image and, when a recogniser exists, read what it saw. */
export async function uploadImage(options: UploadOptions): Promise<UploadOutcome> {
  const {
    file,
    token,
    signal,
    fetchImpl = fetch,
    endpoint = '/api/v1/images',
    slowAfterMs = 6000,
    onSlow,
  } = options;

  const body = new FormData();
  body.append('file', file);

  const slowTimer = onSlow ? setTimeout(onSlow, slowAfterMs) : undefined;

  try {
    const response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
      body,
      ...(signal ? { signal } : {}),
    });

    if (response.status === 413) return { kind: 'failed', reason: 'too-large' };
    if (response.status === 404) return { kind: 'failed', reason: 'unavailable' };
    if (!response.ok) return { kind: 'failed', reason: 'rejected' };

    const payload: unknown = await response.json();
    const imageId = readImageId(payload);
    if (imageId === null) return { kind: 'failed', reason: 'rejected' };

    // The recogniser's output, if the endpoint has grown one. Absent today,
    // and absence is reported as `stored` rather than as a failure — the
    // image did upload, and the engineer can still describe the fault.
    const result = readRecognition(payload);
    return result ? { kind: 'recognised', imageId, result } : { kind: 'stored', imageId };
  } catch {
    return { kind: 'failed', reason: signal?.aborted ? 'aborted' : 'network' };
  } finally {
    if (slowTimer !== undefined) clearTimeout(slowTimer);
  }
}

/** Read the image id, tolerating a payload that is not the shape we expect. */
function readImageId(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const id = (payload as { image_id?: unknown }).image_id;
  return typeof id === 'string' && id !== '' ? id : null;
}

/**
 * Read a recognition result, if one is present and well formed.
 *
 * Validated rather than cast. The whole point of the verdict is that fields
 * are not read until it has been checked, and a payload that arrived without
 * one would defeat that by being trusted anyway.
 */
function readRecognition(payload: unknown): FaultRecognitionResult | null {
  if (typeof payload !== 'object' || payload === null) return null;
  const raw = (payload as { recognition?: unknown }).recognition;
  if (typeof raw !== 'object' || raw === null) return null;

  const verdict = (raw as { verdict?: unknown }).verdict;
  if (
    verdict !== 'fault_display' &&
    verdict !== 'not_a_fault_display' &&
    verdict !== 'unreadable'
  ) {
    return null;
  }

  const note = (raw as { note?: unknown }).note;
  return {
    verdict,
    fault_code: readField(raw, 'fault_code'),
    brand: readField(raw, 'brand'),
    model: readField(raw, 'model'),
    note: typeof note === 'string' ? note : null,
  };
}

function readField(raw: object, name: string): RecognisedField {
  const field = (raw as Record<string, unknown>)[name];
  if (typeof field !== 'object' || field === null) return { value: null, confidence: 0 };
  const value = (field as { value?: unknown }).value;
  const confidence = (field as { confidence?: unknown }).confidence;
  return {
    value: typeof value === 'string' && value !== '' ? value : null,
    // A confidence that is not a number is treated as none. A field carrying
    // a number it cannot justify is exactly what the backend validator
    // rejects, and defaulting high here would reintroduce it.
    confidence: typeof confidence === 'number' && Number.isFinite(confidence) ? confidence : 0,
  };
}
