#!/usr/bin/env node
// npm audit gate with one narrowly scoped, expiring exception record.
//
// Runs exactly the audit CI ran before (`npm audit --omit=dev --audit-level=high`) and
// passes only when the audit is clean, or when every finding is the one finding an
// approved, unexpired exception record in security/exceptions/ describes: the same
// advisory, package, version, installation path and dependency chain, with nothing else.
// Anything unexpected fails closed: another advisory or path, a changed audit shape,
// an audit-service error, an expired or unapproved record, a lockfile that no longer
// matches, or a patched release becoming available (then the exception must be removed).
//
// Usage: node scripts/security/npm-audit-gate.mjs   (from the repository root)

import { execFileSync } from 'node:child_process';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(fileURLToPath(new URL('.', import.meta.url)), '..', '..');
const EXCEPTIONS = join(ROOT, 'security', 'exceptions');

// The latest expiry each exception may have, set here and not only in its record, so a
// record edit alone cannot extend it; a longer one fails.
const MAX_EXPIRY = { 'GHSA-fc5q-39xc-vjpf': '2026-12-15' };

/** Whether ``value`` is a real calendar date written YYYY-MM-DD. */
function isDate(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const parsed = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
}

function same(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * Decide whether an audit passes. Pure: every input is given.
 *
 * @param {object} input
 * @param {number} input.exitCode npm audit's exit code (0 clean, 1 findings at or above the level).
 * @param {string} input.stdout npm audit --json output.
 * @param {object} input.lock package-lock.json.
 * @param {object[]} input.records The exception records.
 * @param {string} input.today YYYY-MM-DD (UTC).
 * @param {(record: object) => {latest: string, affected: boolean} | {error: string}} input.patchStatus
 *   Whether the record's package still has no patched release.
 * @returns {{ok: boolean, errors: string[], notes: string[]}}
 */
export function evaluate({ exitCode, stdout, lock, records, today, patchStatus }) {
  const errors = [];
  const notes = [];
  let audit;
  try {
    audit = JSON.parse(stdout);
  } catch {
    return { ok: false, errors: ['npm audit did not return JSON (audit service failure?)'], notes };
  }
  if (audit === null || typeof audit !== 'object' || Array.isArray(audit)) {
    return { ok: false, errors: ['npm audit returned an unexpected value'], notes };
  }
  if ('error' in audit) {
    return {
      ok: false,
      errors: [`npm audit reported an error: ${JSON.stringify(audit.error)}`],
      notes,
    };
  }
  if (audit.auditReportVersion !== 2) {
    return {
      ok: false,
      errors: [`unexpected auditReportVersion ${String(audit.auditReportVersion)}`],
      notes,
    };
  }
  const vulns = audit.vulnerabilities;
  const counts = audit.metadata?.vulnerabilities;
  if (
    vulns === null ||
    typeof vulns !== 'object' ||
    counts === null ||
    typeof counts !== 'object'
  ) {
    return {
      ok: false,
      errors: ['npm audit output is missing vulnerabilities or metadata'],
      notes,
    };
  }
  if (exitCode !== 0 && exitCode !== 1) {
    return { ok: false, errors: [`npm audit exited ${String(exitCode)}`], notes };
  }
  const names = Object.keys(vulns);
  if (names.length === 0) {
    if (exitCode !== 0) errors.push('npm audit failed but reported no vulnerabilities');
    if (records.length > 0) {
      notes.push('the audit is clean: remove the exception records in security/exceptions/');
    }
    return { ok: errors.length === 0, errors, notes };
  }

  // Every finding must belong to exactly one record; a record covers its audit entries only.
  const covered = new Set();
  for (const record of records) {
    const label = `exception ${String(record.id)}`;
    const recordErrors = checkRecord(record, { vulns, lock, today, patchStatus });
    if (recordErrors.length > 0) {
      errors.push(...recordErrors.map((e) => `${label}: ${e}`));
      continue;
    }
    for (const name of Object.keys(record.scope.audit_entries)) covered.add(name);
    notes.push(
      `${label}: applies to ${record.scope.path} until ${record.expires} (owner-approved)`,
    );
  }
  for (const name of names) {
    if (!covered.has(name)) errors.push(`finding not covered by any exception: ${name}`);
  }
  // The metadata must agree with exactly the covered entries: nothing else at any severity.
  const total = Number(counts.total);
  if (total !== names.length) {
    errors.push(
      `metadata reports ${String(counts.total)} vulnerabilities, entries list ${String(names.length)}`,
    );
  }
  return { ok: errors.length === 0, errors, notes };
}

function checkRecord(record, { vulns, lock, today, patchStatus }) {
  const errors = [];
  if (record.schema !== 'panelpilot-audit-exception/1') return ['unknown record schema'];
  if (record.owner_approval?.approved !== true || !record.owner_approval?.approved_by) {
    errors.push('not approved by the owner (owner_approval.approved / approved_by)');
  }
  const cap = MAX_EXPIRY[record.id];
  if (!isDate(record.expires)) {
    errors.push('no valid expiry date');
  } else if (cap === undefined) {
    errors.push('no maximum expiry is set for it in the gate');
  } else if (record.expires > cap) {
    errors.push(`expiry ${record.expires} is after the maximum ${cap}`);
  } else if (today > record.expires) {
    errors.push(`expired on ${record.expires}`);
  }
  if (record.owner_approval?.approved === true && !isDate(record.owner_approval.approved_on)) {
    errors.push('approval has no valid approved_on date');
  }
  const scope = record.scope ?? {};
  const entries = scope.audit_entries ?? {};
  const advisoryUrl = `https://github.com/advisories/${String(record.id)}`;
  if (record.advisory?.url !== advisoryUrl) errors.push('advisory url does not match the id');

  // The audit entries: exactly the recorded nodes, effects and via, and only this advisory.
  for (const [name, expected] of Object.entries(entries)) {
    const found = vulns[name];
    if (found === undefined) continue; // not reported (any more): nothing to except here
    if (!same(found.nodes, expected.nodes)) {
      errors.push(
        `${name}: installed at ${JSON.stringify(found.nodes)}, recorded ${JSON.stringify(expected.nodes)}`,
      );
    }
    if (!same(found.effects, expected.effects)) {
      errors.push(
        `${name}: effects ${JSON.stringify(found.effects)}, recorded ${JSON.stringify(expected.effects)}`,
      );
    }
    if (name === scope.package) {
      const advisories = found.via.filter((v) => typeof v === 'object');
      const strings = found.via.filter((v) => typeof v !== 'object');
      if (strings.length > 0)
        errors.push(`${name}: also vulnerable via ${JSON.stringify(strings)}`);
      if (advisories.length !== 1) {
        errors.push(`${name}: ${String(advisories.length)} advisories, expected exactly 1`);
      }
      for (const via of advisories) {
        if (via.url !== advisoryUrl || via.name !== scope.package) {
          errors.push(`${name}: another advisory ${String(via.url)}`);
        }
        if (via.range !== record.advisory.vulnerable_range) {
          errors.push(
            `${name}: advisory range is now ${String(via.range)} (recorded ${record.advisory.vulnerable_range})`,
          );
        }
      }
    } else if (!same(found.via, expected.via)) {
      errors.push(
        `${name}: via ${JSON.stringify(found.via)}, recorded ${JSON.stringify(expected.via)}`,
      );
    }
  }
  if (vulns[scope.package] === undefined) {
    errors.push(`${String(scope.package)} is no longer reported: remove this exception`);
  }

  // The lockfile: the recorded chain, versions and paths, and no other path to the package.
  const packages = lock?.packages ?? {};
  for (const link of scope.chain ?? []) {
    const entry = packages[link.path];
    if (entry === undefined || entry.version !== link.version) {
      errors.push(
        `lockfile: ${link.path} is ${entry ? entry.version : 'absent'}, recorded ${link.version}`,
      );
    }
  }
  const chain = scope.chain ?? [];
  for (let i = 1; i < chain.length; i += 1) {
    const dependents = Object.entries(packages)
      .filter(
        ([path, entry]) =>
          path !== '' &&
          !entry.dev &&
          ['dependencies', 'optionalDependencies', 'peerDependencies'].some(
            (key) => entry[key]?.[chain[i].name] !== undefined,
          ),
      )
      .map(([path]) => path);
    // Only the recorded parent may pull it into the production tree.
    if (!same(dependents, [chain[i - 1].path])) {
      errors.push(
        `lockfile: ${chain[i].name} is required in production by ${JSON.stringify(dependents)}`,
      );
    }
  }
  const productionCopies = Object.entries(packages)
    .filter(([path, entry]) => path.endsWith(`node_modules/${scope.package}`) && !entry.dev)
    .map(([path]) => path);
  if (!same(productionCopies, [scope.path])) {
    errors.push(
      `lockfile: production copies of ${String(scope.package)}: ${JSON.stringify(productionCopies)}`,
    );
  }

  // A patched release ends the exception.
  const status = patchStatus(record);
  if ('error' in status) {
    errors.push(`cannot check for a patched release: ${status.error}`);
  } else if (!status.affected) {
    errors.push(
      `${String(scope.package)}@${status.latest} is not affected: a patched release exists, remove this exception`,
    );
  } else if (status.newer.length > 0) {
    errors.push(
      `${String(scope.package)} has releases after ${String(scope.version)} (${status.newer.join(', ')}): check whether one fixes the advisory and update or remove this exception`,
    );
  }
  return errors;
}

function npm(args) {
  try {
    return {
      code: 0,
      stdout: execFileSync('npm', args, { cwd: ROOT, encoding: 'utf8', maxBuffer: 64 << 20 }),
    };
  } catch (exc) {
    return {
      code: typeof exc.status === 'number' ? exc.status : -1,
      stdout: String(exc.stdout ?? ''),
    };
  }
}

/** The latest release and whether the npm advisory API still lists it as affected. */
function livePatchStatus(record) {
  const name = record.scope.package;
  const view = npm(['view', name, 'dist-tags.latest', 'versions', 'time', '--json']);
  let info;
  try {
    info = JSON.parse(view.stdout);
  } catch {
    return { error: `npm view ${name} failed` };
  }
  const latest = info?.['dist-tags.latest'];
  const versions = info?.versions;
  if (view.code !== 0 || typeof latest !== 'string' || !Array.isArray(versions)) {
    return { error: `npm view ${name} failed` };
  }
  // Any release published after the recorded version (a backport or a prerelease that is
  // not `latest` included) is reported, so it is looked at rather than missed.
  const recordedAt = info.time?.[record.scope.version];
  if (typeof recordedAt !== 'string') {
    return { error: `no publish time for ${name}@${record.scope.version}` };
  }
  const newer = versions.filter(
    (v) => typeof info.time?.[v] === 'string' && info.time[v] > recordedAt,
  );
  const body = JSON.stringify({ [name]: [latest] });
  const out = execFileSync(
    'curl',
    [
      '-sS',
      '--fail',
      '--max-time',
      '30',
      '-X',
      'POST',
      '-H',
      'content-type: application/json',
      '--data',
      body,
      'https://registry.npmjs.org/-/npm/v1/security/advisories/bulk',
    ],
    { encoding: 'utf8' },
  );
  let found;
  try {
    found = JSON.parse(out);
  } catch {
    return { error: 'the npm advisory API did not return JSON' };
  }
  const listed = Array.isArray(found?.[name]) ? found[name] : [];
  return { latest, affected: listed.some((a) => a.url === record.advisory.url), newer };
}

function main() {
  const records = readdirSync(EXCEPTIONS)
    .filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(readFileSync(join(EXCEPTIONS, f), 'utf8')));
  const lock = JSON.parse(readFileSync(join(ROOT, 'package-lock.json'), 'utf8'));
  const audit = npm(['audit', '--omit=dev', '--audit-level=high', '--json']);
  const result = evaluate({
    exitCode: audit.code,
    stdout: audit.stdout,
    lock,
    records,
    today: new Date().toISOString().slice(0, 10),
    patchStatus: (record) => {
      try {
        return livePatchStatus(record);
      } catch (exc) {
        return { error: String(exc.message ?? exc) };
      }
    },
  });
  for (const note of result.notes) console.log(`note: ${note}`);
  for (const error of result.errors) console.error(`error: ${error}`);
  if (!result.ok) {
    console.error('npm audit gate: FAILED');
    process.exit(1);
  }
  console.log('npm audit gate: passed');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
