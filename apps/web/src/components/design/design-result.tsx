'use client';

import { useTranslations } from 'next-intl';
import { useId, useRef, useState } from 'react';

import { round } from '@/components/cable-sizing-panel';
import { useNoteText } from '@/components/design/note-text';
import { fitZoom, stepZoom } from '@/components/design/workspace';
import { DownloadIcon, FitIcon, ZoomInIcon, ZoomOutIcon } from '@/components/icons';
import { Notice } from '@/components/ui';
import type { BoardDesignResponse, ExportFormat } from '@/lib/design';

/** Every export the API offers for a designed project, in the order shown. */
export const FORMATS: ExportFormat[] = [
  'pdf',
  'calculations_pdf',
  'dxf',
  'qet',
  'aml',
  'devices_csv',
  'parts_csv',
  'cables_csv',
  'circuits_csv',
  'terminals_csv',
  'eplan_devices_csv',
  'ace_components_csv',
  'ace_terminals_csv',
  'json',
];

type Board = NonNullable<BoardDesignResponse['project']['boards']>[number];
type Device = NonNullable<Board['devices']>[number];

/** A circuit's first device as the table shows it: "-Q3 C16", "-F3 63 A aR". */
function protectionText(device: Device): string {
  const product = `-${device.designation?.product ?? ''}`;
  const rating = round(device.rated_current_a ?? '');
  if (device.kind === 'fuse') return `${product} ${rating} A ${device.curve ?? ''}`.trim();
  const breaking = device.breaking_capacity_ka ? ` ${round(device.breaking_capacity_ka)} kA` : '';
  return `${product} ${device.curve ?? ''}${rating}${breaking}`;
}

function BoardTable({ board }: { board: Board }) {
  const t = useTranslations('design');
  const noteText = useNoteText();
  const devices = new Map((board.devices ?? []).map((device) => [device.id, device]));
  const cables = new Map((board.cables ?? []).map((cable) => [cable.id, cable]));
  return (
    <div data-testid={`design-board-${board.name}`} className="flex flex-col gap-3">
      <h3 className="text-base font-semibold">
        <bdi>{board.name}</bdi>
        {board.fed_from && (
          <span className="ms-2 text-sm font-normal">
            {t('fedFromLabel', { board: board.fed_from })}
          </span>
        )}
      </h3>
      <div className="overflow-x-auto">
        <table className="data-table w-full text-sm">
          <thead>
            <tr>
              <th className="p-2 text-start">{t('col.circuit')}</th>
              <th className="p-2 text-start">{t('col.phase')}</th>
              <th className="p-2 text-start">{t('col.current')}</th>
              <th className="p-2 text-start">{t('col.breaker')}</th>
              <th className="p-2 text-start">{t('col.rcd')}</th>
              <th className="p-2 text-start">{t('col.cable')}</th>
              <th className="p-2 text-start">{t('col.drop')}</th>
              <th className="p-2 text-start">{t('col.loop')}</th>
            </tr>
          </thead>
          <tbody>
            {(board.circuits ?? []).map((circuit) => {
              const breaker = devices.get(circuit.device_ids?.[0] ?? '');
              const rcd = circuit.upstream_id ? devices.get(circuit.upstream_id) : undefined;
              const cable = circuit.cable_id ? cables.get(circuit.cable_id) : undefined;
              return (
                <tr key={circuit.id}>
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
                  <td className="p-2" data-testid="circuit-drop">
                    {circuit.voltage_drop_percent != null
                      ? `${round(circuit.voltage_drop_percent)} %`
                      : '—'}
                  </td>
                  <td className="p-2" data-testid="circuit-loop">
                    {circuit.earth_loop_ohm != null ? `${round(circuit.earth_loop_ohm)} Ω` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {(board.notes ?? []).length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-semibold">{t('notes')}</h4>
          <ul className="list-disc ps-5 text-sm" data-testid="design-notes">
            {(board.notes ?? []).map((note, index) => (
              <li key={index}>{noteText(note)}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** What the design gave the selected board, read only: nothing here edits it. */
function BoardInspector({ board }: { board: Board }) {
  const t = useTranslations('workspace.drawings');
  const supply = board.supply;
  const incomer = (board.devices ?? []).find((device) => board.incomer_ids?.includes(device.id));
  const rows: Array<[string, string]> = [[t('board'), board.name]];
  if (board.location) rows.push([t('location'), board.location]);
  if (supply) {
    rows.push([
      t('supply'),
      `${round(supply.voltage_v)} V, ${String(supply.phases)}~, ${round(supply.frequency_hz)} Hz, ${supply.earthing}`,
    ]);
  }
  if (board.fed_from) rows.push([t('fedFrom'), board.fed_from]);
  if (incomer) rows.push([t('incomer'), protectionText(incomer)]);
  rows.push([t('circuits'), String((board.circuits ?? []).length)]);
  rows.push([t('devices'), String((board.devices ?? []).length)]);
  rows.push([t('cables'), String((board.cables ?? []).length)]);
  return (
    <aside className="panel" aria-label={t('inspector')} data-testid="board-inspector">
      <div className="panel-header">
        <h3 className="panel-title">{t('inspector')}</h3>
      </div>
      <dl className="panel-body grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-text-muted">{label}</dt>
            <dd className="min-w-0 break-words">
              <bdi dir="ltr">{value}</bdi>
            </dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

/**
 * A designed project's boards as sheets: one board's circuits, protection
 * and cables per sheet, on a paper surface with zoom. This is the designed
 * schedule the drawing set is generated from; the drawings themselves are the
 * PDF / DXF exports. The sheet is never mirrored in a right-to-left page.
 */
export function DrawingSheets({ response }: { response: BoardDesignResponse }) {
  const t = useTranslations('workspace.drawings');
  const id = useId();
  const boards = response.project.boards ?? [];
  const [selected, setSelected] = useState(0);
  const [zoom, setZoom] = useState(1);
  const frame = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  if (boards.length === 0) return null;
  const index = Math.min(selected, boards.length - 1);
  const board = boards[index];

  return (
    <section className="flex flex-col gap-4" data-testid="design-result">
      <div className="toolbar flex flex-wrap items-center gap-2">
        {boards.length > 1 && (
          <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-sheet`}>
            {t('sheet')}
            <select
              id={`${id}-sheet`}
              value={index}
              onChange={(event) => {
                setSelected(Number(event.target.value));
              }}
              className="input w-auto"
              dir="ltr"
            >
              {boards.map((entry, position) => (
                <option key={entry.id} value={position}>
                  {`${String(position + 1)}. ${entry.name}`}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="ms-auto flex items-center gap-1" role="group" aria-label={t('zoom')}>
          <button
            type="button"
            className="btn-icon"
            aria-label={t('zoomOut')}
            data-testid="zoom-out"
            onClick={() => {
              setZoom((current) => stepZoom(current, -1));
            }}
          >
            <ZoomOutIcon width="18" height="18" />
          </button>
          <output
            className="min-w-14 text-center font-mono text-sm"
            dir="ltr"
            data-testid="zoom-level"
          >
            {`${String(Math.round(zoom * 100))} %`}
          </output>
          <button
            type="button"
            className="btn-icon"
            aria-label={t('zoomIn')}
            data-testid="zoom-in"
            onClick={() => {
              setZoom((current) => stepZoom(current, 1));
            }}
          >
            <ZoomInIcon width="18" height="18" />
          </button>
          <button
            type="button"
            className="btn-icon"
            aria-label={t('fit')}
            data-testid="zoom-fit"
            onClick={() => {
              setZoom(fitZoom(frame.current?.clientWidth ?? 0, content.current?.scrollWidth ?? 0));
            }}
          >
            <FitIcon width="18" height="18" />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div
          ref={frame}
          className="sheet-frame overflow-auto"
          dir="ltr"
          role="region"
          aria-label={t('sheetLabel', { board: board?.name ?? '' })}
          tabIndex={0}
          data-testid="sheet-frame"
        >
          <div
            ref={content}
            className="p-4"
            // The sheet is always left-to-right, so it scales from its top-left corner.
            style={{ transform: `scale(${String(zoom)})`, transformOrigin: '0 0' }}
            data-testid="sheet-content"
          >
            {boards.map((entry, position) => (
              <div key={entry.id} hidden={position !== index}>
                <BoardTable board={entry} />
              </div>
            ))}
          </div>
        </div>
        {board && <BoardInspector board={board} />}
      </div>
      <p className="text-sm text-text-muted">{t('previewNote')}</p>
    </section>
  );
}

/** Every export of the one designed project, each saved as the file the server names. */
export function ExportPanel({
  onExport,
  exportError,
  testIdPrefix = 'design-export',
}: {
  onExport: (format: ExportFormat) => Promise<void>;
  exportError: string | null;
  /** Tells a draft export's buttons from a revision export's. */
  testIdPrefix?: string;
}) {
  const t = useTranslations('design');
  return (
    <div>
      <div className="flex flex-wrap gap-2">
        {FORMATS.map((format) => (
          <button
            key={format}
            type="button"
            data-testid={`${testIdPrefix}-${format}`}
            onClick={() => {
              void onExport(format);
            }}
            className="btn btn-sm btn-secondary"
          >
            <DownloadIcon width="16" height="16" aria-hidden="true" />
            {t(`format.${format}`)}
          </button>
        ))}
      </div>
      {exportError && (
        <div className="mt-3">
          <Notice tone="critical">{exportError}</Notice>
        </div>
      )}
    </div>
  );
}
