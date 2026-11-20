'use client';

import { useLocale } from 'next-intl';
import { createPortal } from 'react-dom';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { Dialog } from '@/components/ui';
import { useIsolatingTranslations } from '@/components/design/bidi';
import { useOutcomeText } from '@/components/design/note-text';
import {
  listOf,
  type ApprovalBlocker,
  type ApprovalPolicyPart,
} from '@/components/design/readiness';
import { ApprovalScope, SafetyConcerns } from '@/components/design/safety-concerns';
import {
  approveRevision,
  deleteProject,
  listProjects,
  openProject,
  permits,
  resaveRevision,
  resolveSafetyConcern,
  reviseProject,
  type ReferenceConfirmation,
  saveProject,
  type ProjectDesignRequest,
  type ProjectPage,
  type Refusal,
  type RequirementsInput,
  type LoadRef,
  type SafetyConcern,
  type SafetyResolutionEligibility,
  type SafetyResolutionKind,
  type SavedProject,
} from '@/lib/design';

/** Approval refusals after which the revision is read again, so the page shows the server's state. */
const REOPEN_ON = new Set([
  'revision_already_approved',
  'revision_stale',
  'revision_not_validated',
  // The account's permissions changed since the revision was opened.
  'approval_not_permitted',
  'approval_author_not_established',
]);

/** The project being worked on, once saved or opened. */
export type OpenedProject = {
  id: string;
  name: string;
  revision: number;
  revisions: number[];
  /**
   * Who approved the revision open, and when (ISO 8601); null while not
   * approved. `by` is the approving account's display name as the server
   * resolved it (from the approver's token, never from the page), or null
   * when it has none, which reads as "a team member". Its email is never
   * sent. `note` is the note added at approval, never who approved.
   */
  approval: { by: string | null; at: string; note: string | null } | null;
  /**
   * The signed-in account's display name, as an approval made now would name
   * it (from the server); null reads as "a team member".
   */
  approvingAs: string | null;
  /**
   * Whether the signed-in account may approve (the tenant's owner, or a
   * reviewer), as the server said; the server checks again when it is asked.
   */
  canApprove: boolean;
  /**
   * The server's `approval.is_author`: true when the signed-in account saved
   * the revision open, which it cannot approve (`approval_by_author`). Absent,
   * the page does not guess: Approve stays offered and the server decides.
   */
  isAuthor?: boolean | undefined;
  /** The server holds no author for the revision open (`author_unknown`): said, never guessed. */
  authorUnknown?: boolean;
  /**
   * The server's `author_established`: false when the revision records no
   * saving account (only an email, or nothing), so it cannot be approved
   * (`approval_author_not_established`) until it is saved again for review.
   * Absent, the page does not guess.
   */
  authorEstablished?: boolean | undefined;
  /** M6d: the revision's safety concerns and whether the caller may resolve them. */
  safetyConcerns?: SafetyConcern[];
  safetyResolution?: SafetyResolutionEligibility | null;
};

/** One line of the title block's revision list. */
export type TitleRevision = NonNullable<ProjectDesignRequest['info']['revisions']>[number];

/** The server's `is_author` for the revision open; undefined where it does not say. */
export function isAuthorOf(project: SavedProject): boolean | undefined {
  const flag: unknown = (project as { is_author?: unknown }).is_author;
  return typeof flag === 'boolean' ? flag : undefined;
}

/** The server's `author_established` for the revision open; undefined where it does not say. */
export function authorEstablishedOf(project: SavedProject): boolean | undefined {
  const flag: unknown = (project as { author_established?: unknown }).author_established;
  return typeof flag === 'boolean' ? flag : undefined;
}

/** Who approved a saved project's open revision, and when; null while not approved. */
export function approvalOf(project: SavedProject): OpenedProject['approval'] {
  const revision = project.revisions.find((entry) => entry.number === project.revision);
  return revision?.approved_at
    ? {
        by: revision.approver_name ?? null,
        at: revision.approved_at,
        note: revision.approval_note ?? null,
      }
    : null;
}

/**
 * The engineer's saved projects: save what is on the page (a new project, or
 * a new revision of the one open), open one or an earlier revision of it, or
 * delete one. Saving keeps what was entered; opening designs it afresh. The
 * engineer approves the revision open by name, once; its title block then
 * says so, and a project with an approved revision cannot be deleted.
 */
export function ProjectsPanel({
  token,
  current,
  opened,
  onOpened,
  onSaveState,
  onRefused,
  explainedElsewhere,
  onUnauthorized,
  beforeOpen = (open) => {
    open();
  },
  dirty = false,
  blockers = [],
  blockingCount = 0,
  approvalCovers = true,
  approvalOutdated = [],
  trial = false,
  note: heldNote,
  onNoteChange,
  impls = {},
  base,
  onReconcile,
  onKeepAndOpen,
  onReview,
  accountEmail = null,
  onNewProject,
  onDiscard,
  keptFor = [],
  workMark,
}: {
  /** M14: a mark of the work on the page (changes with every edit), to tell work typed during an open. */
  workMark?: string;
  /** M14: projects with unsaved copies kept in this browser (said on their rows). */
  keptFor?: readonly string[];
  /**
   * M14: start a new, unsaved project (offered when given). Unsaved work is
   * settled first: saved, discarded or kept by cancelling.
   */
  onNewProject?: () => void;
  /**
   * M14: forget the unsaved work on the page (its kept draft included), just
   * before another project replaces it. When given, switching asks Save /
   * Discard / Cancel here instead of `beforeOpen`.
   */
  onDiscard?: () => void;
  token: string;
  /** The signed-in account's email, naming it where it has no display name (this page only). */
  accountEmail?: string | null;
  /** The project as entered now, or null while the form is incomplete. */
  current: {
    name: string;
    request: ProjectDesignRequest;
    /** What the guided intake records with the revision (control needs, intake). */
    requirementsInput?: RequirementsInput | null;
    /**
     * M7b: the configuration shown in the editor (or its explicit removal),
     * sent with every save of an opened project; left out, none is sent.
     */
    configuration?: {
      configuration?: Record<string, unknown>;
      configurationRemoved?: true;
    };
    /** Binding references the user keeps for the load now at their position (not stored). */
    referencesConfirmed?: ReferenceConfirmation[];
  } | null;
  opened: OpenedProject | null;
  /**
   * The editing base: the saved revision the work was made from, sent as
   * `expected_revision`. Null: not provable (saving waits for the page's
   * decision). Absent: the revision open.
   */
  base?: number | null;
  /** An explicit reconciliation: the work now counts as changes to revision `latest`. */
  onReconcile?: (latest: number) => void;
  /** Keep the work in this browser, then open revision `latest` in its place. */
  onKeepAndOpen?: (latest: number) => Promise<'opened' | 'notKept' | 'changed' | 'failed'>;
  /** Open the read-only three-way review (base, your work, latest). */
  onReview?: () => void;
  /** `fill` is true when the project was opened, so the form takes it. */
  onOpened: (project: SavedProject, fill: boolean) => void;
  /** Told when a save starts and how it ended, so the page can show where the work stands. */
  onSaveState?: (state: 'saving' | 'saved' | 'failed') => void;
  /** Told what a refused save objected to, so the page can mark the fields at fault. */
  onRefused?: (refusal: Refusal) => void;
  /**
   * Whether the page says this save refusal itself (one primary message with a way to the
   * entry): then it is not repeated here. Other refusals are still said here.
   */
  explainedElsewhere?: (refusal: Refusal) => boolean;
  /** Told when the server no longer accepts the token, so the page can renew it or ask to sign in. */
  onUnauthorized?: () => void;
  /** Asked before opening a project replaces what is on the page; calls `open` to go ahead. */
  beforeOpen?: (open: () => void) => void;
  /** Whether the page has changes not saved, which an approval would not include. */
  dirty?: boolean;
  /**
   * Why the revision open cannot be approved besides unsaved changes (not
   * validated, blocking findings, rules changed since). The server refuses
   * these too; the button says so before it is pressed.
   */
  blockers?: ApprovalBlocker[];
  /** How many blocking findings the revision open has. */
  blockingCount?: number;
  /** False when the stored approval does not sign the revision's validated snapshot. */
  approvalCovers?: boolean;
  /** What changed since the approval was granted, when it no longer counts. */
  approvalOutdated?: readonly ApprovalPolicyPart[];
  /** Whether the page is a trial's: a trial has no account, so it cannot approve. */
  trial?: boolean;
  /**
   * The revision note, when the page keeps it (so it outlives this panel);
   * kept here otherwise. Both or neither.
   */
  note?: string;
  onNoteChange?: (note: string) => void;
  impls?: Partial<{
    list: typeof listProjects;
    save: typeof saveProject;
    revise: typeof reviseProject;
    open: typeof openProject;
    remove: typeof deleteProject;
    approve: typeof approveRevision;
    resave: typeof resaveRevision;
    resolve: typeof resolveSafetyConcern;
  }>;
}) {
  const t = useIsolatingTranslations('design.projects');
  const locale = useLocale();
  const ta = useIsolatingTranslations('design.account');
  const say = useOutcomeText();
  const id = useId();
  const {
    list = listProjects,
    save = saveProject,
    revise = reviseProject,
    open = openProject,
    remove = deleteProject,
    approve = approveRevision,
    resave = resaveRevision,
    resolve = resolveSafetyConcern,
  } = impls;
  const [page, setPage] = useState<ProjectPage | null>(null);
  // M14: the list's own state, apart from save and approval messages.
  const [listError, setListError] = useState(false);
  const [listing, setListing] = useState(false);
  // Unsaved work to settle before `go` replaces it (another project, or a new one).
  const [switching, setSwitching] = useState<{
    target: string;
    go: (discard: boolean, mark?: string) => void;
  } | null>(null);
  const tUi = useIsolatingTranslations('ui');
  // The switch dialog is portalled to the page once on the client (no server markup to match).
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  const [ownNote, setOwnNote] = useState('');
  const note = heldNote ?? ownNote;
  const setNote = onNoteChange ?? setOwnNote;
  const [message, setMessage] = useState<string | null>(null);
  // A save refused because another revision was saved after this work's base:
  // nothing was saved, the work stays, and the engineer chooses how to go on.
  const [stale, setStale] = useState<{ base: number; latest: number } | null>(null);
  const [staleConfirm, setStaleConfirm] = useState(false);
  const [staleBusy, setStaleBusy] = useState(false);
  // Another project or revision (or this page's own save) answers the question.
  const staleFor = opened === null ? null : `${opened.id}|${String(opened.revision)}`;
  useEffect(() => {
    setStale(null);
    setStaleConfirm(false);
    setStaleBusy(false);
  }, [staleFor]);
  const [working, setWorking] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [approvalNote, setApprovalNote] = useState('');
  // M6d: the approver ticks what a review approval covers; never pre-ticked.
  const [scopeConfirmed, setScopeConfirmed] = useState(false);
  // Anything besides unsaved changes that keeps the revision from approval.
  const reasons = blockers.filter((blocker) => blocker !== 'dirty' && blocker !== 'unsaved');
  // Not the owner or a reviewer: the server would refuse (`approval_not_permitted`).
  const notPermitted = !trial && opened !== null && !opened.canApprove;
  // Known ahead of time only from the server's flag, never from names; the
  // server checks the account itself (`approval_by_author`), owners included.
  const selfAuthored =
    !trial && opened !== null && opened.approval === null && opened.isAuthor === true;
  // Only from the server's flag, never from names or emails: a revision that
  // records no saving account cannot be shown to be approved independently.
  const authorNotEstablished =
    !trial && opened !== null && opened.approval === null && opened.authorEstablished === false;
  const held =
    trial || notPermitted || dirty || selfAuthored || authorNotEstablished || reasons.length > 0;
  const concerns = opened?.safetyConcerns ?? [];
  const unresolvedConcerns = concerns.filter((concern) => !concern.resolved).length;
  const canDelete = permits(page, 'delete_project');
  // Saving the revision open again, unchanged, is how it is designed and
  // approved under today's rules and renderer: when its approval no longer
  // counts or covers it, or it is stale or not validated (the page tells the
  // user to do exactly that). The old revision and its approval stay as they are.
  const saveAgainHelps =
    approvalOutdated.length > 0 ||
    (opened?.approval != null && !approvalCovers) ||
    reasons.includes('stale') ||
    reasons.includes('notValidated');
  // The revision open, unchanged and current: saving it again would only store a copy.
  const unchanged = opened !== null && !dirty && !saveAgainHelps;
  // M14: who the switch dialog's discard is about, and whether saving first is possible.
  const currentName = opened?.name ?? t('newUnsavedName');
  const canSaveFirst = current !== null && !unchanged;

  // Read through refs: the page hands a new `onUnauthorized` (and the
  // translator a new `say`) on every render, and listing again on each one
  // turned every answer into another request, a 429 included, until the
  // design budget for the address was spent.
  const latestSay = useRef(say);
  latestSay.current = say;
  const latestOnUnauthorized = useRef(onUnauthorized);
  latestOnUnauthorized.current = onUnauthorized;
  // Rate limited: no list is asked for again until the server's Retry-After
  // has passed, whatever else changes meanwhile.
  const quietUntil = useRef(0);
  // Each save, open and approval takes the next number; only the latest one's
  // answer is applied. A save that answers after an open began would
  // otherwise attach the form now on the page to the project it saved.
  const sequence = useRef(0);

  // Only the latest list request's answer is applied (a retry, a refresh after a save).
  const listTicket = useRef(0);
  const refresh = useCallback(
    async (cursor?: string) => {
      if (Date.now() < quietUntil.current) return;
      const ticket = ++listTicket.current;
      setListing(true);
      const outcome = await list(cursor ? { token, cursor } : { token });
      if (ticket !== listTicket.current) return;
      setListing(false);
      if (outcome.kind === 'listed') {
        setListError(false);
        setPage((before) =>
          cursor && before
            ? { ...outcome.page, projects: [...before.projects, ...outcome.page.projects] }
            : outcome.page,
        );
        return;
      }
      if (outcome.kind === 'unauthorized') latestOnUnauthorized.current?.();
      if (outcome.kind === 'refused' && outcome.code === 'rate_limited') {
        quietUntil.current = Date.now() + (outcome.retryAfterSeconds ?? 60) * 1000;
      }
      // The list could not be read: what is open in the editor stays as it is.
      setListError(true);
      setMessage(latestSay.current(outcome));
    },
    [list, token],
  );

  // Once per identity (token): not per render.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function store(): Promise<boolean> {
    if (current === null || working || unchanged) return false;
    if (opened !== null && base === null) {
      // The page holds this save until the base is decided (its notice says how).
      setMessage(t('baseHeld'));
      return false;
    }
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    onSaveState?.('saving');
    const outcome = opened
      ? await revise({
          token,
          id: opened.id,
          // The revision the work was made from (never one merely seen since):
          // the server refuses the save if another was saved after it.
          expectedRevision: base ?? opened.revision,
          request: current.request,
          note,
          ...(current.requirementsInput ? { requirementsInput: current.requirementsInput } : {}),
          ...(current.configuration ?? {}),
          ...(current.referencesConfirmed && current.referencesConfirmed.length > 0
            ? { referencesConfirmed: current.referencesConfirmed }
            : {}),
        })
      : await save({
          token,
          name: current.name,
          request: current.request,
          note,
          ...(current.requirementsInput ? { requirementsInput: current.requirementsInput } : {}),
        });
    // Overtaken by a later open: what is on the page now is not what was
    // saved, so neither the page's baseline nor its project is touched.
    if (ticket !== sequence.current) {
      if (outcome.kind === 'saved') void refresh();
      return false;
    }
    setWorking(false);
    onSaveState?.(outcome.kind === 'saved' ? 'saved' : 'failed');
    if (outcome.kind === 'saved') {
      setNote('');
      setMessage(t('saved', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      void refresh();
      return true;
    } else {
      // A refusal says what the API objected to; anything else is a save that
      // did not happen, not a design that failed. What was entered stays.
      if (outcome.kind === 'refused' && outcome.code === 'revision_stale_base') {
        const latest = Number(outcome.params?.latest);
        const expected = Number(outcome.params?.expected);
        if (Number.isInteger(latest) && Number.isInteger(expected)) {
          setStale({ base: expected, latest });
          setStaleConfirm(false);
          setMessage(null);
          return false;
        }
      }
      if (outcome.kind === 'refused') onRefused?.(outcome);
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setMessage(
        outcome.kind === 'refused'
          ? explainedElsewhere?.(outcome)
            ? null
            : say(outcome)
          : outcome.kind === 'unauthorized'
            ? ta('saveUnauthorized')
            : t('saveFailed'),
      );
      return false;
    }
  }

  // Set by the Open button: once the project is open, focus moves to what
  // was opened, rather than falling to the page when the button is disabled.
  const focusOpened = useRef(false);
  // What had focus when Open was pressed. Focus moves to the opened project
  // only while it is still there (or on the page itself): never away from
  // somewhere the engineer went while the project was opening.
  const focusFrom = useRef<Element | null>(null);
  // Whether the page holds unsaved work now (read when an open answers).
  // What the page holds now (the screen's `workMark`), kept current after each
  // commit; an open compares it with what it held when its work was settled.
  const markNow = useRef(workMark);
  const dirtyNow = useRef(dirty);
  useLayoutEffect(() => {
    markNow.current = workMark;
    dirtyNow.current = dirty;
  });
  // In the commit that shows the opened project (a layout effect), so focus is
  // never left on the page between the project appearing and a later effect.
  useLayoutEffect(() => {
    if (!focusOpened.current || opened === null) return;
    focusOpened.current = false;
    const active = document.activeElement;
    const untouched = active === null || active === document.body || active === focusFrom.current;
    focusFrom.current = null;
    if (untouched) focusVisible(document.getElementById(`${id}-opened`));
  }, [opened, id]);

  /**
   * M14: run `go` once unsaved work is settled. Nothing unsaved: at once.
   * Otherwise the switch dialog asks to save, discard or cancel; a discard is
   * applied only once the other project is actually there (`go(true)`).
   */
  function requestSwitch(target: string, go: (discard: boolean, mark?: string) => void) {
    if (onDiscard === undefined) {
      beforeOpen(() => {
        go(false);
      });
      return;
    }
    if (!dirty) {
      go(false);
      return;
    }
    setSwitching({ target, go });
  }

  async function saveThenSwitch() {
    const pending = switching;
    setSwitching(null);
    if (pending === null) return;
    // A save that is refused or fails keeps this project and everything typed:
    // the switch does not go on.
    if (!(await store())) return;
    // A click made since (another project, a new one) is newer than this switch.
    const at = sequence.current;
    // Once the page has rendered against the saved baseline: work typed while
    // it saved is still unsaved, and is asked about before anything replaces
    // it. What the save itself reset (confirmations, the stored configuration)
    // is not an edit.
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    if (sequence.current !== at) return;
    if (dirtyNow.current) {
      setSwitching(pending);
      return;
    }
    pending.go(false, markNow.current);
  }

  function load(projectId: string, revision?: number, focus = false, target?: string) {
    focusFrom.current = document.activeElement;
    requestSwitch(target ?? opened?.name ?? '', (discard, mark) => {
      focusOpened.current = focus;
      // The work as it was when it was settled (nothing unsaved, saved, or
      // chosen to discard): only that may be replaced without asking again.
      void fetchAndOpen(projectId, revision, discard, mark ?? markNow.current);
    });
  }

  function startNew() {
    if (onNewProject === undefined) return;
    requestSwitch(t('targetNew'), (discard) => {
      // Any open, save or approval still answering is for the project left.
      sequence.current++;
      setWorking(false);
      setMessage(null);
      setApprovalNote('');
      setScopeConfirmed(false);
      if (discard) onDiscard?.();
      onNewProject();
      focusOpened.current = false;
      window.setTimeout(() => {
        document.getElementById(`${id}-current`)?.focus();
      }, 0);
    });
  }

  /**
   * @param settledMark The page's work when it was settled for this open
   *   (nothing unsaved, saved, or chosen to discard). Work changed since (typed
   *   while it opened) is asked about before it is replaced.
   */
  async function fetchAndOpen(
    projectId: string,
    revision?: number,
    discard = false,
    settledMark?: string,
  ) {
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    const outcome = await open({ token, id: projectId, revision: revision ?? null });
    if (ticket !== sequence.current) return;
    setWorking(false);
    if (outcome.kind === 'saved') {
      const arrived = outcome.project;
      const apply = (drop: boolean, fromDialog = false) => {
        // Discarded only now that the other project is here: a failed open keeps it all.
        if (drop) onDiscard?.();
        setApprovalNote('');
        setScopeConfirmed(false);
        onOpened(arrived, true);
        // Only when chosen in the dialog shown on arrival (the engineer was
        // working elsewhere, maybe in another view): the field the dialog
        // returned focus to went with the project replaced, so focus is not
        // left on the page. Any other open is focused by the layout effect.
        if (fromDialog) {
          window.setTimeout(() => {
            const active = document.activeElement;
            if (active === null || active === document.body) focusVisible(null);
          }, 0);
        }
      };
      // Work typed while it was opening (the page was clean when Open was
      // pressed): never replaced without asking.
      if (onDiscard !== undefined && workMark !== undefined && markNow.current !== settledMark) {
        // The engineer is working elsewhere on the page: focus is not moved for it.
        focusOpened.current = false;
        focusFrom.current = null;
        setSwitching({
          target: arrived.name,
          go: (drop) => {
            apply(drop, true);
          },
        });
        return;
      }
      apply(discard);
    } else {
      // Nothing opened: no later change (a save, a revision) moves focus for it.
      focusOpened.current = false;
      focusFrom.current = null;
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      if (outcome.kind === 'refused' && outcome.code === 'project_not_found') {
        // Deleted, or no longer the caller's, since the list was read.
        setMessage(t('openGone'));
        void refresh();
        return;
      }
      // Not opened: what is open stays as it is (a refusal says why in its own words).
      setMessage(outcome.kind === 'refused' ? say(outcome) : t('openFailed'));
    }
  }

  async function sign() {
    if (opened === null || held || working || !scopeConfirmed) return;
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    const outcome = await approve({
      token,
      id: opened.id,
      revision: opened.revision,
      note: approvalNote.trim(),
      scopeConfirmed: true,
    });
    if (ticket !== sequence.current) return;
    setWorking(false);
    if (outcome.kind === 'saved') {
      setApprovalNote('');
      setScopeConfirmed(false);
      setMessage(t('approvedNow', { revision: outcome.project.revision }));
      onOpened(outcome.project, false);
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    setMessage(say(outcome));
    // Someone else approved it first, or the server no longer holds it as
    // validated or current: read it again so the readiness shown is the
    // server's, keeping the form as it is.
    if (outcome.kind === 'refused' && outcome.code !== undefined && REOPEN_ON.has(outcome.code)) {
      const reopened = await open({ token, id: opened.id, revision: opened.revision });
      if (ticket !== sequence.current) return;
      if (reopened.kind === 'saved') onOpened(reopened.project, false);
    }
  }

  /** Record a resolution, then read the revision again: its hold and record are the server's. */
  async function resolveConcern(
    concern: SafetyConcern,
    resolution: SafetyResolutionKind,
    reason: string,
    notThisEquipment?: LoadRef[],
  ): Promise<string | null> {
    if (opened === null) return null;
    // A switch since (another project, a new one) makes this answer the old project's.
    const at = sequence.current;
    const outcome = await resolve({
      token,
      id: opened.id,
      revision: opened.revision,
      concern,
      resolution,
      reason,
      ...(notThisEquipment ? { notThisEquipment } : {}),
    });
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    if (at !== sequence.current) return null;
    if (outcome.kind !== 'recorded') return say(outcome);
    const reopened = await open({ token, id: opened.id, revision: opened.revision });
    if (at !== sequence.current) return null;
    if (reopened.kind === 'saved') onOpened(reopened.project, false);
    return null;
  }

  async function saveForReview() {
    if (opened === null || working || dirty) return;
    const ticket = ++sequence.current;
    setWorking(true);
    setMessage(null);
    const outcome = await resave({ token, id: opened.id, revision: opened.revision });
    if (ticket !== sequence.current) return;
    setWorking(false);
    if (outcome.kind === 'saved') {
      setMessage(t('resaved', { revision: outcome.project.revision, from: opened.revision }));
      onOpened(outcome.project, true);
      void refresh();
      return;
    }
    if (outcome.kind === 'unauthorized') onUnauthorized?.();
    // Refused as a save would be (a typed feeder row, say): nothing was saved,
    // and nothing of the user's is changed; they correct it in the editor.
    if (outcome.kind === 'refused') onRefused?.(outcome);
    setMessage(
      outcome.kind === 'refused' ? `${say(outcome)} ${t('resaveRefused')}` : t('saveFailed'),
    );
  }

  async function erase(projectId: string) {
    setConfirming(null);
    if (working) return;
    const outcome = await remove({ token, id: projectId });
    if (outcome.kind === 'deleted') void refresh();
    else {
      if (outcome.kind === 'unauthorized') onUnauthorized?.();
      setMessage(say(outcome));
      // No longer the owner: list again, so delete is no longer offered.
      if (outcome.kind === 'refused' && outcome.code === 'project_delete_not_permitted') {
        void refresh();
      }
    }
  }

  return (
    <section className="card flex flex-col gap-4 p-4 md:p-5" data-testid="projects-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">{t('title')}</h2>
        {onNewProject && (
          <button
            type="button"
            disabled={working}
            onClick={startNew}
            className="btn btn-secondary btn-sm"
            data-testid="project-new"
          >
            {t('newProject')}
          </button>
        )}
      </div>
      {onNewProject && (
        <p
          id={`${id}-current`}
          tabIndex={-1}
          className="text-sm"
          dir="auto"
          data-testid="project-current"
        >
          {opened
            ? t(dirty ? 'currentUnsaved' : 'current', { name: opened.name })
            : t(dirty ? 'currentNewUnsaved' : 'currentNew')}
        </p>
      )}
      {opened && (
        <div className="flex flex-wrap items-end gap-3 text-sm" data-testid="project-opened">
          <span id={`${id}-opened`} tabIndex={-1} data-testid="project-opened-name">
            {t('opened', { name: opened.name, revision: opened.revision })}
          </span>
          {opened.revisions.length > 1 && (
            <Field id={`${id}-revision`} label={t('revision')}>
              <select
                id={`${id}-revision`}
                value={opened.revision}
                // Not disabled while loading (that would drop focus to the page):
                // a change made meanwhile is ignored instead.
                aria-disabled={working}
                onChange={(event) => {
                  if (working) return;
                  const revision = Number(event.target.value);
                  load(opened.id, revision, false, t('opened', { name: opened.name, revision }));
                }}
                className="input w-full sm:w-64"
              >
                {opened.revisions.map((number) => (
                  <option key={number} value={number}>
                    {number}
                  </option>
                ))}
              </select>
            </Field>
          )}
        </div>
      )}
      {opened &&
        (opened.approval ? (
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium text-accent" data-testid="project-approval">
              {t('approvedBy', {
                revision: opened.revision,
                name: opened.approval.by ?? t('teamMember'),
                date: opened.approval.at.slice(0, 10),
              })}
            </p>
            {opened.approval.note && (
              <p className="text-sm text-text-muted" data-testid="project-approval-note">
                {t('approvalNoteShown', { note: opened.approval.note })}
              </p>
            )}
            {/* An approval never reads as covering what it did not sign, nor
                as counting once the policy it was granted under changed. */}
            {approvalOutdated.length > 0 && (
              <p className="text-sm text-text-muted" data-testid="approval-not-current">
                {t('approvalNotCurrent', {
                  revision: opened.revision,
                  changes: listOf(
                    locale,
                    approvalOutdated.map((part) => t(`approvalParts.${part}`)),
                  ),
                })}
              </p>
            )}
            {approvalOutdated.length > 0 && !trial && (
              <button
                type="button"
                disabled={working || dirty}
                onClick={() => {
                  void saveForReview();
                }}
                className="btn btn-secondary btn-sm self-start"
                data-testid="resave-outdated-approval"
              >
                {t('resave')}
              </button>
            )}
            {!approvalCovers && approvalOutdated.length === 0 && (
              <p className="text-sm text-text-muted" data-testid="approval-not-covering">
                {t('approvalNotCovering', { revision: opened.revision })}
              </p>
            )}
            {dirty && (
              <p className="text-sm text-text-muted" data-testid="approval-changed">
                {t('approvalChanged', { revision: opened.revision })}
              </p>
            )}
          </div>
        ) : (
          <>
            {/* Said only to someone who may approve: it names who the approval records. */}
            {!trial && !notPermitted && (
              <p className="text-sm text-text-muted" data-testid="approving-as">
                {/* The display name, else the account's email (this page only), as elsewhere. */}
                {opened.approvingAs || accountEmail
                  ? t('approvingAs', { name: opened.approvingAs || accountEmail || '' })
                  : t('approvingAsUnnamed')}
              </p>
            )}
            <SafetyConcerns
              concerns={concerns}
              eligibility={opened.safetyResolution ?? null}
              onResolve={resolveConcern}
            />
            <ApprovalScope
              concerns={concerns}
              confirmed={scopeConfirmed}
              onConfirmedChange={setScopeConfirmed}
              disabled={trial || notPermitted}
            />
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <Field id={`${id}-approval-note`} label={t('approvalNote')}>
                <input
                  id={`${id}-approval-note`}
                  value={approvalNote}
                  maxLength={200}
                  disabled={trial || notPermitted}
                  onChange={(event) => {
                    setApprovalNote(event.target.value);
                  }}
                  className="input w-full"
                />
              </Field>
              <button
                type="button"
                disabled={working || held || !scopeConfirmed}
                aria-describedby={held ? `${id}-approve-held` : undefined}
                onClick={() => {
                  void sign();
                }}
                className="btn btn-secondary btn-sm self-start sm:self-end"
              >
                {t('approve', { revision: opened.revision })}
              </button>
            </div>
            {opened.authorUnknown && (
              <p className="text-sm text-text-muted" data-testid="approve-author-unknown">
                {t('authorUnknown')}
              </p>
            )}
            {held && (
              <div id={`${id}-approve-held`} className="flex flex-col gap-1">
                {authorNotEstablished && (
                  <div className="flex flex-col gap-2" data-testid="approve-author-not-established">
                    <p className="text-sm text-text-muted">
                      {t('authorNotEstablished', { revision: opened.revision })}
                    </p>
                    <button
                      type="button"
                      disabled={working || dirty}
                      onClick={() => {
                        void saveForReview();
                      }}
                      className="btn btn-secondary btn-sm self-start"
                      data-testid="resave-for-review"
                    >
                      {t('resave')}
                    </button>
                  </div>
                )}
                {selfAuthored && (
                  <p className="text-sm text-text-muted" data-testid="approve-self-authored">
                    {t('approveSelfAuthored', { revision: opened.revision })}
                  </p>
                )}
                {trial && (
                  <p className="text-sm text-text-muted" data-testid="approve-held-trial">
                    {t('approveTrial')}
                  </p>
                )}
                {notPermitted && (
                  <p className="text-sm text-text-muted" data-testid="approve-not-permitted">
                    {t('approveNotPermitted')}
                  </p>
                )}
                {dirty && (
                  <p className="text-sm text-text-muted" data-testid="approve-dirty">
                    {t('approveDirty', { revision: opened.revision })}
                  </p>
                )}
                {reasons.map((reason) => (
                  <p
                    key={reason}
                    className="text-sm text-text-muted"
                    data-testid={`approve-held-${reason}`}
                  >
                    {t(`approveHeld.${reason}`, {
                      revision: opened.revision,
                      // Each hold says its own count: blocking findings, or
                      // the unresolved safety concerns (M6e).
                      count: reason === 'safety' ? unresolvedConcerns : blockingCount,
                    })}
                  </p>
                ))}
              </div>
            )}
          </>
        ))}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <Field id={`${id}-note`} label={t('note')}>
          <input
            id={`${id}-note`}
            value={note}
            maxLength={500}
            onChange={(event) => {
              setNote(event.target.value);
            }}
            className="input w-full"
          />
        </Field>
        <button
          type="button"
          disabled={current === null || working || unchanged}
          aria-describedby={unchanged ? `${id}-save-unchanged` : undefined}
          onClick={() => {
            void store();
          }}
          className="btn btn-primary btn-sm self-start sm:self-end"
        >
          {opened ? t('saveRevision') : t('save')}
        </button>
      </div>
      {opened && unchanged && (
        <p
          id={`${id}-save-unchanged`}
          className="text-sm text-text-muted"
          data-testid="save-unchanged"
        >
          {t('saveUnchanged', { revision: opened.revision })}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm" data-testid="projects-message">
          {message}
        </p>
      )}
      {stale !== null && opened !== null && (
        <div
          role="group"
          aria-labelledby={`${id}-stale`}
          className="mt-2 rounded-md border border-status-critical p-3 text-sm"
          data-testid="stale-reconcile"
        >
          <p id={`${id}-stale`} role="alert">
            {t('staleIntro', { base: stale.base, latest: stale.latest })}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {onKeepAndOpen ? (
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                disabled={staleBusy}
                data-testid="stale-keep-open"
                onClick={() => {
                  const target = stale.latest;
                  setStaleBusy(true);
                  void onKeepAndOpen(target)
                    .then((outcome) => {
                      setMessage(
                        outcome === 'opened'
                          ? t('staleOpened', { latest: target })
                          : t(
                              outcome === 'notKept'
                                ? 'staleNotKept'
                                : outcome === 'changed'
                                  ? 'staleChanged'
                                  : 'staleOpenFailed',
                            ),
                      );
                    })
                    .catch(() => {
                      setMessage(t('staleOpenFailed'));
                    })
                    .finally(() => {
                      setStaleBusy(false);
                    });
                }}
              >
                {t('staleKeepOpen', { latest: stale.latest })}
              </button>
            ) : null}
            {onReview ? (
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                data-testid="stale-review"
                onClick={onReview}
              >
                {t('staleReview')}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-sm btn-secondary"
              data-testid="stale-dismiss"
              onClick={() => {
                setStale(null);
              }}
            >
              {t('staleDismiss')}
            </button>
          </div>
          {onReconcile ? (
            <div className="mt-3">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={staleConfirm}
                  data-testid="stale-confirm"
                  onChange={(event) => {
                    setStaleConfirm(event.target.checked);
                  }}
                />
                <span>{t('staleConfirm', { latest: stale.latest })}</span>
              </label>
              <button
                type="button"
                className="btn btn-sm btn-primary mt-2"
                disabled={!staleConfirm}
                data-testid="stale-reconcile-accept"
                onClick={() => {
                  const target = stale.latest;
                  onReconcile(target);
                  setStale(null);
                  setMessage(t('staleReconciled', { latest: target }));
                }}
              >
                {t('staleReconcile', { latest: stale.latest })}
              </button>
            </div>
          ) : null}
        </div>
      )}
      {page === null && !listError && (
        <p role="status" className="text-sm text-text-muted" data-testid="projects-loading">
          {t('loading')}
        </p>
      )}
      {listError && (
        <div className="flex flex-wrap items-center gap-2" data-testid="projects-list-error">
          <p role="alert" className="text-sm">
            {t('listFailed')}
          </p>
          <button
            type="button"
            aria-disabled={listing}
            onClick={() => {
              if (!listing) void refresh();
            }}
            className="btn btn-secondary btn-sm"
            data-testid="projects-retry"
          >
            {t('retry')}
          </button>
        </div>
      )}
      {page && page.projects.length === 0 && (
        <p className="text-sm text-text-muted" data-testid="projects-empty">
          {t('none')}
        </p>
      )}
      {page && page.projects.length > 0 && (
        <ul className="flex flex-col divide-y divide-border-subtle text-sm">
          {page.projects.map((project) => (
            <li
              key={project.id}
              data-testid={`project-${project.id}`}
              aria-current={opened?.id === project.id ? 'true' : undefined}
              className="flex flex-wrap items-center gap-2 py-2"
            >
              <span
                id={`${id}-name-${project.id}`}
                className="min-w-0 flex-1 break-words font-medium"
                dir="auto"
              >
                {project.name}
              </span>
              {keptFor.includes(project.id) && (
                <span className="text-xs text-text-muted" data-testid="project-has-kept">
                  {t('hasKept')}
                </span>
              )}
              {opened?.id === project.id && (
                <span className="text-xs font-semibold" data-testid="project-is-current">
                  {t('isCurrent')}
                </span>
              )}
              <span className="text-text-muted">
                {t('revisions', { count: project.revisions })}
              </span>
              <span className="text-text-muted" data-testid="project-updated">
                {t('updated', { date: shortDate(project.updated_at, locale) })}
              </span>
              <button
                type="button"
                disabled={working}
                aria-describedby={`${id}-name-${project.id}`}
                onClick={() => {
                  load(project.id, undefined, true, project.name);
                }}
                className="btn btn-sm btn-secondary"
              >
                {t('open')}
              </button>
              {/* Only the owner deletes; the server refuses anyone else. */}
              {canDelete && (
                <button
                  type="button"
                  disabled={working}
                  onClick={() => {
                    if (confirming === project.id) void erase(project.id);
                    else setConfirming(project.id);
                  }}
                  className="btn btn-sm btn-secondary"
                >
                  {confirming === project.id ? t('confirmDelete') : t('delete')}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {page?.next_cursor && (
        <button
          type="button"
          aria-disabled={listing}
          onClick={() => {
            if (!listing && page.next_cursor) void refresh(page.next_cursor);
          }}
          className="btn btn-secondary btn-sm self-start"
          data-testid="projects-more"
        >
          {t('more')}
        </button>
      )}
      {onDiscard && (
        <p className="text-xs text-text-muted" data-testid="projects-drafts-local">
          {t('draftsLocal')}
        </p>
      )}
      {/* Rendered at the page level: the panel sits in a view that is hidden
          while another tab is shown, and a modal inside a hidden view would
          block the page without being seen. */}
      {mounted &&
        createPortal(
          <Dialog
            open={switching !== null}
            onClose={() => {
              setSwitching(null);
            }}
            title={t('switchTitle')}
            closeLabel={tUi('close')}
            footer={
              <div className="flex flex-wrap justify-end gap-2" data-testid="switch-dialog">
                <button
                  type="button"
                  className="btn btn-sm btn-secondary"
                  onClick={() => {
                    setSwitching(null);
                  }}
                  data-testid="switch-cancel"
                >
                  {t('switchCancel')}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  onClick={() => {
                    const pending = switching;
                    setSwitching(null);
                    pending?.go(true);
                  }}
                  data-testid="switch-discard"
                >
                  {opened ? t('switchDiscard', { name: opened.name }) : t('switchDiscardNew')}
                </button>
                {canSaveFirst && (
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    onClick={() => {
                      void saveThenSwitch();
                    }}
                    data-testid="switch-save"
                  >
                    {t('switchSave')}
                  </button>
                )}
              </div>
            }
          >
            <p className="text-sm" dir="auto" data-testid="switch-body">
              {t(canSaveFirst ? 'switchBody' : 'switchBodyNoSave', {
                name: currentName,
                target: switching?.target ?? '',
              })}
            </p>
          </Dialog>,
          document.body,
        )}
    </section>
  );
}

/** A list entry's save date, in the page's language (the date only). */
function shortDate(iso: string, locale: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium' }).format(date);
}

/**
 * Focus `target` when it can be seen; when it sits in a hidden view (another
 * tab is shown) or is gone, the selected tab instead, so focus is never left
 * on the page.
 */
function focusVisible(target: HTMLElement | null): void {
  if (target !== null && target.closest('[hidden]') === null) {
    target.focus();
    return;
  }
  document.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.focus();
}
