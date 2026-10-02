'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import { Field } from '@/components/cable-sizing-panel';
import { DesignResult } from '@/components/design/design-result';
import { LoadRows } from '@/components/design/load-rows';
import { useOutcomeText } from '@/components/design/note-text';
import {
  blankBoard,
  blankLoad,
  DRAWING_LANGUAGES,
  EARTHING,
  fromRequest,
  isComplete,
  profileFrom,
  rowsFrom,
  toRequest,
  withLanguage,
  type BoardForm,
  type DrawingLanguage,
  type Load,
  type ProjectInfo,
} from '@/components/design/schedule';
import { MarkupsPanel } from '@/components/design/markups-panel';
import { ProjectsPanel, type OpenedProject } from '@/components/design/projects-panel';
import { ScheduleSources } from '@/components/design/schedule-sources';
import { PlcPanel } from '@/components/plc-panel';
import { QuotationPanel } from '@/components/quotation-panel';
import {
  designProject,
  exportDesign,
  importSchedule,
  suggestSchedule,
  type BoardDesignResponse,
  type SavedProject,
  type DesignOutcome,
  type ExportFormat,
} from '@/lib/design';
import { acquireTrial } from '@/lib/session';

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | { kind: 'designed'; response: BoardDesignResponse }
  | { kind: 'error'; outcome: Exclude<DesignOutcome, { kind: 'designed' }> };

const INITIAL_INFO: ProjectInfo = {
  name: '',
  number: '',
  customer: '',
  consultant: '',
  contractor: '',
};

/** Save a blob under a name, the way a download link does. */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function parseProfile(text: string): Record<string, unknown> | null | 'invalid' {
  if (text.trim() === '') return null;
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : 'invalid';
  } catch {
    return 'invalid';
  }
}

/**
 * `/design`: a project of boards from their load schedules to drawing sets.
 *
 * Each board is a tab. A board fed from another gets a feeder there, sized
 * from its own design. The project is designed under the company's settings
 * (pasted as JSON, only what differs from the default), and every output --
 * the PDF drawing set, DXF, QElectroTech, AutomationML, the CSV lists, the
 * quotation and the PLC program -- comes from the one designed project.
 */
export function DesignScreen({
  acquireImpl = acquireTrial,
  designImpl = designProject,
  exportImpl = exportDesign,
  importImpl = importSchedule,
  suggestImpl = suggestSchedule,
  saveImpl = saveBlob,
}: {
  acquireImpl?: typeof acquireTrial;
  designImpl?: typeof designProject;
  exportImpl?: typeof exportDesign;
  importImpl?: typeof importSchedule;
  suggestImpl?: typeof suggestSchedule;
  saveImpl?: typeof saveBlob;
}) {
  const t = useTranslations('design');
  const say = useOutcomeText();
  const id = useId();
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [info, setInfo] = useState<ProjectInfo>(INITIAL_INFO);
  const [boards, setBoards] = useState<BoardForm[]>([blankBoard(0, 1, 'DB1')]);
  const [activeKey, setActiveKey] = useState(0);
  // Board keys and row keys both come from this counter, so none collide.
  const nextKey = useRef(2);
  const [profileText, setProfileText] = useState('');
  // Follows the page's language until the engineer picks one: the locale is
  // read from storage after the first render, so it cannot seed the state.
  const locale = useLocale();
  const [chosenLanguage, setDrawingLanguage] = useState<DrawingLanguage | null>(null);
  const drawingLanguage: DrawingLanguage = chosenLanguage ?? (locale === 'ar' ? 'ar' : 'en');
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const [exportError, setExportError] = useState<string | null>(null);
  const [opened, setOpened] = useState<OpenedProject | null>(null);

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
  }, [acquireImpl]);

  useEffect(() => {
    void connect();
  }, [connect]);

  const token = session.kind === 'ready' ? session.token : null;
  const active = boards.find((board) => board.key === activeKey) ?? boards[0];
  const parsed = parseProfile(profileText);
  const profile = withLanguage(parsed === 'invalid' ? null : parsed, drawingLanguage);

  function takeKeys(count: number): number {
    const first = nextKey.current;
    nextKey.current += Math.max(1, count);
    return first;
  }

  function updateBoard(key: number, patch: Partial<BoardForm>) {
    setBoards((current) => {
      const before = current.find((board) => board.key === key);
      return current.map((board) => {
        if (board.key === key) return { ...board, ...patch };
        // A renamed board stays the supply of the boards it feeds.
        if (patch.name !== undefined && before && board.fedFrom === before.name) {
          return { ...board, fedFrom: patch.name };
        }
        return board;
      });
    });
  }

  function updateLoads(key: number, change: (loads: Load[]) => Load[]) {
    setBoards((current) =>
      current.map((board) =>
        board.key === key ? { ...board, loads: change(board.loads) } : board,
      ),
    );
  }

  function addBoard() {
    const key = takeKeys(2);
    const main = boards[0]?.name ?? '';
    setBoards((current) => [
      ...current,
      blankBoard(key, key + 1, `DB${String(current.length + 1)}`, main),
    ]);
    setActiveKey(key);
  }

  function removeBoard(key: number) {
    const removed = boards.find((board) => board.key === key);
    const remaining = boards
      .filter((board) => board.key !== key)
      .map((board) =>
        removed && board.fedFrom === removed.name ? { ...board, fedFrom: removed.fedFrom } : board,
      );
    setBoards(remaining);
    if (activeKey === key && remaining[0]) setActiveKey(remaining[0].key);
  }

  const complete = isComplete(info, boards);

  function onOpened(project: SavedProject, fill: boolean) {
    setOpened({
      id: project.id,
      name: project.name,
      revision: project.revision,
      revisions: project.revisions.map((revision) => revision.number),
    });
    if (!fill) return;
    const loaded = fromRequest(project.request, nextKey.current);
    nextKey.current += Math.max(1, loaded.used);
    setInfo(loaded.info);
    setBoards(loaded.boards);
    if (loaded.boards[0]) setActiveKey(loaded.boards[0].key);
    const settings = profileFrom(project.request.profile);
    setProfileText(settings.text);
    setDrawingLanguage(settings.language);
    setResult({ kind: 'idle' });
    setExportError(null);
  }

  async function run() {
    if (token === null || !complete) return;
    if (parsed === 'invalid') {
      setResult({ kind: 'error', outcome: { kind: 'refused', detail: t('profile.invalid') } });
      return;
    }
    setResult({ kind: 'working' });
    setExportError(null);
    const outcome = await designImpl({ token, request: toRequest(info, boards, profile) });
    if (outcome.kind === 'designed') {
      setResult({ kind: 'designed', response: outcome.response });
    } else {
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  async function download(format: ExportFormat) {
    if (token === null || result.kind !== 'designed') return;
    setExportError(null);
    const outcome = await exportImpl({
      token,
      project: result.response.project,
      format,
      profile,
    });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      setExportError(say(outcome));
    }
  }

  function infoField(key: keyof ProjectInfo, ltr = false) {
    return (
      <Field id={`${id}-${key}`} label={t(`field.${key}`)}>
        <input
          id={`${id}-${key}`}
          dir={ltr ? 'ltr' : undefined}
          value={info[key]}
          onChange={(event) => {
            setInfo((current) => ({ ...current, [key]: event.target.value }));
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  function boardField(
    key: 'name' | 'location' | 'voltage' | 'faultLevel' | 'earthLoop' | 'feederLength',
    label: string,
    unit = '',
  ) {
    if (!active) return null;
    return (
      <Field id={`${id}-board-${key}`} label={t(`field.${label}`)} unit={unit}>
        <input
          id={`${id}-board-${key}`}
          dir="ltr"
          inputMode={unit ? 'decimal' : 'text'}
          value={active[key]}
          onChange={(event) => {
            updateBoard(active.key, { [key]: event.target.value });
          }}
          className="input w-full"
        />
      </Field>
    );
  }

  const designed = result.kind === 'designed' ? result.response.project : null;
  // The PLC drives every contactor and every drive.
  const hasPlcOutputs = (designed?.boards ?? []).some((board) =>
    (board.devices ?? []).some((device) => device.kind === 'contactor' || device.kind === 'drive'),
  );

  return (
    <AppShell>
      <h1 className="mb-2 text-2xl font-bold tracking-tight">{t('title')}</h1>
      <p className="mb-5 max-w-3xl text-text-muted">{t('intro')}</p>

      {session.kind !== 'ready' && session.kind !== 'starting' && (
        <p role="alert" className="mb-4 text-sm text-danger">
          {t('unavailable')}
        </p>
      )}

      {token !== null && (
        <div className="mb-6">
          <ProjectsPanel
            token={token}
            current={
              complete && parsed !== 'invalid'
                ? { name: info.name.trim(), request: toRequest(info, boards, profile) }
                : null
            }
            opened={opened}
            onOpened={onOpened}
          />
        </div>
      )}

      <form
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
        className="mb-6 flex flex-col gap-4"
      >
        <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-5">
          <legend className="px-1 text-sm font-semibold">{t('project')}</legend>
          {infoField('name')}
          {infoField('number', true)}
          {infoField('customer')}
          {infoField('consultant')}
          {infoField('contractor')}
        </fieldset>

        <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t('boards')}>
          {boards.map((board) => (
            <button
              key={board.key}
              type="button"
              aria-pressed={board.key === active?.key}
              data-testid={`board-tab-${String(board.key)}`}
              onClick={() => {
                setActiveKey(board.key);
              }}
              className={`btn btn-sm ${board.key === active?.key ? 'btn-primary' : 'btn-secondary'}`}
              dir="ltr"
            >
              {board.name || '—'}
            </button>
          ))}
          <button
            type="button"
            data-testid="board-add"
            onClick={addBoard}
            className="btn btn-sm btn-ghost"
          >
            {t('addBoard')}
          </button>
        </div>

        {active && (
          <>
            <fieldset className="card grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-6">
              <legend className="px-1 text-sm font-semibold">{t('board')}</legend>
              {boardField('name', 'board')}
              {boardField('location', 'location')}
              {boardField('voltage', 'voltage', 'V')}
              <Field id={`${id}-phases`} label={t('field.phases')}>
                <select
                  id={`${id}-phases`}
                  value={active.phases}
                  onChange={(event) => {
                    updateBoard(active.key, { phases: event.target.value as '1' | '3' });
                  }}
                  className="input w-full"
                >
                  <option value="3">3</option>
                  <option value="1">1</option>
                </select>
              </Field>
              {boardField('faultLevel', 'faultLevel', 'kA')}
              <Field id={`${id}-earthing`} label={t('field.earthing')}>
                <select
                  id={`${id}-earthing`}
                  dir="ltr"
                  value={active.earthing}
                  onChange={(event) => {
                    const earthing = EARTHING.find((value) => value === event.target.value);
                    if (earthing) updateBoard(active.key, { earthing });
                  }}
                  className="input w-full"
                >
                  {EARTHING.map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </select>
              </Field>
              {active.earthing !== 'TT' && boardField('earthLoop', 'earthLoop', 'Ω')}
              <Field id={`${id}-fed`} label={t('field.fedFrom')}>
                <select
                  id={`${id}-fed`}
                  value={active.fedFrom}
                  onChange={(event) => {
                    updateBoard(active.key, { fedFrom: event.target.value });
                  }}
                  className="input w-full"
                >
                  <option value="">{t('fedFromNone')}</option>
                  {boards
                    .filter((board) => board.key !== active.key && board.name.trim() !== '')
                    .map((board) => (
                      <option key={board.key} value={board.name}>
                        {board.name}
                      </option>
                    ))}
                </select>
              </Field>
              {active.fedFrom !== '' && boardField('feederLength', 'feederLength', 'm')}
              {boards.length > 1 && (
                <button
                  type="button"
                  data-testid="board-remove"
                  onClick={() => {
                    removeBoard(active.key);
                  }}
                  className="btn btn-sm btn-secondary self-end"
                >
                  {t('removeBoard')}
                </button>
              )}
            </fieldset>

            <fieldset className="card flex flex-col gap-4 p-4 md:p-5">
              <legend className="px-1 text-sm font-semibold">
                {t('loadsOf', { board: active.name || '—' })}
              </legend>
              <ScheduleSources
                key={active.key}
                token={token}
                supplyPhases={Number(active.phases)}
                profile={profile}
                importImpl={importImpl}
                suggestImpl={suggestImpl}
                onUnauthorized={() => {
                  void connect();
                }}
                onLoads={(loads) => {
                  const first = takeKeys(loads.length);
                  updateLoads(active.key, () => rowsFrom(loads, first));
                }}
              />
              <LoadRows
                idPrefix={`${id}-${String(active.key)}`}
                loads={active.loads}
                onChange={(key, patch) => {
                  updateLoads(active.key, (loads) =>
                    loads.map((load) => (load.key === key ? { ...load, ...patch } : load)),
                  );
                }}
                onRemove={(key) => {
                  updateLoads(active.key, (loads) => loads.filter((load) => load.key !== key));
                }}
                onAdd={() => {
                  const key = takeKeys(1);
                  updateLoads(active.key, (loads) => [...loads, blankLoad(key)]);
                }}
              />
            </fieldset>
          </>
        )}

        <Field id={`${id}-drawing-language`} label={t('drawingLanguage')}>
          <select
            id={`${id}-drawing-language`}
            value={drawingLanguage}
            onChange={(event) => {
              setDrawingLanguage(event.target.value as DrawingLanguage);
            }}
            className="input w-full sm:w-64"
          >
            {DRAWING_LANGUAGES.map((language) => (
              <option key={language} value={language}>
                {t(`drawingLanguages.${language}`)}
              </option>
            ))}
          </select>
        </Field>

        <details className="card p-4 md:p-5">
          <summary className="cursor-pointer text-sm font-semibold">{t('profile.title')}</summary>
          <p className="my-2 text-sm text-text-muted">{t('profile.help')}</p>
          <label htmlFor={`${id}-profile`} className="sr-only">
            {t('profile.title')}
          </label>
          <textarea
            id={`${id}-profile`}
            dir="ltr"
            rows={6}
            value={profileText}
            placeholder='{"key": "my-company", "name": "My Company", "max_circuits_per_rcd": 8}'
            onChange={(event) => {
              setProfileText(event.target.value);
            }}
            className="input w-full font-mono text-sm"
          />
        </details>

        <div>
          <button
            type="submit"
            disabled={token === null || !complete || result.kind === 'working'}
            className="btn btn-primary"
          >
            {result.kind === 'working' ? t('working') : t('submit')}
          </button>
        </div>
      </form>

      {result.kind === 'error' && (
        <p role="alert" className="mb-4 text-sm text-danger" data-testid="design-error">
          {say(result.outcome)}
        </p>
      )}

      {result.kind === 'designed' && (
        <DesignResult response={result.response} onExport={download} exportError={exportError} />
      )}

      {designed && token !== null && (
        <div className="mt-6">
          <QuotationPanel token={token} project={designed} profile={profile} saveImpl={saveImpl} />
        </div>
      )}

      {designed && token !== null && (
        <div className="mt-6">
          <MarkupsPanel token={token} project={designed} profile={profile} />
        </div>
      )}

      {designed && token !== null && hasPlcOutputs && (
        <div className="mt-6">
          <PlcPanel token={token} project={designed} profile={profile} saveImpl={saveImpl} />
        </div>
      )}
    </AppShell>
  );
}
