'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';

import { AppShell } from '@/components/app-shell';
import { Field } from '@/components/cable-sizing-panel';
import { CompanySettingsPanel } from '@/components/design/company-settings';
import { DrawingSheets, ExportPanel } from '@/components/design/design-result';
import { LoadRows } from '@/components/design/load-rows';
import { useOutcomeText } from '@/components/design/note-text';
import {
  applySuggestion,
  blankBoard,
  blankLoad,
  DEFAULT_PROFILE_KEY,
  DRAWING_LANGUAGES,
  EARTHING,
  fromRequest,
  IEC_SUPPLY,
  isComplete,
  profileFrom,
  rowsFrom,
  toRequest,
  withLanguage,
  type BoardForm,
  type DrawingLanguage,
  type Load,
  type ProjectInfo,
  type SupplyDefaults,
} from '@/components/design/schedule';
import { MarkupsPanel } from '@/components/design/markups-panel';
import {
  approvalOf,
  ProjectsPanel,
  type OpenedProject,
  type TitleRevision,
} from '@/components/design/projects-panel';
import { ScheduleSources } from '@/components/design/schedule-sources';
import {
  DEFAULT_VIEW,
  urlForView,
  useUnsavedChangesGuard,
  viewFromSearch,
  VIEWS,
  type View,
} from '@/components/design/workspace';
import { FolderIcon, LayersIcon, PlusIcon } from '@/components/icons';
import { PlcPanel } from '@/components/plc-panel';
import { QuotationPanel } from '@/components/quotation-panel';
import {
  EmptyState,
  LoadingState,
  Notice,
  PageHeader,
  Panel,
  SaveIndicator,
  StatusBadge,
  TableFrame,
  tabId,
  tabPanelId,
  Tabs,
  type SaveState,
} from '@/components/ui';
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
import { fetchMarkets, readMarket, storeMarket, withMarket, type MarketInfo } from '@/lib/market';
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

type RevisionSummary = SavedProject['revisions'][number];

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

/** A market's supply as a board's defaults; IEC 60364's without one. */
function supplyOf(market: MarketInfo | null): SupplyDefaults {
  if (market === null) return IEC_SUPPLY;
  const earthing = EARTHING.find((system) => system === market.earthing) ?? 'TN-S';
  return {
    voltage: String(market.voltage_v),
    earthing,
    frequency: market.frequency_hz === 60 ? '60' : '50',
  };
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
 * What the engineer has entered, as one comparable string. Row and board
 * keys are left out: opening a project numbers them afresh without changing
 * the work. The drawing language counts only once the engineer picks one,
 * since until then it follows the page's language.
 */
export function snapshotOf(
  info: ProjectInfo,
  boards: BoardForm[],
  profileText: string,
  language: DrawingLanguage | null,
  market: string,
): string {
  return JSON.stringify({ info, boards, profileText, language, market }, (key, value: unknown) =>
    key === 'key' ? undefined : value,
  );
}

/**
 * `/design`: a project workspace, from its load schedules to its drawing set.
 *
 * The workspace is split into views (`?view=`): the overview (saved projects
 * and the project's title-block details), the load schedule of each board
 * with the design basis, the designed sheets, the panel layout, materials,
 * revisions and the review and exports. Every view stays mounted while
 * hidden, so nothing typed in one is lost by moving to another. Every output
 * -- the PDF drawing set, DXF, QElectroTech, AutomationML, the CSV lists, the
 * quotation and the PLC program -- comes from the one designed project.
 */
export function DesignScreen({
  acquireImpl = acquireTrial,
  designImpl = designProject,
  exportImpl = exportDesign,
  importImpl = importSchedule,
  suggestImpl = suggestSchedule,
  saveImpl = saveBlob,
  marketsImpl = fetchMarkets,
  readMarketImpl = readMarket,
}: {
  acquireImpl?: typeof acquireTrial;
  designImpl?: typeof designProject;
  exportImpl?: typeof exportDesign;
  importImpl?: typeof importSchedule;
  suggestImpl?: typeof suggestSchedule;
  saveImpl?: typeof saveBlob;
  marketsImpl?: typeof fetchMarkets;
  readMarketImpl?: typeof readMarket;
}) {
  const t = useTranslations('design');
  const tw = useTranslations('workspace');
  const say = useOutcomeText();
  const id = useId();
  const formId = `${id}-form`;
  const tabsBase = `${id}-view`;
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
  const drawingLanguage: DrawingLanguage =
    chosenLanguage ?? DRAWING_LANGUAGES.find((language) => language === locale) ?? 'en';
  const [result, setResult] = useState<Result>({ kind: 'idle' });
  const [exportError, setExportError] = useState<string | null>(null);
  const [opened, setOpened] = useState<OpenedProject | null>(null);
  // The opened revision's title-block revision list, which the form does not edit.
  const [titleRevisions, setTitleRevisions] = useState<TitleRevision[]>([]);
  // The saved project's revision history, as the API listed it when opened or saved.
  const [history, setHistory] = useState<RevisionSummary[]>([]);
  // The market sets a new board's supply and the rules its code states; read
  // after the first render, from the engineer's last choice or the browser.
  const [market, setMarket] = useState('');
  const [markets, setMarkets] = useState<MarketInfo[]>([]);
  const [view, setView] = useState<View>(DEFAULT_VIEW);
  const [lastSave, setLastSave] = useState<'saving' | 'saved' | 'failed' | null>(null);

  const snapshot = snapshotOf(info, boards, profileText, chosenLanguage, market);
  // What the work looked like when last saved or opened (or when the page
  // started); anything else is unsaved.
  const [clean, setClean] = useState(snapshot);
  // Set when a change that is not the engineer's (the detected market) lands
  // on an untouched form, so that form is still untouched afterwards. Applied
  // during render, so the form never flashes as unsaved in between.
  const [rebaseline, setRebaseline] = useState(false);
  if (rebaseline) {
    setRebaseline(false);
    setClean(snapshot);
  }
  // Whether the work is untouched, for a callback that outlives the render.
  const pristine = useRef(true);
  const dirty = snapshot !== clean;
  useEffect(() => {
    pristine.current = !dirty;
  });
  useUnsavedChangesGuard(dirty);

  const saveState: SaveState =
    lastSave === 'saving'
      ? 'saving'
      : dirty
        ? lastSave === 'failed'
          ? 'failed'
          : 'unsaved'
        : opened
          ? 'saved'
          : 'idle';

  // The view named in the address, read after the first render (the server
  // render cannot see it) and again when the engineer goes back or forward.
  useEffect(() => {
    function read() {
      setView(viewFromSearch(window.location.search));
    }
    read();
    window.addEventListener('popstate', read);
    return () => {
      window.removeEventListener('popstate', read);
    };
  }, []);

  function selectView(next: View) {
    setView(next);
    window.history.replaceState(window.history.state, '', urlForView(window.location.href, next));
  }

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
  }, [acquireImpl]);

  useEffect(() => {
    void connect();
  }, [connect]);

  const marketInfo = markets.find((entry) => entry.code === market) ?? null;
  const supply = supplyOf(marketInfo);

  useEffect(() => {
    let live = true;
    void marketsImpl().then((listed) => {
      if (!live || listed === null) return;
      setMarkets(listed);
      const code = readMarketImpl();
      if (listed.some((entry) => entry.code === code)) chooseMarket(code, listed, false);
    });
    return () => {
      live = false;
    };
    // Once, on arrival: a later choice is the engineer's.
  }, [marketsImpl, readMarketImpl]);

  /** Use a market: its supply on every board, remembered when the engineer chose it. */
  function chooseMarket(code: string, listed: MarketInfo[], chosen: boolean) {
    // A detected market is not an edit: an untouched form stays untouched.
    if (!chosen && pristine.current) setRebaseline(true);
    setMarket(code);
    if (chosen) storeMarket(code);
    const defaults = supplyOf(listed.find((entry) => entry.code === code) ?? null);
    setBoards((current) => current.map((board) => ({ ...board, ...defaults })));
  }

  const token = session.kind === 'ready' ? session.token : null;
  const active = boards.find((board) => board.key === activeKey) ?? boards[0];
  const parsed = parseProfile(profileText);
  const profile = withMarket(
    withLanguage(parsed === 'invalid' ? null : parsed, drawingLanguage),
    market,
    DEFAULT_PROFILE_KEY,
  );

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
      blankBoard(key, key + 1, `DB${String(current.length + 1)}`, main, supply),
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
  // The form, not the session, is what keeps the button disabled.
  const incomplete = token !== null && !complete;

  function onOpened(project: SavedProject, fill: boolean) {
    setOpened({
      id: project.id,
      name: project.name,
      revision: project.revision,
      revisions: project.revisions.map((revision) => revision.number),
      approval: approvalOf(project),
    });
    setHistory(project.revisions);
    setTitleRevisions(project.request.info.revisions ?? []);
    if (!fill) {
      // Saved: what is on the page is what the project now holds.
      setClean(snapshot);
      return;
    }
    const loaded = fromRequest(project.request, nextKey.current);
    nextKey.current += Math.max(1, loaded.used);
    setInfo(loaded.info);
    setBoards(loaded.boards);
    if (loaded.boards[0]) setActiveKey(loaded.boards[0].key);
    const settings = profileFrom(project.request.profile);
    setProfileText(settings.text);
    setDrawingLanguage(settings.language);
    setClean(snapshotOf(loaded.info, loaded.boards, settings.text, settings.language, market));
    setLastSave(null);
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
    const request = toRequest(info, boards, profile);
    request.info.revisions = titleRevisions;
    const outcome = await designImpl({ token, request });
    if (outcome.kind === 'designed') {
      setResult({ kind: 'designed', response: outcome.response });
    } else {
      // The schedule stays as entered, so the engineer can correct and retry.
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
          form={formId}
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
  const sheetCount = designed?.boards?.length ?? 0;
  // The PLC drives every contactor and every drive.
  const hasPlcOutputs = (designed?.boards ?? []).some((board) =>
    (board.devices ?? []).some((device) => device.kind === 'contactor' || device.kind === 'drive'),
  );
  const projectName = info.name.trim() || opened?.name || '';

  /** A view's panel: mounted always, shown only while its tab is selected. */
  function viewPanel(key: View, children: ReactNode) {
    return (
      <div
        key={key}
        role="tabpanel"
        id={tabPanelId(tabsBase, key)}
        aria-labelledby={tabId(tabsBase, key)}
        hidden={view !== key}
        data-testid={`view-${key}`}
      >
        {/* Layout lives inside: a display class on the panel itself would
            override the hidden attribute and show every view at once. */}
        <div className="flex flex-col gap-5">{children}</div>
      </div>
    );
  }

  /** A button that takes the engineer to another view. */
  function goTo(key: View, label: string) {
    return (
      <button
        type="button"
        className="btn btn-sm btn-secondary"
        onClick={() => {
          selectView(key);
        }}
      >
        {label}
      </button>
    );
  }

  const designFirst = (
    <EmptyState
      icon={<LayersIcon />}
      title={tw('designFirst.title')}
      action={goTo('schedule', tw('designFirst.action'))}
    >
      {result.kind === 'working' ? null : tw('designFirst.body')}
    </EmptyState>
  );

  const context = (
    <nav aria-label={tw('context.label')} className="flex min-w-0 items-center gap-2">
      <FolderIcon width="16" height="16" aria-hidden="true" className="shrink-0" />
      <button
        type="button"
        className="min-w-0 truncate font-medium text-text hover:underline"
        onClick={() => {
          selectView('overview');
        }}
      >
        {projectName ? <bdi>{projectName}</bdi> : tw('context.untitled')}
      </button>
      {info.number.trim() && (
        <bdi dir="ltr" className="shrink-0 font-mono text-xs">
          {info.number.trim()}
        </bdi>
      )}
      {opened && (
        <StatusBadge tone="neutral">
          {tw('context.revision', { revision: opened.revision })}
        </StatusBadge>
      )}
      {opened && (
        <StatusBadge tone={opened.approval ? 'success' : 'warning'}>
          {opened.approval ? tw('context.approved') : tw('context.draft')}
        </StatusBadge>
      )}
    </nav>
  );

  const tabs = VIEWS.map((key) => ({
    key,
    label: tw(`views.${key}`),
    ...(key === 'schedule' ? { badge: String(boards.length) } : {}),
    ...(key === 'drawings' && sheetCount > 0 ? { badge: String(sheetCount) } : {}),
  }));

  return (
    <AppShell wide context={context}>
      <PageHeader
        title={t('title')}
        description={t('intro')}
        meta={
          <SaveIndicator
            state={saveState}
            labels={{
              unsaved: tw('save.unsaved'),
              saving: tw('save.saving'),
              saved: tw('save.saved'),
              failed: tw('save.failed'),
            }}
          />
        }
        actions={
          <div className="flex flex-col items-start gap-1">
            <button
              type="submit"
              form={formId}
              disabled={token === null || !complete || result.kind === 'working'}
              aria-describedby={incomplete ? `${id}-incomplete` : undefined}
              className="btn btn-primary"
            >
              {result.kind === 'working' ? t('working') : t('submit')}
            </button>
            {/* A disabled button says nothing about why. Said only for what the
                engineer can fix: a session still starting is not their doing. */}
            {incomplete && (
              <p
                id={`${id}-incomplete`}
                className="max-w-xs text-sm text-text-muted"
                data-testid="design-incomplete"
              >
                {t('incomplete')}
              </p>
            )}
          </div>
        }
      />

      <div className="flex flex-col gap-4">
        {session.kind !== 'ready' && session.kind !== 'starting' && (
          <Notice
            tone="critical"
            action={
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                onClick={() => {
                  void connect();
                }}
              >
                {tw('retry')}
              </button>
            }
          >
            {t('unavailable')}
          </Notice>
        )}
        {dirty && lastSave === 'failed' && (
          <Notice tone="warning" live={false}>
            {tw('save.failedHint')}
          </Notice>
        )}
        {result.kind === 'error' && (
          <Notice tone="critical" testId="design-error">
            {say(result.outcome)}
          </Notice>
        )}
        {result.kind === 'designed' && view !== 'drawings' && (
          <Notice tone="success" action={goTo('drawings', tw('designed.action'))}>
            {tw('designed.body', { count: sheetCount })}
          </Notice>
        )}

        <div className="max-w-full overflow-x-auto">
          <Tabs
            items={tabs}
            selected={view}
            onSelect={selectView}
            label={tw('views.label')}
            idBase={tabsBase}
          />
        </div>

        {viewPanel(
          'overview',
          <>
            <Panel title={t('project')}>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
                {infoField('name')}
                {infoField('number', true)}
                {infoField('customer')}
                {infoField('consultant')}
                {infoField('contractor')}
              </div>
            </Panel>
            {token !== null ? (
              <ProjectsPanel
                token={token}
                current={
                  complete && parsed !== 'invalid'
                    ? { name: info.name.trim(), request: toRequest(info, boards, profile) }
                    : null
                }
                opened={opened}
                onOpened={onOpened}
                onSaveState={setLastSave}
              />
            ) : (
              session.kind === 'starting' && <LoadingState>{tw('connecting')}</LoadingState>
            )}
          </>,
        )}

        <div
          role="tabpanel"
          id={tabPanelId(tabsBase, 'schedule')}
          aria-labelledby={tabId(tabsBase, 'schedule')}
          hidden={view !== 'schedule'}
          data-testid="view-schedule"
        >
          <form
            id={formId}
            onSubmit={(event) => {
              event.preventDefault();
              void run();
            }}
            className="flex flex-col gap-5"
          >
            <div
              className="flex flex-wrap items-center gap-2"
              role="group"
              aria-label={t('boards')}
            >
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
                <PlusIcon width="16" height="16" aria-hidden="true" />
                {t('addBoard')}
              </button>
            </div>

            {active && (
              <>
                <fieldset className="panel grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-6">
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
                      className="btn btn-sm btn-danger self-end"
                    >
                      {t('removeBoard')}
                    </button>
                  )}
                </fieldset>

                <fieldset className="panel flex min-w-0 flex-col gap-4 p-4 md:p-5">
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

            <fieldset className="panel flex flex-col gap-4 p-4 md:p-5">
              <legend className="px-1 text-sm font-semibold">{tw('basis')}</legend>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <Field id={`${id}-market`} label={t('market')}>
                  <select
                    id={`${id}-market`}
                    value={market}
                    onChange={(event) => {
                      chooseMarket(event.target.value, markets, true);
                    }}
                    className="input w-full"
                  >
                    <option value="">{t('markets.iec')}</option>
                    {markets.map((entry) => (
                      <option key={entry.code} value={entry.code}>
                        {t.has(`markets.${entry.code}`) ? t(`markets.${entry.code}`) : entry.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field id={`${id}-drawing-language`} label={t('drawingLanguage')}>
                  <select
                    id={`${id}-drawing-language`}
                    value={drawingLanguage}
                    onChange={(event) => {
                      setDrawingLanguage(event.target.value as DrawingLanguage);
                    }}
                    className="input w-full"
                  >
                    {DRAWING_LANGUAGES.map((language) => (
                      <option key={language} value={language}>
                        {t(`drawingLanguages.${language}`)}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {marketInfo && (
                <p className="text-sm text-text-muted" data-testid="market-basis">
                  {t('marketBasis', {
                    voltage: marketInfo.voltage_v,
                    frequency: marketInfo.frequency_hz,
                    earthing: marketInfo.earthing,
                    regulation: marketInfo.regulation,
                  })}
                </p>
              )}
              <details className="rounded-md border border-border-subtle p-4">
                <summary className="cursor-pointer text-sm font-semibold">
                  {t('profile.title')}
                </summary>
                <div className="mt-3">
                  <CompanySettingsPanel
                    token={token}
                    text={profileText}
                    onText={setProfileText}
                    onLoaded={(settings) => {
                      const loaded = profileFrom(settings);
                      setProfileText((current) => (current === '' ? loaded.text : current));
                      if (loaded.language) setDrawingLanguage(loaded.language);
                    }}
                    language={drawingLanguage}
                  />
                </div>
              </details>
            </fieldset>
          </form>
        </div>

        {viewPanel(
          'drawings',
          result.kind === 'designed' ? (
            <DrawingSheets response={result.response} />
          ) : result.kind === 'working' ? (
            <LoadingState>{t('working')}</LoadingState>
          ) : (
            designFirst
          ),
        )}

        {viewPanel(
          'layout',
          <EmptyState
            icon={<LayersIcon />}
            title={tw('layout.title')}
            action={goTo('review', tw('layout.action'))}
          >
            {tw('layout.body')}
          </EmptyState>,
        )}

        {viewPanel(
          'materials',
          designed && token !== null ? (
            <QuotationPanel
              token={token}
              project={designed}
              profile={profile}
              saveImpl={saveImpl}
              {...(marketInfo ? { currency: marketInfo.currency } : {})}
            />
          ) : (
            designFirst
          ),
        )}

        {viewPanel(
          'revisions',
          <>
            <Panel title={tw('revisions.history')}>
              {opened && history.length > 0 ? (
                <TableFrame label={tw('revisions.history')}>
                  <table className="data-table w-full text-sm" data-testid="revision-history">
                    <thead>
                      <tr>
                        <th className="p-2 text-start">{tw('revisions.number')}</th>
                        <th className="p-2 text-start">{tw('revisions.note')}</th>
                        <th className="p-2 text-start">{tw('revisions.author')}</th>
                        <th className="p-2 text-start">{tw('revisions.date')}</th>
                        <th className="p-2 text-start">{tw('revisions.approval')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((entry) => (
                        <tr
                          key={entry.number}
                          aria-current={entry.number === opened.revision ? 'true' : undefined}
                        >
                          <td className="p-2 font-mono" dir="ltr">
                            {entry.number}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.note || '—'}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.author || '—'}
                          </td>
                          <td className="p-2 font-mono" dir="ltr">
                            {entry.created_at.slice(0, 10)}
                          </td>
                          <td className="p-2">
                            {entry.approved_by && entry.approved_at ? (
                              <StatusBadge tone="success">
                                {tw('revisions.approvedBy', {
                                  name: entry.approved_by,
                                  date: entry.approved_at.slice(0, 10),
                                })}
                              </StatusBadge>
                            ) : (
                              <StatusBadge tone="warning">{tw('context.draft')}</StatusBadge>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableFrame>
              ) : (
                <EmptyState
                  title={tw('revisions.noneTitle')}
                  action={goTo('overview', tw('revisions.noneAction'))}
                >
                  {tw('revisions.noneBody')}
                </EmptyState>
              )}
            </Panel>
            {titleRevisions.length > 0 && (
              <Panel title={tw('revisions.titleBlock')}>
                <TableFrame label={tw('revisions.titleBlock')}>
                  <table className="data-table w-full text-sm">
                    <thead>
                      <tr>
                        <th className="p-2 text-start">{tw('revisions.index')}</th>
                        <th className="p-2 text-start">{tw('revisions.date')}</th>
                        <th className="p-2 text-start">{tw('revisions.description')}</th>
                        <th className="p-2 text-start">{tw('revisions.drawn')}</th>
                        <th className="p-2 text-start">{tw('revisions.checked')}</th>
                        <th className="p-2 text-start">{tw('revisions.approved')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {titleRevisions.map((entry, index) => (
                        <tr key={`${entry.index}-${String(index)}`}>
                          <td className="p-2 font-mono" dir="ltr">
                            {entry.index}
                          </td>
                          <td className="p-2 font-mono" dir="ltr">
                            {entry.date}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.description}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.drawn_by}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.checked_by}
                          </td>
                          <td className="p-2" dir="auto">
                            {entry.approved_by}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableFrame>
              </Panel>
            )}
            {designed && token !== null ? (
              <MarkupsPanel
                token={token}
                project={designed}
                profile={profile}
                onApply={(suggestion) => {
                  const changed = applySuggestion(boards, suggestion);
                  if (changed === null) return false;
                  setBoards(changed);
                  return true;
                }}
              />
            ) : (
              <Notice tone="info" live={false}>
                {tw('revisions.markupsAfterDesign')}
              </Notice>
            )}
          </>,
        )}

        {viewPanel(
          'review',
          <>
            <Panel title={tw('review.approval')}>
              {opened ? (
                opened.approval ? (
                  <Notice tone="success" live={false}>
                    {t('projects.approvedBy', {
                      revision: opened.revision,
                      name: opened.approval.by,
                      date: opened.approval.at.slice(0, 10),
                    })}
                  </Notice>
                ) : (
                  <Notice
                    tone="warning"
                    live={false}
                    action={goTo('overview', tw('review.approveAction'))}
                  >
                    {tw('review.notApproved', { revision: opened.revision })}
                  </Notice>
                )
              ) : (
                <Notice tone="info" live={false} action={goTo('overview', tw('review.saveAction'))}>
                  {tw('review.notSaved')}
                </Notice>
              )}
            </Panel>
            <Panel title={t('downloads')}>
              {result.kind === 'designed' ? (
                <ExportPanel onExport={download} exportError={exportError} />
              ) : (
                designFirst
              )}
            </Panel>
            {designed && token !== null && hasPlcOutputs && (
              <PlcPanel token={token} project={designed} profile={profile} saveImpl={saveImpl} />
            )}
          </>,
        )}
      </div>
    </AppShell>
  );
}
