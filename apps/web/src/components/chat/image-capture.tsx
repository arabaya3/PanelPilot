'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { TechnicalToken } from '@/components/technical-token';
import {
  CaptureFailure,
  prepareImage,
  type CaptureError,
  type Prepared,
} from '@/lib/image-capture';
import {
  needsConfirmation,
  trusted,
  uploadImage,
  type FaultRecognitionResult,
  type UploadFailure,
  type UploadOutcome,
} from '@/lib/recognition';

/**
 * Capturing a photo of a fault display.
 *
 * An engineer describing a fault code from memory mistypes it; photographing
 * the display removes that error source. Two entry points feed one handler:
 * `capture="environment"` for the phone camera, and a drop zone for the desk.
 *
 * Every failure state here is explicit rather than a spinner. The spec is
 * specific about that, and the reason is that all of these look identical
 * while they are silent: a slow upload on a factory connection, a camera the
 * browser refused, and a request that will never return.
 */

type Stage =
  | { kind: 'idle' }
  | { kind: 'preparing' }
  | { kind: 'ready'; prepared: Prepared }
  | { kind: 'uploading'; prepared: Prepared; slow: boolean }
  | { kind: 'confirm'; prepared: Prepared; result: FaultRecognitionResult }
  | { kind: 'stored'; prepared: Prepared }
  | { kind: 'error'; message: string };

export function ImageCapture({
  token,
  onConfirm,
  uploadImpl = uploadImage,
}: {
  token: string;
  /** Hands the confirmed text to the composer for a one-tap send. */
  onConfirm: (text: string) => void;
  uploadImpl?: typeof uploadImage;
}) {
  const t = useTranslations('capture');
  const [stage, setStage] = useState<Stage>({ kind: 'idle' });
  const [dragging, setDragging] = useState(false);
  const inputId = useId();
  const aborter = useRef<AbortController | null>(null);

  // The URL currently on screen, whichever stage is showing it.
  const shown =
    stage.kind === 'ready' || stage.kind === 'uploading' || stage.kind === 'stored'
      ? stage.prepared.previewUrl
      : stage.kind === 'confirm'
        ? stage.prepared.previewUrl
        : null;

  // Revoked when this URL stops being the one displayed, and on unmount.
  // Keyed on the URL rather than held in a ref, so replacing one photo with
  // another releases exactly the one that went away — never the one now on
  // screen, and never nothing at all.
  useEffect(() => {
    if (!shown) return;
    return () => {
      URL.revokeObjectURL(shown);
    };
  }, [shown]);

  // A stream left running for a component nobody is looking at still consumes
  // the factory-floor connection it was competing for.
  useEffect(() => {
    return () => {
      aborter.current?.abort();
    };
  }, []);

  function fail(reason: UploadFailure | CaptureError) {
    setStage({ kind: 'error', message: t(`error.${reason}`) });
  }

  async function accept(file: File) {
    // Anything already in flight is abandoned: its result would arrive holding
    // a `prepared` whose preview has since been replaced.
    aborter.current?.abort();
    setStage({ kind: 'preparing' });

    let prepared: Prepared;
    try {
      prepared = await prepareImage(file);
    } catch (error) {
      fail(error instanceof CaptureFailure ? error.reason : 'decode-failed');
      return;
    }
    setStage({ kind: 'ready', prepared });
  }

  async function send(prepared: Prepared) {
    const controller = new AbortController();
    aborter.current = controller;
    setStage({ kind: 'uploading', prepared, slow: false });

    const outcome: UploadOutcome = await uploadImpl({
      file: prepared.file,
      token,
      signal: controller.signal,
      // Says "this is taking a while" rather than leaving a spinner that an
      // engineer cannot distinguish from a hung request.
      onSlow: () => {
        setStage((current) =>
          current.kind === 'uploading' ? { ...current, slow: true } : current,
        );
      },
    });

    // A result for an upload that has been superseded or unmounted is
    // discarded rather than rendered: it would show a card built around a
    // preview that no longer exists.
    if (controller.signal.aborted) return;

    if (outcome.kind === 'failed') {
      fail(outcome.reason);
      return;
    }
    if (outcome.kind === 'stored') {
      // The image uploaded and nothing read it — the recogniser has no route
      // yet. Reported plainly, because the engineer can still describe the
      // fault themselves and a silent success would look like a bug.
      setStage({ kind: 'stored', prepared });
      return;
    }
    setStage({ kind: 'confirm', prepared, result: outcome.result });
  }

  if (stage.kind === 'error') {
    return (
      <div role="alert" data-testid="capture-error" className="p-3 text-sm text-text">
        <p>{stage.message}</p>
        <button
          type="button"
          onClick={() => {
            setStage({ kind: 'idle' });
          }}
          className="mt-2 rounded-md border border-border bg-surface px-3 py-1 text-sm"
        >
          {t('tryAnother')}
        </button>
      </div>
    );
  }

  if (stage.kind === 'preparing') {
    return (
      <p role="status" data-testid="capture-preparing" className="p-3 text-sm text-text-muted">
        {t('preparing')}
      </p>
    );
  }

  if (stage.kind === 'uploading') {
    return (
      <div className="p-3 text-sm text-text-muted">
        <img
          src={stage.prepared.previewUrl}
          alt={t('previewAlt')}
          className="mb-2 max-h-40 rounded-md"
        />
        <p role="status" data-testid="capture-uploading" data-slow={stage.slow ? 'true' : 'false'}>
          {stage.slow ? t('stillUploading') : t('uploading')}
        </p>
      </div>
    );
  }

  if (stage.kind === 'ready') {
    return (
      <div className="p-3" data-testid="capture-preview">
        <img
          src={stage.prepared.previewUrl}
          alt={t('previewAlt')}
          className="mb-2 max-h-40 rounded-md"
        />
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              void send(stage.prepared);
            }}
            className="rounded-md bg-accent px-3 py-1 text-sm text-accent-contrast"
          >
            {t('send')}
          </button>
          <button
            type="button"
            onClick={() => {
              setStage({ kind: 'idle' });
            }}
            className="rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
          >
            {t('discard')}
          </button>
        </div>
      </div>
    );
  }

  if (stage.kind === 'stored') {
    return (
      <div className="p-3 text-sm" data-testid="capture-stored">
        <p className="text-text">{t('storedNoReading')}</p>
        <button
          type="button"
          onClick={() => {
            setStage({ kind: 'idle' });
          }}
          className="mt-2 rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
        >
          {t('describeInstead')}
        </button>
      </div>
    );
  }

  if (stage.kind === 'confirm') {
    return (
      <Confirmation
        result={stage.result}
        previewUrl={stage.prepared.previewUrl}
        onSend={(text) => {
          setStage({ kind: 'idle' });
          onConfirm(text);
        }}
        onDiscard={() => {
          setStage({ kind: 'idle' });
        }}
      />
    );
  }

  return (
    <div
      data-testid="capture-dropzone"
      data-dragging={dragging ? 'true' : 'false'}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => {
        setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const file = event.dataTransfer.files[0];
        if (file) void accept(file);
      }}
      className={
        dragging
          ? 'rounded-md border-2 border-dashed border-accent p-3 text-sm text-text'
          : 'rounded-md border-2 border-dashed border-border p-3 text-sm text-text-muted'
      }
    >
      <label htmlFor={inputId} className="cursor-pointer underline">
        {t('choose')}
      </label>
      <input
        id={inputId}
        type="file"
        // `capture="environment"` asks a phone for the rear camera. Desktop
        // browsers ignore it and show a file picker, so one input serves both
        // paths rather than the UI having to guess which it is on.
        accept="image/*"
        capture="environment"
        data-testid="capture-input"
        className="sr-only"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void accept(file);
          // Cleared so choosing the same file twice fires a change event.
          event.target.value = '';
        }}
      />
      <span className="ms-2">{t('orDrop')}</span>
    </div>
  );
}

/**
 * What was read, for the engineer to confirm before anything is diagnosed.
 *
 * The acceptance criterion: the code and equipment are shown for confirmation
 * *before* the diagnosis proceeds. A high-confidence read pre-fills the
 * message for one tap; anything else asks rather than assumes, because a
 * wrong fault code sends the engineer to the wrong procedure and one extra
 * tap is far cheaper than that.
 */
function Confirmation({
  result,
  previewUrl,
  onSend,
  onDiscard,
}: {
  result: FaultRecognitionResult;
  previewUrl: string;
  onSend: (text: string) => void;
  onDiscard: () => void;
}) {
  const t = useTranslations('capture');

  // Not a fault display at all, or unreadable. AI-008 supplies a note saying
  // what it saw so the engineer is told what to do instead of just "no".
  if (result.verdict !== 'fault_display') {
    return (
      <div className="p-3 text-sm" data-testid="capture-rejected" data-verdict={result.verdict}>
        <img src={previewUrl} alt={t('previewAlt')} className="mb-2 max-h-40 rounded-md" />
        <p className="text-text">{result.note ?? t(`verdict.${result.verdict}`)}</p>
        <button
          type="button"
          onClick={onDiscard}
          className="mt-2 rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
        >
          {t('tryAnother')}
        </button>
      </div>
    );
  }

  const code = result.fault_code.value;
  const confident = !needsConfirmation(result);
  const parts = [
    trusted(result, 'brand') ? result.brand.value : null,
    trusted(result, 'model') ? result.model.value : null,
  ].filter((part): part is string => part !== null);

  const message = code
    ? t('message', { code, equipment: parts.join(' ') }).trim()
    : t('messageNoCode');

  return (
    <div
      className="p-3 text-sm"
      data-testid="capture-confirm"
      data-confident={confident ? 'true' : 'false'}
    >
      <img src={previewUrl} alt={t('previewAlt')} className="mb-2 max-h-40 rounded-md" />
      <p className="text-text">{confident ? t('readAs') : t('pleaseConfirm')}</p>
      <p className="mt-1">
        {code ? <TechnicalToken className="text-base font-semibold">{code}</TechnicalToken> : null}
        {parts.map((part) => (
          <TechnicalToken key={part} className="ms-2 text-text-muted">
            {part}
          </TechnicalToken>
        ))}
      </p>
      <div className="mt-2 flex gap-2">
        <button
          type="button"
          onClick={() => {
            onSend(message);
          }}
          className="rounded-md bg-accent px-3 py-1 text-sm text-accent-contrast"
        >
          {confident ? t('confirmSend') : t('confirmAnyway')}
        </button>
        <button
          type="button"
          onClick={onDiscard}
          className="rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
        >
          {t('tryAnother')}
        </button>
      </div>
    </div>
  );
}
