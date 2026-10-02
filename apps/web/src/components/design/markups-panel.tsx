'use client';

import { useTranslations } from 'next-intl';
import { useId, useState } from 'react';

import { useOutcomeText } from '@/components/design/note-text';
import { FilePicker } from '@/components/file-picker';
import { readMarkups, type DesignProject, type MarkupReport } from '@/lib/design';

/**
 * A reviewed drawing set's marks: upload the PDF the consultant returned and
 * see every comment with the board and label it sits on. Nothing is changed;
 * the engineer makes the changes the review asks for.
 */
export function MarkupsPanel({
  token,
  project,
  profile,
  readImpl = readMarkups,
}: {
  token: string;
  project: DesignProject;
  profile: Record<string, unknown> | null;
  readImpl?: typeof readMarkups;
}) {
  const t = useTranslations('design.markups');
  const say = useOutcomeText();
  const id = useId();
  const [report, setReport] = useState<MarkupReport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [working, setWorking] = useState(false);

  async function read(file: File) {
    setWorking(true);
    setMessage(null);
    const outcome = await readImpl({ token, file, filename: file.name, project, profile });
    setWorking(false);
    if (outcome.kind === 'read') setReport(outcome.report);
    else {
      setReport(null);
      setMessage(say(outcome));
    }
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="markups-panel">
      <h2 className="text-lg font-semibold">{t('title')}</h2>
      <FilePicker
        id={`${id}-pdf`}
        label={t('upload')}
        help={t('help')}
        accept=".pdf,application/pdf"
        disabled={working}
        onFile={(file) => {
          void read(file);
        }}
      />
      {message && (
        <p role="status" className="text-sm" data-testid="markups-message">
          {message}
        </p>
      )}
      {report && (
        <div className="flex flex-col gap-2" data-testid="markups-result">
          <p className="text-sm text-text-muted">
            {report.markups.length === 0
              ? t('none')
              : t(report.matched ? 'found' : 'foundUnmatched', { count: report.markups.length })}
          </p>
          {report.markups.length > 0 && (
            <ol className="flex flex-col divide-y divide-border-subtle text-sm">
              {report.markups.map((markup, index) => (
                <li key={index} className="flex flex-col gap-1 py-2">
                  <span className="text-text-muted" dir="auto">
                    {[
                      t('page', { page: markup.page }),
                      markup.board,
                      markup.near && t('near', { label: markup.near }),
                      markup.author,
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                  <span dir="auto">{markup.text}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  );
}
