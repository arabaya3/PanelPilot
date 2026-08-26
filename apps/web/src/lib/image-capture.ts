/**
 * Preparing a photo of an equipment display for upload.
 *
 * A field engineer photographs a drive's display so the fault code is read off
 * the screen rather than typed from memory — mistyping is the error source
 * this removes. The photo comes straight off a phone camera, so it is large,
 * and the upload happens on whatever connection a factory floor has.
 */

/**
 * The longest edge, in pixels, that survives compression.
 *
 * A fault code is a handful of large glyphs on a backlit LCD. 1600px is far
 * more than enough to read one, and a 12-megapixel original is several
 * megabytes of detail that exists only to slow the upload down on a
 * connection that is already the weak link.
 */
export const MAX_EDGE_PX = 1600;

/** Matches the backend's own ceiling in `app/domain/images.py`. */
export const MAX_UPLOAD_BYTES = 8 * 1024 * 1024;

/** JPEG, because a photograph is not a diagram and PNG would be far larger. */
const OUTPUT_TYPE = 'image/jpeg';
const OUTPUT_QUALITY = 0.82;

export interface Prepared {
  /** The compressed file, ready to upload. */
  file: File;
  /** An object URL for the preview. The caller must revoke it. */
  previewUrl: string;
  /** Bytes before and after, so the UI can be honest about what it did. */
  originalBytes: number;
  compressedBytes: number;
}

/** How the image was obtained, for the messages the two paths need. */
export type CaptureError =
  'not-an-image' | 'too-large-after-compression' | 'decode-failed' | 'encode-failed';

export class CaptureFailure extends Error {
  constructor(readonly reason: CaptureError) {
    super(reason);
    this.name = 'CaptureFailure';
  }
}

/**
 * Work out the target size for one image.
 *
 * Exported because it is the part worth testing directly: the aspect ratio
 * must survive, and an image already smaller than the ceiling must not be
 * scaled *up* — enlarging a small photo would add bytes and no detail, which
 * is the opposite of the point.
 */
export function targetSize(
  width: number,
  height: number,
  maxEdge: number = MAX_EDGE_PX,
): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= maxEdge) return { width, height };
  const scale = maxEdge / longest;
  // Rounded, and floored at 1: a very long thin image could otherwise scale
  // its short edge to zero, and a zero-width canvas throws.
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/**
 * Decode a file into something drawable, respecting EXIF orientation.
 *
 * `imageOrientation: 'from-image'` is not optional here, and getting it wrong
 * defeats the whole feature. A phone almost always stores a portrait photo as
 * landscape pixels plus an EXIF rotation flag; drawing those pixels to a
 * canvas discards the flag, so the upload is sideways. The vision model then
 * has to read seven-segment glyphs rotated ninety degrees, fails, and
 * correctly reports `unreadable` — on a photo that was perfectly good.
 *
 * The failure is silent in the worst way: an `<img>` *does* apply EXIF, so the
 * preview the engineer approves looks upright while the bytes leaving the
 * device are not. The preview would be lying about what was sent.
 *
 * The browser default has been `'from-image'` for some time, but it was
 * `'none'` historically and iOS Safari — the likeliest camera here — has not
 * been consistent. Stating it costs nothing.
 */
async function decode(
  file: File,
): Promise<{ width: number; height: number; source: CanvasImageSource }> {
  if (typeof createImageBitmap !== 'function') {
    // The old fallback read `naturalWidth`/`naturalHeight`, which are the
    // *pre*-orientation dimensions, and drew unrotated pixels — producing
    // exactly the sideways upload described above. Rather than reimplement an
    // EXIF parser, this reports a decode failure: a browser without
    // `createImageBitmap` is old enough that an honest error beats a photo the
    // recogniser will silently reject.
    throw new CaptureFailure('decode-failed');
  }

  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    return { width: bitmap.width, height: bitmap.height, source: bitmap };
  } catch {
    throw new CaptureFailure('decode-failed');
  }
}

/**
 * Downscale and compress a photo before it is uploaded.
 *
 * The spec asks for a unit test on this step specifically, and the reason is
 * that its failure mode is quiet: an implementation that silently returns the
 * original still works, just slowly, and only on a fast connection does
 * anybody notice nothing happened.
 */
export async function prepareImage(file: File, maxEdge: number = MAX_EDGE_PX): Promise<Prepared> {
  if (!file.type.startsWith('image/')) {
    throw new CaptureFailure('not-an-image');
  }

  const { width, height, source } = await decode(file);
  const target = targetSize(width, height, maxEdge);

  let blob: Blob | null;
  try {
    const canvas = document.createElement('canvas');
    canvas.width = target.width;
    canvas.height = target.height;
    const context = canvas.getContext('2d');
    if (!context) throw new CaptureFailure('encode-failed');
    context.drawImage(source, 0, 0, target.width, target.height);

    blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, OUTPUT_TYPE, OUTPUT_QUALITY);
    });
  } finally {
    // An `ImageBitmap` for a 12MP photo holds tens of megabytes outside the JS
    // heap, and the GC is driven by heap size — which the small wrapper barely
    // moves. An engineer retaking three or four shots on a mid-range phone can
    // otherwise accumulate enough to have the tab killed, which looks like the
    // app vanishing mid-job.
    if ('close' in source && typeof source.close === 'function') source.close();
  }
  if (!blob) throw new CaptureFailure('encode-failed');

  // Compression is not guaranteed to shrink anything — an already-small JPEG
  // re-encoded can grow. Keeping whichever is smaller means this step can
  // never make the upload worse.
  const useOriginal = blob.size >= file.size && file.type === OUTPUT_TYPE;
  const chosen = useOriginal
    ? file
    : new File([blob], file.name.replace(/\.[^.]+$/, '') + '.jpg', { type: OUTPUT_TYPE });

  if (chosen.size > MAX_UPLOAD_BYTES) {
    // Reported rather than uploaded and rejected: the round trip on a factory
    // connection is exactly what this path exists to avoid spending.
    throw new CaptureFailure('too-large-after-compression');
  }

  return {
    file: chosen,
    previewUrl: URL.createObjectURL(chosen),
    originalBytes: file.size,
    compressedBytes: chosen.size,
  };
}
