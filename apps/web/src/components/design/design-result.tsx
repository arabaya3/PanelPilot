'use client';

import { useTranslations } from 'next-intl';

import { round } from '@/components/cable-sizing-panel';
import type { BoardDesignResponse, ExportFormat } from '@/lib/design';

const FORMATS: ExportFormat[] = [
  'pdf',
  'dxf',
  'qet',
  'aml',
  'devices_csv',
  'parts_csv',
  'cables_csv',
  'circuits_csv',
  'json',
];

type Board = NonNullable<BoardDesignResponse['project']['boards']>[number];
type Device = NonNullable<Board['devices']>[number];

/** A circuit's first device as the table shows it: "-Q3 C16", "-F3 63 A aR". */
function protectionText(device: Device): string {
  const product = `-${device.designation?.product ?? ''}`;
  const rating = round(device.rated_current_a ?? '');
  if (device.kind === 'fuse') return `${product} ${rating} A ${device.curve ?? ''}`.trim();
  return `${product} ${device.curve ?? ''}${rating}`;
}

function BoardTable({ board, showName }: { board: Board; showName: boolean }) {
  const t = useTranslations('design');
  const devices = new Map((board.devices ?? []).map((device) => [device.id, device]));
  const cables = new Map((board.cables ?? []).map((cable) => [cable.id, cable]));
  return (
    <div data-testid={`design-board-${board.name}`} className="flex flex-col gap-3">
      {showName && (
        <h2 className="text-base font-semibold">
          {board.name}
          {board.fed_from && (
            <span className="ms-2 text-sm font-normal text-text-muted">
              {t('fedFromLabel', { board: board.fed_from })}
            </span>
          )}
        </h2>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm" dir="ltr">
          <thead>
            <tr className="border-b border-border-subtle text-start">
              <th className="p-2 text-start">{t('col.circuit')}</th>
              <th className="p-2 text-start">{t('col.phase')}</th>
              <th className="p-2 text-start">{t('col.current')}</th>
              <th className="p-2 text-start">{t('col.breaker')}</th>
              <th className="p-2 text-start">{t('col.rcd')}</th>
              <th className="p-2 text-start">{t('col.cable')}</th>
            </tr>
          </thead>
          <tbody>
            {(board.circuits ?? []).map((circuit) => {
              const breaker = devices.get(circuit.device_ids?.[0] ?? '');
              const rcd = circuit.upstream_id ? devices.get(circuit.upstream_id) : undefined;
              const cable = circuit.cable_id ? cables.get(circuit.cable_id) : undefined;
              return (
                <tr key={circuit.id} className="border-b border-border-subtle last:border-b-0">
                  <td className="p-2" dir="auto">
                    {circuit.description}
                  </td>
                  <td className="p-2">{circuit.phase}</td>
                  <td className="p-2">{`${round(circuit.design_current_a)} A`}</td>
                  <td className="p-2">{breaker ? protectionText(breaker) : ''}</td>
                  <td className="p-2">
                    {rcd
                      ? `-${rcd.designation?.product ?? ''} ${round(rcd.residual_current_ma ?? '')} mA`
                      : '—'}
                  </td>
                  <td className="p-2">
                    {cable
                      ? `${String(cable.cores)}G${round(cable.cross_section_mm2)} ${cable.material}`
                      : ''}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(board.notes ?? []).length > 0 && (
        <div>
          <h3 className="mb-2 text-sm font-semibold text-text-muted">{t('notes')}</h3>
          <ul className="list-disc ps-5 text-sm" dir="ltr" data-testid="design-notes">
            {(board.notes ?? []).map((note, index) => (
              <li key={index}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * A designed project: each board's circuits with their protection and cable,
 * what the design assumed, and every export of the one project.
 */
export function DesignResult({
  response,
  onExport,
  exportError,
}: {
  response: BoardDesignResponse;
  onExport: (format: ExportFormat) => Promise<void>;
  exportError: string | null;
}) {
  const t = useTranslations('design');
  const boards = response.project.boards ?? [];
  if (boards.length === 0) return null;

  return (
    <section className="card flex flex-col gap-6 p-4 md:p-5" data-testid="design-result">
      {boards.map((board) => (
        <BoardTable key={board.id} board={board} showName={boards.length > 1} />
      ))}
      <div>
        <h2 className="mb-2 text-sm font-semibold text-text-muted">{t('downloads')}</h2>
        <div className="flex flex-wrap gap-2">
          {FORMATS.map((format) => (
            <button
              key={format}
              type="button"
              data-testid={`design-export-${format}`}
              onClick={() => {
                void onExport(format);
              }}
              className="btn btn-sm btn-secondary"
            >
              {t(`format.${format}`)}
            </button>
          ))}
        </div>
        {exportError && (
          <p role="alert" className="mt-2 text-sm text-danger">
            {exportError}
          </p>
        )}
      </div>
    </section>
  );
}
