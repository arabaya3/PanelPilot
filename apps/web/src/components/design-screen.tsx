'use client';

import { useLocale, useTranslations } from 'next-intl';
import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';

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
import {
  executionBlockersOf,
  executionRefusalOf,
  readinessOf,
  rowOf,
  validationOf,
  type ExecutionRefusal,
  type RevisionValidation,
} from '@/components/design/readiness';
import {
  ExecutionRefused,
  FindingsList,
  ReadinessSteps,
} from '@/components/design/revision-status';
import { ScheduleSources } from '@/components/design/schedule-sources';
import {
  clearDraft,
  draftBoards,
  formBoards,
  readDraft,
  writeDraft,
  type DesignDraft,
} from '@/components/design/draft';
import {
  DEFAULT_VIEW,
  pushView,
  useUnsavedChangesGuard,
  viewFromSearch,
  VIEWS,
  type View,
} from '@/components/design/workspace';
import { FolderIcon, LayersIcon, PlusIcon } from '@/components/icons';
import { PlcPanel } from '@/components/plc-panel';
import { QuotationPanel } from '@/components/quotation-panel';
import {
  Dialog,
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
  exportRevision,
  importSchedule,
  openProject,
  suggestSchedule,
  type BoardDesignResponse,
  type SavedProject,
  type DesignOutcome,
  type ExportFormat,
  type ExportPurpose,
  type TooLong,
} from '@/lib/design';
import { fetchMarkets, readMarket, storeMarket, withMarket, type MarketInfo } from '@/lib/market';
import { acquireTrial } from '@/lib/session';
import { readTrial } from '@/lib/trial';

type Session =
  | { kind: 'starting' }
  | { kind: 'ready'; token: string }
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

type Result =
  | { kind: 'idle' }
  | { kind: 'working' }
  | {
      kind: 'designed';
      response: BoardDesignResponse;
      /** The settings it was designed with, so its exports match what is shown. */
      profile: Record<string, unknown> | null;
      /** What the inputs were when it was designed, to tell when it no longer matches them. */
      basis: string;
    }
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
/**
 * The most a refused text field takes, when the server refused the one at
 * `path` (`info.name`) as too long, under whichever request it was sent in.
 */
export function tooLongAt(refused: readonly TooLong[], path: string): number | null {
  const hit = refused.find((entry) => entry.path === path || entry.path.endsWith(`.${path}`));
  return hit ? hit.max : null;
}

export function DesignScreen({
  acquireImpl = acquireTrial,
  designImpl = designProject,
  exportImpl = exportDesign,
  exportRevisionImpl = exportRevision,
  importImpl = importSchedule,
  suggestImpl = suggestSchedule,
  saveImpl = saveBlob,
  marketsImpl = fetchMarkets,
  readMarketImpl = readMarket,
  openImpl = openProject,
  leaveImpl,
}: {
  /** Reopens the saved project a restored draft belongs to. */
  openImpl?: typeof openProject;
  acquireImpl?: typeof acquireTrial;
  designImpl?: typeof designProject;
  exportImpl?: typeof exportDesign;
  exportRevisionImpl?: typeof exportRevision;
  importImpl?: typeof importSchedule;
  suggestImpl?: typeof suggestSchedule;
  saveImpl?: typeof saveBlob;
  marketsImpl?: typeof fetchMarkets;
  readMarketImpl?: typeof readMarket;
  /** Follows a link the engineer chose to leave for with changes discarded. */
  leaveImpl?: (href: string) => void;
}) {
  const t = useTranslations('design');
  const tw = useTranslations('workspace');
  const tUi = useTranslations('ui');
  const say = useOutcomeText();
  const id = useId();
  const formId = `${id}-form`;
  const tabsBase = `${id}-view`;
  const [session, setSession] = useState<Session>({ kind: 'starting' });
  const [trialId, setTrialId] = useState<string | null>(null);
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
  // What the server recorded for the revision open: validated or not, its findings.
  const [validation, setValidation] = useState<RevisionValidation | null>(null);
  // The server said the rules or company profile changed since the revision open.
  const [revisionStale, setRevisionStale] = useState(false);
  const [revisionError, setRevisionError] = useState<string | null>(null);
  const [executionRefusal, setExecutionRefusal] = useState<ExecutionRefusal | null>(null);
  // The load row a finding's "go to row" asked for, focused once it is shown.
  const pendingFocus = useRef<string | null>(null);
  // The market sets a new board's supply and the rules its code states; read
  // after the first render, from the engineer's last choice or the browser.
  const [market, setMarket] = useState('');
  const [markets, setMarkets] = useState<MarketInfo[]>([]);
  const [view, setView] = useState<View>(DEFAULT_VIEW);
  const [lastSave, setLastSave] = useState<'saving' | 'saved' | 'failed' | null>(null);
  // Project fields the server refused as too long, said at the field; the
  // entered text stays, so it can be shortened.
  const [tooLong, setTooLong] = useState<TooLong[]>([]);

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
  // Set once a draft is restored, so a market detected afterwards does not
  // overwrite the supply the draft brought back.
  const restoredDraft = useRef(false);
  const dirty = snapshot !== clean;
  // A layout effect, so a market detected straight after a keystroke cannot
  // read the state from before it.
  useLayoutEffect(() => {
    pristine.current = !dirty;
  });
  const unsaved = useUnsavedChangesGuard(dirty, leaveImpl);

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
    pushView(next);
  }

  const connect = useCallback(async () => {
    setSession({ kind: 'starting' });
    const outcome = await acquireImpl();
    setSession(outcome.kind === 'ready' ? { kind: 'ready', token: outcome.accessToken } : outcome);
    // The trial the work is typed in, which a draft is kept for. When the
    // trial cannot be resumed just now (rate limited, offline) it is still the
    // one this browser holds, so work typed meanwhile is kept for it too.
    setTrialId(
      outcome.kind === 'ready' ? outcome.trial.sessionId : (readTrial()?.sessionId ?? null),
    );
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
      // A restored draft brings its own market and supply.
      if (restoredDraft.current) return;
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

  // Unsaved work is kept as a draft for this tab and session, for the ways of
  // leaving the guard cannot hold. On arrival, a draft left by an earlier
  // visit is offered back; until the session is known and that is settled,
  // nothing is written or cleared, so the page starting clean cannot wipe a
  // draft it is about to offer.
  const [draftChecked, setDraftChecked] = useState<string | null>(null);
  const [draftOffer, setDraftOffer] = useState<DesignDraft | null>(null);
  const [draftProjectGone, setDraftProjectGone] = useState(false);
  const [restoring, setRestoring] = useState(false);
  // The snapshot the engineer chose to discard: not kept unless edited again.
  const discardedSnapshot = useRef<string | null>(null);
  const lastWritten = useRef<string | null>(null);
  useEffect(() => {
    if (trialId === null || draftChecked === trialId) return;
    setDraftOffer(readDraft(trialId));
    setDraftChecked(trialId);
  }, [trialId, draftChecked]);
  // Every render: cheap next to the snapshot, and only written when changed.
  useEffect(() => {
    if (trialId === null || draftChecked !== trialId || draftOffer !== null) return;
    if (!dirty || discardedSnapshot.current === snapshot) {
      if (lastWritten.current !== '') clearDraft();
      lastWritten.current = '';
      return;
    }
    const draft: DesignDraft = {
      session: trialId,
      project: opened ? { id: opened.id, revision: opened.revision } : null,
      clean,
      info,
      boards: draftBoards(boards),
      profileText,
      language: chosenLanguage,
      market,
    };
    const text = JSON.stringify(draft);
    if (text === lastWritten.current) return;
    if (writeDraft(draft)) lastWritten.current = text;
    else console.warn('Unsaved work could not be kept in session storage.');
  });

  async function restoreDraft(draft: DesignDraft) {
    // The offer (and so the stored draft) stays until the work is back on the page.
    setRestoring(true);
    restoredDraft.current = true;
    let reopened = draft.project === null;
    if (draft.project !== null && token !== null) {
      const outcome = await openImpl({
        token,
        id: draft.project.id,
        revision: draft.project.revision,
      });
      if (outcome.kind === 'saved') {
        onOpened(outcome.project, false);
        reopened = true;
      }
    }
    setDraftProjectGone(!reopened);
    setRestoring(false);
    setDraftOffer(null);
    const keyed = formBoards(draft.boards, nextKey.current);
    nextKey.current += Math.max(1, keyed.used);
    setInfo(draft.info);
    setBoards(keyed.boards);
    if (keyed.boards[0]) setActiveKey(keyed.boards[0].key);
    setProfileText(draft.profileText);
    setDrawingLanguage(draft.language);
    setMarket(draft.market);
    setClean(draft.clean);
    setLastSave(null);
    setResult({ kind: 'idle' });
    setExportError(null);
  }

  function dismissDraft() {
    setDraftOffer(null);
    clearDraft();
  }

  function discardChanges() {
    discardedSnapshot.current = snapshot;
    clearDraft();
    lastWritten.current = '';
    unsaved.discard();
  }
  const active = boards.find((board) => board.key === activeKey) ?? boards[0];
  const parsed = parseProfile(profileText);
  const profile = withMarket(
    withLanguage(parsed === 'invalid' ? null : parsed, drawingLanguage),
    market,
    DEFAULT_PROFILE_KEY,
  );
  // Everything a design is made from: the inputs, the language as it is used,
  // and the title block's revisions, which an approval changes.
  const basis = `${snapshot}|${drawingLanguage}|${JSON.stringify(titleRevisions)}`;

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
    // A save, an approval or an opening each answer with the revision as now
    // stored: its validation replaces whatever was known of the last one.
    setValidation(validationOf(project));
    setRevisionStale(false);
    setRevisionError(null);
    setExecutionRefusal(null);
    setDraftProjectGone(false);
    setTitleRevisions(project.request.info.revisions ?? []);
    // Saved or approved: the form is left as it is. Only a save rebaselines
    // it (in `onSaveState`); an approval signs the stored revision, not
    // whatever has been typed since.
    if (!fill) return;
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
    setTooLong(outcome.kind === 'refused' ? (outcome.tooLong ?? []) : []);
    if (outcome.kind === 'designed') {
      setResult({ kind: 'designed', response: outcome.response, profile, basis });
    } else {
      // The schedule stays as entered, so the engineer can correct and retry.
      setResult({ kind: 'error', outcome });
      if (outcome.kind === 'unauthorized') void connect();
    }
  }

  async function download(format: ExportFormat) {
    // A design the inputs have moved on from is not exported as if current.
    if (token === null || result.kind !== 'designed' || result.basis !== basis) return;
    setExportError(null);
    const outcome = await exportImpl({
      token,
      project: result.response.project,
      format,
      profile: result.profile,
    });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
    } else {
      if (outcome.kind === 'refused') setTooLong(outcome.tooLong ?? []);
      setExportError(say(outcome));
    }
  }

  /**
   * Export the revision open as the server stored it. Only while the page
   * holds exactly that revision: an export of it would not show the edits.
   */
  async function downloadRevision(format: ExportFormat, purpose: ExportPurpose = 'review') {
    if (token === null || opened === null || dirty) return;
    setRevisionError(null);
    setExecutionRefusal(null);
    const outcome = await exportRevisionImpl({
      token,
      id: opened.id,
      revision: opened.revision,
      format,
      purpose,
    });
    if (outcome.kind === 'exported') {
      saveImpl(outcome.blob, outcome.filename);
      return;
    }
    if (outcome.kind === 'unauthorized') void connect();
    if (outcome.kind === 'refused' && outcome.code === 'execution_package_incomplete') {
      setExecutionRefusal(executionRefusalOf(outcome));
      return;
    }
    if (outcome.kind === 'refused' && outcome.code === 'revision_stale') setRevisionStale(true);
    if (outcome.kind === 'refused' && outcome.code === 'revision_not_validated') {
      setValidation((current) => (current ? { ...current, validated: false } : current));
    }
    setRevisionError(say(outcome));
  }

  /** Show a finding's board, and its load row when it names one. */
  function jumpTo(boardName: string, row: number | null) {
    const board = boards.find((entry) => entry.name === boardName);
    selectView('schedule');
    if (!board) return;
    setActiveKey(board.key);
    const load = row === null ? undefined : board.loads[row];
    pendingFocus.current = load
      ? `${id}-${String(board.key)}-desc-${String(load.key)}`
      : `${id}-board-name`;
  }

  // Once the schedule shows the board asked for, focus the row (or board) found.
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    pendingFocus.current = null;
    // Focusing scrolls the field into view, too.
    document.getElementById(target)?.focus();
  });

  function infoField(key: keyof ProjectInfo, ltr = false) {
    // The project is saved under its name, too, which the save checks on its own.
    const limit =
      tooLongAt(tooLong, `info.${key}`) ??
      (key === 'name' ? (tooLong.find((entry) => entry.path === 'name')?.max ?? null) : null);
    return (
      <Field
        id={`${id}-${key}`}
        label={t(`field.${key}`)}
        error={limit === null ? undefined : t('errors.too_long_here', { max: limit })}
      >
        <input
          id={`${id}-${key}`}
          aria-invalid={limit === null ? undefined : true}
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
  const designedProfile = result.kind === 'designed' ? result.profile : profile;
  // The inputs have changed since the design shown was made.
  const stale = result.kind === 'designed' && result.basis !== basis;
  // Where the saved revision stands; edits since make its validation and approval stale.
  const readiness = readinessOf({ opened, validation, dirty, stale: revisionStale });
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

  const designAgain = (
    <EmptyState
      icon={<LayersIcon />}
      title={tw('stale.title')}
      action={
        <button type="submit" form={formId} className="btn btn-sm btn-secondary">
          {tw('stale.action')}
        </button>
      }
    >
      {tw('stale.outputs')}
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
        // Approved only while the approval signs what is stored and nothing has changed since.
        <StatusBadge
          tone={readiness.approved === 'done' ? 'success' : 'warning'}
          testId="context-approval"
        >
          {readiness.approved === 'done'
            ? tw('context.approved')
            : opened.approval
              ? tw('context.approvalOutdated')
              : tw('context.draft')}
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
            {session.kind === 'rate-limited' ? t('rateLimited') : t('unavailable')}
          </Notice>
        )}
        {draftOffer !== null && (
          <Notice
            tone="warning"
            title={tw('draft.title')}
            testId="draft-offer"
            action={
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  // A draft of a saved project waits for the session that can reopen it.
                  disabled={restoring || (draftOffer.project !== null && token === null)}
                  onClick={() => {
                    const offered = draftOffer;
                    unsaved.guard(() => {
                      void restoreDraft(offered);
                    });
                  }}
                >
                  {tw('draft.restore')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  disabled={restoring}
                  onClick={dismissDraft}
                >
                  {tw('draft.dismiss')}
                </button>
              </div>
            }
          >
            {tw('draft.body')}
          </Notice>
        )}
        {draftProjectGone && (
          <Notice tone="warning" live={false} testId="draft-project-gone">
            {tw('draft.projectGone')}
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
        {stale && (
          <Notice
            tone="warning"
            testId="design-stale"
            action={
              <button type="submit" form={formId} className="btn btn-sm btn-secondary">
                {tw('stale.action')}
              </button>
            }
          >
            {tw('stale.body')}
          </Notice>
        )}
        {result.kind === 'designed' && !stale && view !== 'drawings' && (
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
                beforeOpen={unsaved.guard}
                dirty={dirty}
                blockers={readiness.approvalBlockers}
                blockingCount={readiness.blockingCount}
                approvalCovers={readiness.approvalCovers}
                onRefused={(refusal) => {
                  setTooLong(refusal.tooLong ?? []);
                }}
                onSaveState={(state) => {
                  setLastSave(state);
                  if (state === 'saved') setTooLong([]);
                  // This closure is the one the save started from, so its
                  // snapshot is exactly the request that was stored.
                  if (state === 'saved') setClean(snapshot);
                }}
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
              data-testid="board-tabs"
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
                <fieldset
                  className="panel grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 md:p-5 lg:grid-cols-6"
                  data-testid="board-details"
                >
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
          designed && token !== null && !stale ? (
            <QuotationPanel
              token={token}
              project={designed}
              profile={designedProfile}
              saveImpl={saveImpl}
              {...(marketInfo ? { currency: marketInfo.currency } : {})}
            />
          ) : stale ? (
            designAgain
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
                profile={designedProfile}
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
            <Panel title={t('readiness.title')}>
              <div className="flex flex-col gap-3">
                <ReadinessSteps readiness={readiness} />
                {opened && dirty && (
                  <Notice tone="warning" live={false} testId="readiness-dirty">
                    {t('readiness.dirty', { revision: opened.revision })}
                  </Notice>
                )}
                {opened && readiness.approvalBlockers.includes('stale') && (
                  <Notice tone="warning" live={false} testId="readiness-stale">
                    {t('errors.revision_stale')}
                  </Notice>
                )}
                {opened && validation !== null && !validation.validated && (
                  <Notice tone="warning" live={false} testId="readiness-not-validated">
                    {t('readiness.notValidated', { revision: opened.revision })}
                  </Notice>
                )}
                <p className="text-sm text-text-muted" data-testid="readiness-execution-note">
                  {t('readiness.executionBlocked')}
                </p>
              </div>
            </Panel>
            {opened && validation?.validated && (
              <Panel title={t('findings.title')}>
                <FindingsList
                  findings={validation.findings}
                  outdated={dirty}
                  onJump={jumpTo}
                  describe={(finding) => {
                    const row = rowOf(finding);
                    const board = boards.find((entry) => entry.name === finding.board_id);
                    const load = row === null ? undefined : board?.loads[row];
                    return load?.description.trim() || null;
                  }}
                />
              </Panel>
            )}
            <Panel title={tw('review.approval')}>
              {opened ? (
                opened.approval ? (
                  <Notice tone={readiness.approved === 'done' ? 'success' : 'warning'} live={false}>
                    {t('projects.approvedBy', {
                      revision: opened.revision,
                      name: opened.approval.by,
                      date: opened.approval.at.slice(0, 10),
                    })}
                    {!readiness.approvalCovers &&
                      ` ${t('projects.approvalNotCovering', { revision: opened.revision })}`}
                    {dirty && ` ${t('projects.approvalChanged', { revision: opened.revision })}`}
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
            {opened && (
              <Panel title={t('exports.revisionTitle', { revision: opened.revision })}>
                <div className="flex flex-col gap-3" data-testid="revision-export">
                  {dirty ? (
                    <Notice tone="warning" live={false} testId="revision-export-dirty">
                      {t('exports.revisionDirty', { revision: opened.revision })}
                    </Notice>
                  ) : validation !== null && !validation.validated ? (
                    <Notice tone="warning" live={false} testId="revision-export-not-validated">
                      {t('readiness.notValidated', { revision: opened.revision })}
                    </Notice>
                  ) : (
                    <>
                      <p className="text-sm text-text-muted">
                        {t('exports.revisionBody', { revision: opened.revision })}
                      </p>
                      <ExportPanel
                        onExport={(format) => downloadRevision(format)}
                        exportError={revisionError}
                        testIdPrefix="revision-export"
                      />
                      <div className="flex flex-col gap-2 border-t border-border-subtle pt-3">
                        <p className="text-sm text-text-muted">{t('exports.executionBody')}</p>
                        <div>
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            data-testid="execution-export"
                            onClick={() => {
                              void downloadRevision('pdf', 'execution');
                            }}
                          >
                            {t('exports.execution')}
                          </button>
                        </div>
                        {executionRefusal ? (
                          <ExecutionRefused refusal={executionRefusal} />
                        ) : (
                          validation !== null && (
                            <ExecutionRefused
                              refusal={executionBlockersOf(validation)}
                              refused={false}
                            />
                          )
                        )}
                      </div>
                    </>
                  )}
                </div>
              </Panel>
            )}
            <Panel title={t('exports.draftTitle')}>
              {result.kind === 'designed' && !stale ? (
                <div className="flex flex-col gap-3" data-testid="draft-export">
                  <Notice tone="warning" live={false} title={t('exports.draftMark')}>
                    {t('exports.draftBody')}
                  </Notice>
                  <ExportPanel onExport={download} exportError={exportError} />
                </div>
              ) : stale ? (
                designAgain
              ) : (
                designFirst
              )}
            </Panel>
            {designed && token !== null && !stale && hasPlcOutputs && (
              <PlcPanel
                token={token}
                project={designed}
                profile={designedProfile}
                saveImpl={saveImpl}
              />
            )}
          </>,
        )}
      </div>

      <Dialog
        open={unsaved.asking}
        onClose={unsaved.stay}
        title={tw('leave.title')}
        closeLabel={tUi('close')}
        footer={
          <div className="flex flex-wrap justify-end gap-2" data-testid="unsaved-dialog">
            <button type="button" className="btn btn-sm btn-secondary" onClick={unsaved.stay}>
              {tw('leave.stay')}
            </button>
            <button type="button" className="btn btn-sm btn-danger" onClick={discardChanges}>
              {tw('leave.discard')}
            </button>
          </div>
        }
      >
        <p className="text-sm">{tw('leave.body')}</p>
      </Dialog>
    </AppShell>
  );
}
