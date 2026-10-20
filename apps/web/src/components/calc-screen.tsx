'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { CableSizingPanel } from '@/components/cable-sizing-panel';
import { PanelBomPanel } from '@/components/panel-bom-panel';
import { PageHeader, Tabs, tabId, tabPanelId } from '@/components/ui';
import { VfdSelectionPanel } from '@/components/vfd-selection-panel';
import type { buildBom, listDriveRanges, selectVfd, sizeCable } from '@/lib/calculations';
import type { acquireTrial } from '@/lib/session';

type Tab = 'cable' | 'vfd' | 'bom';

const TABS: readonly Tab[] = ['cable', 'vfd', 'bom'];

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
  listImpl,
  buildImpl,
}: {
  acquireImpl?: typeof acquireTrial;
  sizeImpl?: typeof sizeCable;
  selectImpl?: typeof selectVfd;
  listImpl?: typeof listDriveRanges;
  buildImpl?: typeof buildBom;
}) {
  const t = useTranslations('calc');
  const [tab, setTab] = useState<Tab>('cable');
  const tabsId = useId();

  return (
    <AppShell>
      <PageHeader title={t('title')} description={t('description')} />

      <div className="mb-5">
        <Tabs
          items={TABS.map((key) => ({
            key,
            // The test id rides on the label; a click on it reaches the tab.
            label: <span data-testid={`calc-tab-${key}`}>{t(`tab.${key}`)}</span>,
          }))}
          selected={tab}
          onSelect={setTab}
          label={t('title')}
          idBase={tabsId}
        />
      </div>

      <div
        role="tabpanel"
        id={tabPanelId(tabsId, tab)}
        aria-labelledby={tabId(tabsId, tab)}
        tabIndex={0}
      >
        {tab === 'cable' && (
          <CableSizingPanel
            {...(acquireImpl ? { acquireImpl } : {})}
            {...(sizeImpl ? { sizeImpl } : {})}
          />
        )}
        {tab === 'vfd' && (
          <VfdSelectionPanel
            {...(acquireImpl ? { acquireImpl } : {})}
            {...(selectImpl ? { selectImpl } : {})}
            {...(listImpl ? { listImpl } : {})}
          />
        )}
        {tab === 'bom' && (
          <PanelBomPanel
            {...(acquireImpl ? { acquireImpl } : {})}
            {...(buildImpl ? { buildImpl } : {})}
            {...(listImpl ? { listImpl } : {})}
          />
        )}
      </div>
    </AppShell>
  );
}
