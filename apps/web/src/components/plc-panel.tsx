'use client';

import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { useOutcomeText } from '@/components/design/note-text';
import { exportDesign, writePlcProgram, type DesignProject, type PlcProgram } from '@/lib/design';

/**
 * The control program under a designed board: the Structured Text that drives
 * every PLC-switched circuit's contactor, the checker's verdict on it, and the
 * program and its I/O list as files.
 *
 * The verdict is shown as the checker gave it: "valid" only when the code was
 * parsed in full and nothing was found.
 */
export function PlcPanel({
  token,
  project,
  profile,
  writeImpl = writePlcProgram,
  exportImpl = exportDesign,
  saveImpl,
}: {
  token: string;
  project: DesignProject;
  profile: Record<string, unknown> | null;
  writeImpl?: typeof writePlcProgram;
  exportImpl?: typeof exportDesign;
  saveImpl: (blob: Blob, filename: string) => void;
}) {
  const t = useTranslations('design.plc');
  const say = useOutcomeText();
  const [program, setProgram] = useState<PlcProgram | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function write() {
    setWorking(true);
    setMessage(null);
    const outcome = await writeImpl({ token, project, profile });
    setWorking(false);
    if (outcome.kind === 'written') {
      setProgram(outcome.program);
    } else {
      setMessage(say(outcome));
    }
  }

  async function download(format: 'plc_st' | 'plc_io_csv') {
    const outcome = await exportImpl({ token, project, format, profile });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      setMessage(say(outcome));
    }
  }

  const status = program?.validation.status;

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="plc-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <p className="text-sm text-text-muted">{t('intro')}</p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={working}
          onClick={() => {
            void write();
          }}
          className="btn btn-primary btn-sm"
        >
          {working ? t('working') : t('submit')}
        </button>
        <button
          type="button"
          data-testid="plc-export-st"
          onClick={() => {
            void download('plc_st');
          }}
          className="btn btn-sm btn-secondary"
        >
          {t('st')}
        </button>
        <button
          type="button"
          data-testid="plc-export-io"
          onClick={() => {
            void download('plc_io_csv');
          }}
          className="btn btn-sm btn-secondary"
        >
          {t('io')}
        </button>
      </div>

      {message && (
        <p role="status" className="text-sm" data-testid="plc-message">
          {message}
        </p>
      )}

      {program && status && (
        <div data-testid="plc-result" className="flex flex-col gap-3">
          <p
            role={status === 'valid' ? 'status' : 'alert'}
            data-testid="plc-status"
            className={`text-sm font-semibold ${status === 'valid' ? 'text-accent' : 'text-danger'}`}
          >
            {t(`status.${status}`)}
          </p>
          {(program.validation.findings ?? []).length > 0 && (
            <ul className="list-disc ps-5 text-sm" dir="ltr">
              {(program.validation.findings ?? []).map((finding, index) => (
                <li key={index}>
                  {finding.line == null
                    ? finding.message
                    : `${String(finding.line)}: ${finding.message}`}
                </li>
              ))}
            </ul>
          )}
          <p className="text-sm text-text-muted">
            {t('io_count', {
              inputs: program.io.filter((point) => point.direction === 'input').length,
              outputs: program.io.filter((point) => point.direction === 'output').length,
            })}
          </p>
          <pre
            dir="ltr"
            data-testid="plc-source"
            className="max-h-96 overflow-auto rounded-md bg-surface-raised p-3 text-xs"
          >
            {program.source}
          </pre>
        </div>
      )}
    </section>
  );
}
