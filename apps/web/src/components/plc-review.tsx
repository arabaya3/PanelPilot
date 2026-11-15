'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { CheckCircleIcon, CodeIcon } from '@/components/icons';
import { PlcGenerate } from '@/components/plc-generate';
import { PlcView } from '@/components/plc-view';
import {
  EmptyState,
  LoadingState,
  Notice,
  PageHeader,
  Tabs,
  tabId,
  tabPanelId,
} from '@/components/ui';
import { generatePlc, reviewPlc } from '@/lib/plc';
import { acquireTrial } from '@/lib/session';

type PlcValidationResult = components['schemas']['PlcValidationResult'];

const PLC_TABS = ['check', 'generate'] as const;

type Review =
  | { kind: 'idle' }
  | { kind: 'checking' }
  /** The source as submitted, so the lines match the findings' numbers. */
  | { kind: 'reviewed'; source: string; result: PlcValidationResult }
  | { kind: 'rejected'; detail: string | null }
  | { kind: 'too-long'; length: number }
  | { kind: 'failed' };

/** `PlcValidationRequest.source`'s `max_length`: longer is refused unread. */
const MAX_SOURCE_LENGTH = 100_000;

/**
 * Paste Structured Text, get it checked.
 *
 * The review endpoint and `PlcView` both existed with no page between them,
 * so the only way to reach either was the API directly. The findings are
 * drawn against the program exactly as it was submitted, not the text box's
 * current contents: an edit after checking would otherwise put line 8's error
 * beside whatever line 8 has become.
 */
export function PlcReview({
  reviewImpl = reviewPlc,
  generateImpl = generatePlc,
  acquireImpl = acquireTrial,
}: {
  reviewImpl?: typeof reviewPlc;
  generateImpl?: typeof generatePlc;
  acquireImpl?: typeof acquireTrial;
}) {
  const t = useTranslations('plc');
  const [source, setSource] = useState('');
  const [review, setReview] = useState<Review>({ kind: 'idle' });
  const [tab, setTab] = useState<'check' | 'generate'>('check');
  const fieldId = useId();
  const tabsId = useId();

  async function check() {
    const submitted = source;
    // Said here, in characters: past the limit the server answers about
    // request bytes, which is not a size the reader can act on.
    if (submitted.length > MAX_SOURCE_LENGTH) {
      setReview({ kind: 'too-long', length: submitted.length });
      return;
    }
    setReview({ kind: 'checking' });
    const outcome = await reviewImpl({ source: submitted });
    if (outcome.kind === 'reviewed') {
      setReview({ kind: 'reviewed', source: submitted, result: outcome.result });
    } else {
      setReview(outcome);
    }
  }

  return (
    <AppShell>
      <PageHeader
        title={t('heading')}
        description={tab === 'check' ? t('intro') : t('generateIntro')}
      />

      <div className="mb-5">
        <Tabs
          items={PLC_TABS.map((key) => ({
            key,
            // The test id rides on the label; a click on it reaches the tab.
            label: (
              <span data-testid={`plc-tab-${key}`}>
                {key === 'check' ? t('tabCheck') : t('tabGenerate')}
              </span>
            ),
          }))}
          selected={tab}
          onSelect={setTab}
          label={t('heading')}
          idBase={tabsId}
        />
      </div>

      <div
        role="tabpanel"
        id={tabPanelId(tabsId, tab)}
        aria-labelledby={tabId(tabsId, tab)}
        tabIndex={0}
      >
        {tab === 'generate' ? (
          <PlcGenerate generateImpl={generateImpl} acquireImpl={acquireImpl} />
        ) : (
          // Side by side from `lg`, so a finding and the line it names are both
          // on screen; stacked below that, the code first.
          <div className="grid items-start gap-5 lg:grid-cols-2">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void check();
              }}
              className="card flex flex-col gap-3 p-4 md:p-5"
            >
              <label htmlFor={fieldId} className="field-label">
                {t('label')}
              </label>
              <textarea
                id={fieldId}
                value={source}
                onChange={(event) => {
                  setSource(event.target.value);
                }}
                rows={16}
                spellCheck={false}
                dir="ltr"
                placeholder={t('placeholder')}
                className="w-full rounded-md border border-border bg-surface-raised p-3 font-mono text-sm leading-relaxed text-text placeholder:text-text-muted"
              />
              <div>
                <button
                  type="submit"
                  disabled={source.trim() === '' || review.kind === 'checking'}
                  className="btn btn-primary"
                >
                  <CheckCircleIcon width="16" height="16" />
                  {review.kind === 'checking' ? t('checking') : t('check')}
                </button>
              </div>
            </form>

            <div className="flex min-w-0 flex-col gap-3">
              {review.kind === 'reviewed' && (
                <PlcView
                  language="structured-text"
                  source={review.source}
                  validation={review.result}
                />
              )}
              {review.kind === 'rejected' && (
                <Notice tone="warning" testId="plc-rejected">
                  <span dir="auto">{review.detail ?? t('rejected')}</span>
                </Notice>
              )}
              {review.kind === 'too-long' && (
                <Notice tone="warning" testId="plc-too-long">
                  {t('tooLong', { length: review.length, limit: MAX_SOURCE_LENGTH })}
                </Notice>
              )}
              {review.kind === 'failed' && (
                <Notice tone="critical" testId="plc-failed">
                  {t('failed')}
                </Notice>
              )}
              {review.kind === 'checking' && <LoadingState>{t('checking')}</LoadingState>}
              {review.kind === 'idle' && (
                <EmptyState icon={<CodeIcon width="28" height="28" />} title={t('emptyResult')} />
              )}
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
