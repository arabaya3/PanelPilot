'use client';

import { useTranslations } from 'next-intl';
import { useId } from 'react';

/**
 * The control program under a designed board. The program's Structured Text
 * and its I/O list are the `plc_st` and `plc_io_csv` files, which could carry
 * a draft mark only as a comment that an import strips. The server refuses
 * them (`export_format_unprotected`), from the export and from
 * `POST /api/v1/design/plc` alike, so the panel neither asks for the program
 * nor shows its source: it says why the files are not available.
 */
export function PlcPanel() {
  const t = useTranslations('design.plc');
  const tDesign = useTranslations('design');
  const id = useId();

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="plc-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <p className="text-sm text-text-muted">{t('intro')}</p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled
          aria-describedby={`${id}-unavailable`}
          data-testid="plc-export-st"
          data-unavailable="true"
          className="btn btn-sm btn-secondary"
        >
          {t('st')}
        </button>
        <button
          type="button"
          disabled
          aria-describedby={`${id}-unavailable`}
          data-testid="plc-export-io"
          data-unavailable="true"
          className="btn btn-sm btn-secondary"
        >
          {t('io')}
        </button>
      </div>
      <p id={`${id}-unavailable`} className="text-sm text-text-muted" data-testid="plc-unavailable">
        {tDesign('errors.export_format_unprotected')}
      </p>
    </section>
  );
}
