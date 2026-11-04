'use client';

import { useId, useState } from 'react';
import { useTranslations } from 'next-intl';

import type {
  SafetyConcern,
  SafetyResolutionEligibility,
  SafetyResolutionKind,
} from '@/lib/design';

const REASON_MIN = 10;
const REASON_MAX = 500;

/**
 * M6d: the revision's safety concerns (detected life-safety or possibly-safety
 * mentions, and items unsupported as such), each unresolved one holding
 * approval. An eligible second member records one of two resolutions with a
 * reason: excluded from scope (real, not covered here) or a false positive
 * (not a life-safety requirement). The server checks every rule again.
 */
export function SafetyConcerns({
  concerns,
  eligibility,
  onResolve,
}: {
  concerns: readonly SafetyConcern[];
  eligibility: SafetyResolutionEligibility | null;
  /** Records a resolution; resolves to a message to show (a refusal), or null when done. */
  onResolve: (
    concern: SafetyConcern,
    resolution: SafetyResolutionKind,
    reason: string,
  ) => Promise<string | null>;
}) {
  const t = useTranslations('design.projects.safety');
  const id = useId();
  const [open, setOpen] = useState<{ key: string; resolution: SafetyResolutionKind } | null>(null);
  const [reason, setReason] = useState('');
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  if (concerns.length === 0) return null;
  const unresolved = concerns.filter((concern) => !concern.resolved).length;

  async function submit(concern: SafetyConcern, resolution: SafetyResolutionKind) {
    const text = reason.trim();
    if (working || text.length < REASON_MIN || text.length > REASON_MAX) return;
    setWorking(true);
    setMessage(null);
    const refused = await onResolve(concern, resolution, text);
    setWorking(false);
    if (refused === null) {
      setOpen(null);
      setReason('');
      setMessage(t('done'));
    } else setMessage(refused);
  }

  function action(concern: SafetyConcern) {
    const key = `${concern.kind}-${concern.target_id}`;
    if (concern.resolved || eligibility === null) return null;
    if (!eligibility.eligible) {
      const why = eligibility.reason ?? '';
      return (
        <p className="text-xs text-text-muted" data-testid={`resolve-ineligible-${key}`}>
          {t.has(`ineligible.${why}`) ? t(`ineligible.${why}`) : t('ineligible.other')}
        </p>
      );
    }
    if (open?.key !== key) {
      return (
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={working}
            data-testid={`resolve-exclude-${key}`}
            onClick={() => {
              setOpen({ key, resolution: 'excluded_from_scope' });
              setReason('');
            }}
          >
            {t('exclude')}
          </button>
          {concern.false_positive_refusal ? (
            <p className="text-xs text-text-muted" data-testid={`resolve-no-fp-${key}`}>
              {t(
                concern.false_positive_refusal === 'item'
                  ? 'noFalsePositive.item'
                  : 'noFalsePositive.overlaps',
              )}
            </p>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              disabled={working}
              data-testid={`resolve-fp-${key}`}
              onClick={() => {
                setOpen({ key, resolution: 'false_positive' });
                setReason('');
              }}
            >
              {t('falsePositive')}
            </button>
          )}
        </div>
      );
    }
    const field = `${id}-${key}`;
    const length = reason.trim().length;
    const valid = length >= REASON_MIN && length <= REASON_MAX;
    return (
      <div className="flex flex-col gap-2" data-testid={`resolve-form-${key}`}>
        <p className="text-xs text-text-muted">
          {t(open.resolution === 'excluded_from_scope' ? 'meansExclude' : 'meansFalsePositive')}
        </p>
        <label htmlFor={field} className="text-sm font-medium">
          {t('reason')}
        </label>
        <textarea
          id={field}
          dir="auto"
          required
          maxLength={REASON_MAX}
          className="min-h-[4rem] rounded-md border border-border p-2 text-sm"
          value={reason}
          data-testid={`resolve-reason-${key}`}
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
        <p className="text-xs text-text-muted">
          {t('reasonHint', { min: REASON_MIN, max: REASON_MAX, length })}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn btn-sm btn-primary"
            disabled={!valid || working}
            data-testid={`resolve-submit-${key}`}
            onClick={() => void submit(concern, open.resolution)}
          >
            {t('submit')}
          </button>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            onClick={() => {
              setOpen(null);
            }}
          >
            {t('cancel')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-2" data-testid="safety-concerns">
      <h3 className="text-sm font-semibold">{t('title')}</h3>
      <p className="text-sm text-text-muted">
        {unresolved > 0 ? t('held', { count: unresolved }) : t('allResolved')}
      </p>
      <p className="text-xs text-text-muted">{t('detectionLimit')}</p>
      <ul className="flex flex-col gap-3">
        {concerns.map((concern) => {
          const key = `${concern.kind}-${concern.target_id}`;
          const resolution = concern.resolution;
          return (
            <li key={key} className="flex flex-col gap-1" data-testid={`safety-concern-${key}`}>
              <p className="text-sm">
                <span className="font-medium">
                  {t(concern.level === 'life_safety' ? 'level.life_safety' : 'level.possibly')}
                </span>{' '}
                <bdi>‘{concern.label}’</bdi>{' '}
                <span className="text-text-muted">
                  ({t(concern.kind === 'mention' ? 'kind.mention' : 'kind.item')})
                </span>
              </p>
              {resolution ? (
                <p className="text-sm text-text-muted" data-testid={`resolved-${key}`}>
                  {t(
                    resolution.resolution === 'excluded_from_scope'
                      ? 'resolvedExcluded'
                      : 'resolvedFalsePositive',
                    {
                      name: resolution.by_name,
                      date: resolution.at.slice(0, 10),
                      reason: resolution.reason,
                    },
                  )}
                </p>
              ) : concern.resolved ? (
                <p className="text-sm text-text-muted" data-testid={`resolved-${key}`}>
                  {t('resolvedByWords')}
                </p>
              ) : (
                action(concern)
              )}
            </li>
          );
        })}
      </ul>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
    </section>
  );
}

/**
 * M6d: what a review approval covers and does not, the exclusions and false
 * positives it is given with, and the confirmation the approver must tick.
 */
export function ApprovalScope({
  concerns,
  confirmed,
  onConfirmedChange,
  disabled,
}: {
  concerns: readonly SafetyConcern[];
  confirmed: boolean;
  onConfirmedChange: (confirmed: boolean) => void;
  disabled: boolean;
}) {
  const t = useTranslations('design.projects.scope');
  const id = useId();
  const excluded = concerns.filter((c) => c.resolution?.resolution === 'excluded_from_scope');
  const falsePositives = concerns.filter((c) => c.resolution?.resolution === 'false_positive');
  return (
    <section className="flex flex-col gap-2" data-testid="approval-scope">
      <h3 className="text-sm font-semibold">{t('title')}</h3>
      <p className="text-sm">{t('covers')}</p>
      <p className="text-sm">{t('notCoveredIntro')}</p>
      <ul className="list-disc ps-5 text-sm">
        {(
          [
            'control',
            'wiring',
            'openOrExcluded',
            'lifeSafety',
            'standards',
            'accuracy',
            'execution',
          ] as const
        ).map((part) => (
          <li key={part}>{t(`notCovered.${part}`)}</li>
        ))}
      </ul>
      <p className="text-sm font-medium">{t('notValidation')}</p>
      {excluded.length > 0 && (
        <div data-testid="scope-excluded">
          <p className="text-sm font-medium">{t('excludedTitle')}</p>
          <ul className="list-disc ps-5 text-sm">
            {excluded.map((c) => (
              <li key={c.target_id}>
                <bdi>‘{c.label}’</bdi>: {c.resolution?.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      {falsePositives.length > 0 && (
        <div data-testid="scope-false-positives">
          <p className="text-sm font-medium">{t('falsePositivesTitle')}</p>
          <ul className="list-disc ps-5 text-sm">
            {falsePositives.map((c) => (
              <li key={c.target_id}>
                <bdi>‘{c.label}’</bdi>: {c.resolution?.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
      <label htmlFor={`${id}-confirm`} className="flex items-start gap-2 text-sm">
        <input
          id={`${id}-confirm`}
          type="checkbox"
          checked={confirmed}
          disabled={disabled}
          data-testid="scope-confirm"
          onChange={(event) => {
            onConfirmedChange(event.target.checked);
          }}
        />
        <span>{t('confirm')}</span>
      </label>
    </section>
  );
}
