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

/** Decode a file into something drawable. */
async function decode(
  file: File,
): Promise<{ width: number; height: number; source: CanvasImageSource }> {
  // `createImageBitmap` where available: it decodes off the main thread, which
  // matters on the mid-range phones this is used from.
  if (typeof createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file);
      return { width: bitmap.width, height: bitmap.height, source: bitmap };
    } catch {
      throw new CaptureFailure('decode-failed');
    }
  }

  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image();
      element.onload = () => {
        resolve(element);
      };
      element.onerror = () => {
        reject(new CaptureFailure('decode-failed'));
      };
      element.src = url;
    });
    return { width: image.naturalWidth, height: image.naturalHeight, source: image };
  } finally {
    URL.revokeObjectURL(url);
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

  const canvas = document.createElement('canvas');
  canvas.width = target.width;
  canvas.height = target.height;
  const context = canvas.getContext('2d');
  if (!context) throw new CaptureFailure('encode-failed');
  context.drawImage(source, 0, 0, target.width, target.height);

  const blob = await new Promise<Blob | null>((resolve) => {
    canvas.toBlob(resolve, OUTPUT_TYPE, OUTPUT_QUALITY);
  });
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
