import { fireEvent, screen, waitFor } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { Chat } from '@/components/chat';
import type { StreamEvent, StreamOptions } from '@/lib/diagnosis-stream';
import { ImageCapture } from '@/components/chat/image-capture';
import { CaptureFailure, prepareImage, targetSize, MAX_EDGE_PX } from '@/lib/image-capture';
import {
  MIN_FIELD_CONFIDENCE,
  needsConfirmation,
  trusted,
  uploadImage,
  type FaultRecognitionResult,
  type UploadOutcome,
} from '@/lib/recognition';

import { renderApp } from './helpers';

/**
 * Tests for fault-code image capture.
 *
 * The spec asks specifically for a unit test on the compress-before-upload
 * step, and the reason is that its failure is quiet: an implementation that
 * silently returns the original still works, just slowly, and only on a fast
 * connection does anyone notice nothing happened.
 *
 * The rest drive the real controls — choose a file, drop a file, press send,
 * confirm — because that is where this lane's defects have hidden.
 *
 * The recogniser itself has no route yet (see `lib/recognition.ts`), so the
 * confirm path is exercised against the shape AI-008 produces rather than
 * against a live endpoint. The `stored` path — upload succeeds, nothing reads
 * it — is what actually happens today, and is tested as such.
 */

// jsdom implements neither canvas nor createImageBitmap. These stubs make the
// compression step observable: `toBlob` reports the size the canvas was asked
// to produce, so a step that skipped the resize is visible as a wrong size.
let drawnTo: { width: number; height: number } | null = null;
let encodeReturnsNull = false;

beforeEach(() => {
  drawnTo = null;
  encodeReturnsNull = false;

  vi.stubGlobal(
    'createImageBitmap',
    vi.fn((file: File) =>
      Promise.resolve({
        width: Number(file.name.split('x')[0] ?? 4000),
        height: Number(file.name.split('x')[1]?.split('.')[0] ?? 3000),
        close: () => undefined,
      }),
    ),
  );

  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    drawImage: (_source: unknown, _x: number, _y: number, width: number, height: number) => {
      drawnTo = { width, height };
    },
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;

  HTMLCanvasElement.prototype.toBlob = function toBlob(callback: BlobCallback) {
    if (encodeReturnsNull) {
      callback(null);
      return;
    }
    // Size scaled from the drawn dimensions, so a skipped resize produces a
    // visibly larger blob rather than an identical one.
    const area = (drawnTo?.width ?? 0) * (drawnTo?.height ?? 0);
    callback(
      new Blob([new Uint8Array(Math.max(1, Math.round(area / 40)))], { type: 'image/jpeg' }),
    );
  };

  // Distinct per call: with one constant URL, 'revoked the old one but not
  // the one being shown' is not expressible, and the whole lifecycle went
  // untested behind stubs nothing asserted on.
  let nextUrl = 0;
  URL.createObjectURL = vi.fn(() => `blob:${String(++nextUrl)}`);
  URL.revokeObjectURL = vi.fn();
});

/**
 * The `revokeObjectURL` spy, for asserting on.
 *
 * `URL.revokeObjectURL` is a static that never reads `this`, and here it is a
 * `vi.fn()` anyway — so the unbound-method rule is warning about a hazard that
 * cannot occur, and reaching it through a wrapper would only hide the fact
 * that this is the global under test.
 */
function revoked(): ReturnType<typeof vi.fn> {
  // eslint-disable-next-line @typescript-eslint/unbound-method
  return URL.revokeObjectURL as unknown as ReturnType<typeof vi.fn>;
}

/** The most recent object URL handed out by the stub. */
function lastObjectUrl(): string {
  const results = (URL.createObjectURL as unknown as ReturnType<typeof vi.fn>).mock.results;
  const last = results[results.length - 1];
  if (typeof last?.value !== 'string') throw new Error('no object URL was created');
  return last.value;
}

/** A file whose name encodes the dimensions the stub decoder reports. */
function photo(width: number, height: number, bytes = 4_000_000): File {
  return new File([new Uint8Array(bytes)], `${String(width)}x${String(height)}.jpg`, {
    type: 'image/jpeg',
  });
}

// --- the compression step, on its own ---------------------------------------

describe('targetSize', () => {
  it('scales the longest edge down to the ceiling', () => {
    expect(targetSize(4000, 3000)).toEqual({ width: 1600, height: 1200 });
    expect(targetSize(3000, 4000)).toEqual({ width: 1200, height: 1600 });
  });

  it('preserves the aspect ratio', () => {
    const { width, height } = targetSize(4032, 3024);
    expect(width / height).toBeCloseTo(4032 / 3024, 3);
  });

  it('never enlarges an image that is already small', () => {
    // Scaling up adds bytes and no detail, which is the opposite of the point.
    expect(targetSize(800, 600)).toEqual({ width: 800, height: 600 });
  });

  it('keeps a very long thin image at least one pixel wide', () => {
    // A zero-width canvas throws, and an 8000x2 panorama is not impossible.
    const { width, height } = targetSize(8000, 2);
    expect(width).toBe(MAX_EDGE_PX);
    expect(height).toBeGreaterThanOrEqual(1);
  });
});

describe('prepareImage', () => {
  it('actually downscales before upload', async () => {
    // The assertion that matters: the canvas was drawn at the reduced size,
    // not at the original. A no-op implementation passes a size check on the
    // output file but fails this.
    const prepared = await prepareImage(photo(4000, 3000));
    expect(drawnTo).toEqual({ width: 1600, height: 1200 });
    expect(prepared.compressedBytes).toBeLessThan(prepared.originalBytes);
  });

  it('reports both sizes so the UI can be honest', async () => {
    const prepared = await prepareImage(photo(4000, 3000));
    expect(prepared.originalBytes).toBe(4_000_000);
    expect(prepared.compressedBytes).toBeGreaterThan(0);
  });

  it('refuses a file that is not an image', async () => {
    const notAnImage = new File(['#!/bin/sh'], 'script.sh', { type: 'text/x-shellscript' });
    await expect(prepareImage(notAnImage)).rejects.toBeInstanceOf(CaptureFailure);
  });

  it('reports an encode failure rather than uploading nothing', async () => {
    encodeReturnsNull = true;
    await expect(prepareImage(photo(4000, 3000))).rejects.toMatchObject({
      reason: 'encode-failed',
    });
  });

  it('rejects a photo still over the limit rather than spending the round trip', async () => {
    // The round trip on a factory connection is exactly what this avoids.
    //
    // Reached by raising the ceiling rather than by using a huge source: a
    // large photo downscales to 1600px like any other, so the only way past
    // the limit is an image that stays large after compression.
    // The source is itself over the ceiling, so `prepareImage` keeps the
    // original (re-encoding it would only make it larger) and the guard has
    // to catch it on the way out.
    await expect(prepareImage(photo(20000, 20000, 12_000_000), 20000)).rejects.toMatchObject({
      reason: 'too-large-after-compression',
    });
  });
});

// --- the confidence gate ------------------------------------------------------

function recognition(overrides: Partial<FaultRecognitionResult> = {}): FaultRecognitionResult {
  return {
    verdict: 'fault_display',
    fault_code: { value: 'F0001', confidence: 0.95 },
    brand: { value: 'ABB', confidence: 0.9 },
    model: { value: 'ACS880', confidence: 0.9 },
    note: null,
    ...overrides,
  };
}

describe('the confidence gate', () => {
  it('matches the backend threshold', () => {
    // Duplicated across a language boundary, so it is asserted against the
    // Python constant rather than trusted to stay in step.
    const source = readFileSync(
      resolve(process.cwd(), '../../apps/api/app/ai/recognition.py'),
      'utf8',
    );
    const match = /MIN_FIELD_CONFIDENCE = ([\d.]+)/.exec(source);
    expect(match?.[1]).toBeDefined();
    expect(Number(match?.[1])).toBe(MIN_FIELD_CONFIDENCE);
  });

  it('checks the verdict before reading any field', () => {
    // A model that decided the photo is a wiring diagram and reported a code
    // anyway has invented the code. The backend schema refuses that
    // combination; this refuses to trust it even if it arrives.
    const invented = recognition({ verdict: 'not_a_fault_display' });
    expect(trusted(invented, 'fault_code')).toBe(false);
    expect(needsConfirmation(invented)).toBe(true);
  });

  it('asks for confirmation below the threshold', () => {
    const unsure = recognition({ fault_code: { value: 'F0001', confidence: 0.6 } });
    expect(needsConfirmation(unsure)).toBe(true);
  });

  it('never trusts a field with no value, whatever the number beside it', () => {
    const empty = recognition({ brand: { value: null, confidence: 0.99 } });
    expect(trusted(empty, 'brand')).toBe(false);
  });

  it('pre-fills only on a confident read of a readable display', () => {
    expect(needsConfirmation(recognition())).toBe(false);
  });
});

// --- the upload client --------------------------------------------------------

describe('uploadImage', () => {
  function respond(status: number, payload: unknown) {
    return vi.fn().mockResolvedValue({
      ok: status >= 200 && status < 300,
      status,
      json: () => Promise.resolve(payload),
    });
  }

  it('reports a stored image when nothing read it', async () => {
    // What actually happens today: the endpoint stores the photo and returns
    // an id, because AI-008 is wired to no route.
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: respond(200, { image_id: 'img-1' }),
    });
    expect(outcome).toEqual({ kind: 'stored', imageId: 'img-1' });
  });

  it('reads a recognition result when one is present', async () => {
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: respond(200, {
        image_id: 'img-1',
        recognition: recognition(),
      }),
    });
    expect(outcome.kind).toBe('recognised');
    expect(
      (outcome as Extract<UploadOutcome, { kind: 'recognised' }>).result.fault_code.value,
    ).toBe('F0001');
  });

  it('ignores a recognition payload with no verdict', async () => {
    // The verdict is what makes reading the fields safe. A payload without
    // one must not be trusted just because it carries a code.
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: respond(200, {
        image_id: 'img-1',
        recognition: { fault_code: { value: 'F0001', confidence: 0.99 } },
      }),
    });
    expect(outcome.kind).toBe('stored');
  });

  it('treats a non-numeric confidence as none rather than as high', async () => {
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: respond(200, {
        image_id: 'img-1',
        recognition: {
          verdict: 'fault_display',
          fault_code: { value: 'F0001', confidence: 'high' },
        },
      }),
    });
    expect(outcome.kind).toBe('recognised');
    const result = (outcome as Extract<UploadOutcome, { kind: 'recognised' }>).result;
    expect(result.fault_code.confidence).toBe(0);
    expect(needsConfirmation(result)).toBe(true);
  });

  it.each([
    [413, 'too-large'],
    [404, 'unavailable'],
    [500, 'rejected'],
  ])('reports %s as %s', async (status, reason) => {
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: respond(status, {}),
    });
    expect(outcome).toEqual({ kind: 'failed', reason });
  });

  it('reports a network failure rather than hanging', async () => {
    const outcome = await uploadImage({
      file: photo(100, 100),
      token: 't',
      fetchImpl: vi.fn().mockRejectedValue(new Error('offline')),
    });
    expect(outcome).toEqual({ kind: 'failed', reason: 'network' });
  });

  it('announces a slow upload rather than leaving a silent spinner', async () => {
    vi.useFakeTimers();
    const onSlow = vi.fn();
    const promise = uploadImage({
      file: photo(100, 100),
      token: 't',
      slowAfterMs: 100,
      onSlow,
      fetchImpl: (() => new Promise(() => undefined)) as unknown as typeof fetch,
    });
    await vi.advanceTimersByTimeAsync(150);
    expect(onSlow).toHaveBeenCalled();
    vi.useRealTimers();
    void promise;
  });

  it('sends the file and the bearer token', async () => {
    const fetchImpl = respond(200, { image_id: 'img-1' });
    await uploadImage({
      file: photo(100, 100),
      token: 'secret',
      fetchImpl: fetchImpl,
    });
    const init = fetchImpl.mock.calls[0]?.[1] as {
      headers: Record<string, string>;
      body: FormData;
    };
    expect(init.headers.Authorization).toBe('Bearer secret');
    expect(init.body.get('file')).toBeInstanceOf(File);
  });
});

// --- through the real controls -------------------------------------------------

function chooseFile(file: File) {
  const input = screen.getByTestId('capture-input');
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  fireEvent.change(input);
}

describe('capturing a photo by hand', () => {
  function uploadReturning(outcome: UploadOutcome) {
    return vi.fn().mockResolvedValue(outcome) as unknown as typeof uploadImage;
  }

  it('previews a chosen photo before anything is sent', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'stored', imageId: 'i' })}
      />,
    );
    chooseFile(photo(4000, 3000));

    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    // Nothing has been uploaded — the engineer decides.
    expect(screen.getByRole('img')).toBeTruthy();
  });

  it('accepts a dropped photo through the same path', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'stored', imageId: 'i' })}
      />,
    );
    const zone = screen.getByTestId('capture-dropzone');

    fireEvent.dragOver(zone);
    expect(zone.getAttribute('data-dragging')).toBe('true');

    fireEvent.drop(zone, { dataTransfer: { files: [photo(4000, 3000)] } });
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
  });

  it('shows a confident read for one-tap confirmation', async () => {
    const onConfirm = vi.fn();
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={onConfirm}
        uploadImpl={uploadReturning({ kind: 'recognised', imageId: 'i', result: recognition() })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-confirm')).toBeTruthy();
    });
    expect(screen.getByTestId('capture-confirm').getAttribute('data-confident')).toBe('true');
    expect(screen.getByText('F0001')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /confirm and send/i }));
    expect(onConfirm).toHaveBeenCalledWith(expect.stringContaining('F0001'));
  });

  it('asks rather than assumes below the threshold', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({
          kind: 'recognised',
          imageId: 'i',
          result: recognition({ fault_code: { value: 'F0001', confidence: 0.5 } }),
        })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-confirm')).toBeTruthy();
    });
    expect(screen.getByTestId('capture-confirm').getAttribute('data-confident')).toBe('false');
    expect(screen.getByText(/please confirm/i)).toBeTruthy();
  });

  it('surfaces the off-topic rejection with what the model saw', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({
          kind: 'recognised',
          imageId: 'i',
          result: recognition({
            verdict: 'not_a_fault_display',
            fault_code: { value: null, confidence: 0 },
            note: 'This looks like a wiring diagram.',
          }),
        })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-rejected')).toBeTruthy();
    });
    expect(screen.getByText(/wiring diagram/)).toBeTruthy();
    // And no code is offered to send.
    expect(screen.queryByTestId('capture-confirm')).toBeNull();
  });

  it('says plainly when the photo uploaded but nothing read it', async () => {
    // Today's real behaviour: no recogniser route exists. A silent success
    // would look like a bug, and the engineer can still describe the fault.
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'stored', imageId: 'i' })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-stored')).toBeTruthy();
    });
  });

  it('reports an upload failure as an alert, not a spinner', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'failed', reason: 'network' })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-error')).toBeTruthy();
    });
    expect(screen.getByRole('alert')).toBeTruthy();
  });

  it('reports a file that is not an image without uploading it', async () => {
    const uploadImpl = vi.fn();
    renderApp(<ImageCapture token="t" onConfirm={vi.fn()} uploadImpl={uploadImpl} />);
    chooseFile(new File(['#!/bin/sh'], 'script.sh', { type: 'text/x-shellscript' }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-error')).toBeTruthy();
    });
    expect(uploadImpl).not.toHaveBeenCalled();
  });

  it('offers the rear camera on a phone', () => {
    // `capture="environment"` is what makes the mobile path a camera rather
    // than a file browser; desktop ignores it, so one input serves both.
    renderApp(<ImageCapture token="t" onConfirm={vi.fn()} />);
    const input = screen.getByTestId('capture-input');
    expect(input.getAttribute('capture')).toBe('environment');
    expect(input.getAttribute('accept')).toBe('image/*');
  });
});

// --- and into the chat ---------------------------------------------------------

describe('a confirmed photo reaches the conversation', () => {
  it('asks the backend about the code that was read off the display', async () => {
    // The acceptance criterion, end to end and through the real controls:
    // choose a photo, send it, confirm what was read, and the diagnosis
    // proceeds from the recognised code — with the engineer never typing it.
    //
    // Asserted on what reaches the wire rather than on what the card shows,
    // because a confirm button that renders correctly and hands nothing to
    // the composer is exactly the kind of gap that has bitten this lane.
    const sent: StreamOptions[] = [];
    const streamImpl = (options: StreamOptions) => {
      sent.push(options);
      // A stream that reports one stage and stops. The turn's outcome is not
      // what this test is about — what reaches the request is.
      return (async function* (): AsyncGenerator<StreamEvent> {
        await Promise.resolve();
        yield { kind: 'stage', stage: 'retrieving' };
      })();
    };

    renderApp(
      <Chat
        token="t"
        streamImpl={streamImpl}
        uploadImpl={vi.fn().mockResolvedValue({
          kind: 'recognised',
          imageId: 'i',
          result: recognition(),
        })}
      />,
    );

    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));

    await waitFor(() => {
      expect(screen.getByTestId('capture-confirm')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /confirm and send/i }));

    await waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    expect(sent[0]?.request.symptom).toContain('F0001');
    // And the equipment it read, so the assistant is not asked about a code
    // with no unit attached.
    expect(sent[0]?.request.symptom).toContain('ACS880');
    // The capture panel steps back once the question is asked.
    expect(screen.queryByTestId('capture-confirm')).toBeNull();
  });
});

// --- the preview's lifetime ---------------------------------------------------

describe('object URLs', () => {
  function uploadReturning(outcome: UploadOutcome) {
    return vi.fn().mockResolvedValue(outcome) as unknown as typeof uploadImage;
  }

  it('releases the preview when the component goes away', async () => {
    // Every photo pins its decoded blob for the lifetime of the document
    // until this runs. An engineer working through a panel takes ten or
    // fifteen shots, and this is exactly the mid-range hardware where that
    // gets the tab killed. Deleting the cleanup passed all 218 tests.
    const { unmount } = renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'stored', imageId: 'i' })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });

    const created = lastObjectUrl();
    unmount();
    expect(revoked()).toHaveBeenCalledWith(created);
  });

  it('releases the old photo when a second one replaces it', async () => {
    renderApp(
      <ImageCapture
        token="t"
        onConfirm={vi.fn()}
        uploadImpl={uploadReturning({ kind: 'stored', imageId: 'i' })}
      />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    const first = lastObjectUrl();

    // Discard first: the capture input only exists in the idle stage, which
    // is also the path an engineer takes to retake a shot.
    fireEvent.click(screen.getByRole('button', { name: /discard/i }));
    chooseFile(photo(3000, 4000));
    await waitFor(() => {
      expect(lastObjectUrl()).not.toBe(first);
    });
    // The effect that releases the old URL runs after the new stage commits.
    await waitFor(() => {
      expect(revoked()).toHaveBeenCalledWith(first);
    });

    const second = lastObjectUrl();
    // The one on screen is not released.
    expect(revoked()).not.toHaveBeenCalledWith(second);
  });

  it('abandons an upload whose component has gone away', async () => {
    // The capture panel unmounts while a photo is in flight. Without an
    // abort the upload keeps consuming the factory-floor connection it was
    // competing for, and its result then lands on a dead component holding a
    // preview URL that has already been released.
    const release: { fn: ((outcome: UploadOutcome) => void) | null } = { fn: null };
    let seenSignal: AbortSignal | undefined;
    const uploadImpl = vi.fn((options: { signal?: AbortSignal }) => {
      seenSignal = options.signal;
      return new Promise<UploadOutcome>((resolve) => {
        release.fn = resolve;
      });
    }) as unknown as typeof uploadImage;

    const { unmount } = renderApp(
      <ImageCapture token="t" onConfirm={vi.fn()} uploadImpl={uploadImpl} />,
    );
    chooseFile(photo(4000, 3000));
    await waitFor(() => {
      expect(screen.getByTestId('capture-preview')).toBeTruthy();
    });
    fireEvent.click(screen.getByRole('button', { name: /send photo/i }));
    await waitFor(() => {
      expect(screen.getByTestId('capture-uploading')).toBeTruthy();
    });

    // The upload is given a signal to honour…
    expect(seenSignal).toBeInstanceOf(AbortSignal);
    expect(seenSignal?.aborted).toBe(false);

    unmount();

    // …which is aborted when the component goes, and the late result is
    // discarded rather than rendered into nothing.
    expect(seenSignal?.aborted).toBe(true);
    release.fn?.({ kind: 'recognised', imageId: 'i', result: recognition() });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByTestId('capture-confirm')).toBeNull();
  });
});

// --- orientation ---------------------------------------------------------------

describe('EXIF orientation', () => {
  it('asks the decoder to apply the stored rotation', async () => {
    // A phone stores a portrait photo as landscape pixels plus a rotation
    // flag. Drawing those pixels to a canvas discards the flag, so the upload
    // is sideways — and the `<img>` preview *does* apply EXIF, so the
    // engineer approves an upright image and sideways bytes leave the device.
    // The recogniser then correctly reports `unreadable` on a good photo.
    await prepareImage(photo(4000, 3000));

    const decoder = createImageBitmap as unknown as ReturnType<typeof vi.fn>;
    expect(decoder).toHaveBeenCalled();
    expect(decoder.mock.calls[0]?.[1]).toMatchObject({ imageOrientation: 'from-image' });
  });

  it('fails honestly on a browser that cannot orient the photo', async () => {
    // The old fallback read pre-orientation dimensions and drew unrotated
    // pixels, producing exactly the sideways upload above. An error beats a
    // photo the recogniser will silently reject.
    // Deleted rather than set to undefined: the guard tests `typeof`, and a
    // global explicitly set to undefined is still a declared binding.
    const original = globalThis.createImageBitmap;
    // @ts-expect-error — removing a global for the duration of one test.
    delete globalThis.createImageBitmap;
    await expect(prepareImage(photo(4000, 3000))).rejects.toMatchObject({
      reason: 'decode-failed',
    });
    globalThis.createImageBitmap = original;
  });

  it('releases the decoded bitmap', async () => {
    // Tens of megabytes outside the JS heap per 12MP photo, and the GC is
    // driven by heap size — which the small wrapper barely moves.
    const close = vi.fn();
    vi.stubGlobal('createImageBitmap', () => Promise.resolve({ width: 4000, height: 3000, close }));
    await prepareImage(photo(4000, 3000));
    expect(close).toHaveBeenCalled();
  });
});
