import type { Refusal, SavedProject } from '@/lib/design';

/**
 * Where a saved revision stands on the way to an execution package, and the
 * findings the server recorded when it was saved.
 *
 * Saved -> Validated (with findings) -> Review approved -> Execution ready.
 * Execution ready is never reached yet: no check proves an execution package
 * complete, so the server refuses it and names what is missing.
 *
 * The fields are read from the saved project defensively: they are external
 * input, and a revision saved before validation existed has none of them.
 */

export type FindingSeverity = 'blocking' | 'warning' | 'info';

/** One finding the server recorded for a revision (contract: `ValidationFinding`). */
export type DesignFinding = {
  code: string;
  severity: FindingSeverity;
  board_id: string | null;
  circuit_id: string | null;
  device_id: string | null;
  values: Record<string, string>;
  /** The server's English sentence, for a code the page has no translation of. */
  message: string;
  requires_engineering_confirmation: boolean;
  source: string;
};

/** What the server recorded about the revision open. */
export type RevisionValidation = {
  /** False for a revision saved before validation: it must be saved again. */
  validated: boolean;
  findings: DesignFinding[];
  snapshotSha256: string | null;
  approvedSnapshotSha256: string | null;
  rulesVersion: string | null;
  /** What the revision can be exported as now, as the API said (`stale`, `blocked`, ...). */
  exportStatus: string | null;
};

const SEVERITIES: readonly FindingSeverity[] = ['blocking', 'warning', 'info'];

type Fields = Record<string, unknown>;

function isFields(value: unknown): value is Fields {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null;
}

/** One finding, or null when it is not one (a malformed entry is skipped, not trusted). */
export function parseFinding(value: unknown): DesignFinding | null {
  if (!isFields(value)) return null;
  const code = text(value.code);
  const severity = SEVERITIES.find((entry) => entry === value.severity);
  if (code === null || severity === undefined) return null;
  const values: Record<string, string> = {};
  if (isFields(value.values)) {
    for (const [key, entry] of Object.entries(value.values)) {
      if (['string', 'number', 'boolean'].includes(typeof entry)) values[key] = String(entry);
    }
  }
  return {
    code,
    severity,
    board_id: text(value.board_id),
    circuit_id: text(value.circuit_id),
    device_id: text(value.device_id),
    values,
    message: typeof value.message === 'string' ? value.message : '',
    // An unclassified finding needs an engineer; absent is read as needing one.
    requires_engineering_confirmation: value.requires_engineering_confirmation !== false,
    source: typeof value.source === 'string' ? value.source : '',
  };
}

/** Blocking first, then warnings, then information, each kept in the server's order. */
export function sortFindings(findings: DesignFinding[]): DesignFinding[] {
  return findings
    .map((finding, index) => ({ finding, index }))
    .sort(
      (a, b) =>
        SEVERITIES.indexOf(a.finding.severity) - SEVERITIES.indexOf(b.finding.severity) ||
        a.index - b.index,
    )
    .map(({ finding }) => finding);
}

/** The validation of the revision a saved project is open at. */
export function validationOf(project: SavedProject): RevisionValidation {
  const top: Fields = project;
  // The open revision's summary may carry them instead of the project.
  const summary: Fields =
    (project.revisions.find((entry) => entry.number === project.revision) as Fields | undefined) ??
    {};
  const read = (key: string): unknown => (key in top ? top[key] : summary[key]);
  const rawFindings = read('findings');
  const findings = Array.isArray(rawFindings)
    ? rawFindings.map(parseFinding).filter((entry): entry is DesignFinding => entry !== null)
    : [];
  return {
    validated: read('validated') === true,
    findings: sortFindings(findings),
    snapshotSha256: text(read('snapshot_sha256')),
    approvedSnapshotSha256: text(read('approved_snapshot_sha256')),
    rulesVersion: text(read('rules_version')),
    exportStatus: text(read('export_status')),
  };
}

export type StepState = 'done' | 'pending' | 'blocked' | 'stale';

export type Readiness = {
  saved: StepState;
  validated: StepState;
  approved: StepState;
  execution: 'blocked';
  /** Approval can be given now (the signer's name aside). */
  canApprove: boolean;
  /** Why approval cannot be given, most pressing first. */
  approvalBlockers: ApprovalBlocker[];
  /** The stored approval signs the snapshot exported (not a legacy or mismatched one). */
  approvalCovers: boolean;
  blockingCount: number;
};

export type ApprovalBlocker = 'unsaved' | 'dirty' | 'stale' | 'notValidated' | 'blocking';

/**
 * Where the work stands.
 *
 * `dirty`: the page has changes the revision does not hold, so neither its
 * validation nor its approval covers what is on screen. `stale`: the server
 * said the rules or the company profile changed since the revision was saved.
 */
export function readinessOf(input: {
  opened: { approval: { by: string; at: string } | null } | null;
  validation: RevisionValidation | null;
  dirty: boolean;
  stale: boolean;
}): Readiness {
  const { opened, validation, dirty } = input;
  // Stale when an export said so, or when the revision was read as stale.
  const stale = input.stale || validation?.exportStatus === 'stale';
  const blockingCount =
    validation?.findings.filter((finding) => finding.severity === 'blocking').length ?? 0;
  const validated = opened !== null && validation?.validated === true;
  const approvalCovers =
    opened?.approval != null &&
    validated &&
    validation.snapshotSha256 != null &&
    validation.approvedSnapshotSha256 === validation.snapshotSha256;

  const approvalBlockers: ApprovalBlocker[] = [];
  if (opened === null) approvalBlockers.push('unsaved');
  else {
    if (dirty) approvalBlockers.push('dirty');
    if (stale) approvalBlockers.push('stale');
    if (!validated) approvalBlockers.push('notValidated');
    else if (blockingCount > 0) approvalBlockers.push('blocking');
  }
  const moved = dirty || stale;

  return {
    saved: opened === null ? 'pending' : dirty ? 'stale' : 'done',
    validated: !validated ? 'pending' : moved ? 'stale' : blockingCount > 0 ? 'blocked' : 'done',
    approved: approvalCovers
      ? moved
        ? 'stale'
        : 'done'
      : blockingCount > 0
        ? 'blocked'
        : 'pending',
    execution: 'blocked',
    canApprove: opened !== null && opened.approval === null && approvalBlockers.length === 0,
    approvalBlockers,
    approvalCovers,
    blockingCount,
  };
}

/** What an execution package lacks, as the API named it, in a stable order. */
export const EXECUTION_GAPS = [
  'wiring',
  'control_schematics',
  'orderable_parts',
  'mechanical_layout',
] as const;

export type ExecutionRefusal = {
  /** What is missing from the package, as codes (translated when known). */
  missing: string[];
  blockingFindings: number;
  approvalMissing: boolean;
};

function list(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value.filter((entry): entry is string => typeof entry === 'string' && entry !== '');
  }
  if (typeof value === 'string' && value !== '') {
    return value
      .split(',')
      .map((entry) => entry.trim())
      .filter((entry) => entry !== '');
  }
  return [];
}

/**
 * The reasons an execution export was refused (`execution_package_incomplete`).
 *
 * The contract names the gaps but not their keys' spelling, so the common
 * spellings are accepted; anything unrecognised is still listed, never dropped.
 */
export function executionRefusalOf(refusal: Refusal): ExecutionRefusal {
  const values = refusal.values ?? {};
  const missing = list(values.missing ?? values.missing_items ?? values.gaps);
  // The API lists the blocking findings' codes, comma-separated; a count is read too.
  const findings = values.blocking_findings;
  const blockingFindings = typeof findings === 'number' ? findings : list(findings).length;
  const approvalMissing =
    values.approval === 'missing' ||
    missing.includes('approval') ||
    missing.includes('review_approval');
  return {
    missing: missing.filter((entry) => entry !== 'approval' && entry !== 'review_approval'),
    blockingFindings,
    approvalMissing,
  };
}

/** The canonical gap a server spelling stands for, or null when it is not one we know. */
export function gapOf(code: string): (typeof EXECUTION_GAPS)[number] | null {
  const key = code.toLowerCase();
  if (key.includes('wiring') || key.includes('terminal') || key.includes('connect')) {
    return 'wiring';
  }
  if (key.includes('control') || key.includes('schematic')) return 'control_schematics';
  if (key.includes('part') || key.includes('order')) return 'orderable_parts';
  if (key.includes('mechanical') || key.includes('layout') || key.includes('enclosure')) {
    return 'mechanical_layout';
  }
  return null;
}

/**
 * The load row a finding is about. Circuit ids are `c<n>`, numbered from the
 * board's first load row (`c3` is the third); a device id begins with its
 * circuit's (`c3-breaker`). A board's own devices (`incomer`) have no row.
 */
export function rowOf(finding: Pick<DesignFinding, 'circuit_id' | 'device_id'>): number | null {
  const id = finding.circuit_id ?? finding.device_id;
  const match = id === null ? null : /^c(\d+)(?:-|$)/.exec(id);
  if (!match?.[1]) return null;
  const number = Number(match[1]);
  return number >= 1 ? number - 1 : null;
}
