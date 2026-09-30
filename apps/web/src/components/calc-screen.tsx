'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { CableSizingPanel } from '@/components/cable-sizing-panel';
import { VfdSelectionPanel } from '@/components/vfd-selection-panel';
import type { selectVfd, sizeCable } from '@/lib/calculations';
import type { acquireTrial } from '@/lib/session';

type Tab = 'cable' | 'vfd';

/**
 * `/calc`: the engineering calculations, one tab each.
 *
 * Every figure names the table and page it came from, so it can be checked
 * against the manufacturer's document before it goes on a drawing.
 */
export function CalcScreen({
  acquireImpl,
  sizeImpl,
  selectImpl,
}: {
  acquireImpl?: typeof acquireTrial;
  sizeImpl?: typeof sizeCable;
  selectImpl?: typeof selectVfd;
}) {
  const t = useTranslations('calc');
  const [tab, setTab] = useState<Tab>('cable');
  const tabsId = useId();

  return (
    <AppShell>
      <h1 className="mb-4 text-2xl font-bold tracking-tight">{t('title')}</h1>

      <div
        role="tablist"
        aria-label={t('title')}
        className="mb-5 flex gap-1 self-start rounded-lg border border-border-subtle bg-surface p-1"
      >
        {(['cable', 'vfd'] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`${tabsId}-${key}`}
            aria-selected={tab === key}
            aria-controls={`${tabsId}-panel`}
            data-testid={`calc-tab-${key}`}
            onClick={() => {
              setTab(key);
            }}
            className={`rounded-md px-3 py-2 text-sm transition-colors ${
              tab === key
                ? 'bg-accent-subtle font-semibold text-accent-hover'
                : 'font-medium text-text-muted hover:bg-surface-raised hover:text-text'
            }`}
          >
            {t(`tab.${key}`)}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`${tabsId}-panel`} aria-labelledby={`${tabsId}-${tab}`}>
        {tab === 'cable' ? (
          <CableSizingPanel
            {...(acquireImpl ? { acquireImpl } : {})}
            {...(sizeImpl ? { sizeImpl } : {})}
          />
        ) : (
          <VfdSelectionPanel
            {...(acquireImpl ? { acquireImpl } : {})}
            {...(selectImpl ? { selectImpl } : {})}
          />
        )}
      </div>
    </AppShell>
  );
}
