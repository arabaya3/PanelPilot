// Tests of the npm audit gate: the recorded exception passes only exactly as recorded,
// and everything else fails closed. Fixtures are the real `npm audit --omit=dev
// --audit-level=high --json` output and lockfile entries of main at 8508fcd (2026-11-17).
// Run: node --test scripts/security/

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { evaluate } from './npm-audit-gate.mjs';

const read = (path) => JSON.parse(readFileSync(new URL(path, import.meta.url), 'utf8'));
const AUDIT = read('./fixtures/audit-2026-11-17.json');
const LOCK = read('./fixtures/lock-2026-11-17.json');
const RECORD = read('../../security/exceptions/GHSA-fc5q-39xc-vjpf.json');

const approved = () => ({
  ...structuredClone(RECORD),
  owner_approval: { approved: true, approved_by: 'synthetic-owner', approved_on: '2026-11-18' },
});
const stillAffected = () => ({ latest: '4.0.7', affected: true });

function run(overrides = {}) {
  const audit = overrides.audit ?? structuredClone(AUDIT);
  return evaluate({
    exitCode: 1,
    stdout: typeof audit === 'string' ? audit : JSON.stringify(audit),
    lock: structuredClone(LOCK),
    records: [approved()],
    today: '2026-11-18',
    patchStatus: stillAffected,
    ...overrides,
    ...(overrides.audit !== undefined
      ? { stdout: typeof audit === 'string' ? audit : JSON.stringify(audit) }
      : {}),
  });
}

function failsWith(result, pattern) {
  assert.equal(result.ok, false, 'expected the gate to fail');
  assert.ok(
    result.errors.some((e) => pattern.test(e)),
    `no error matched ${String(pattern)}: ${JSON.stringify(result.errors)}`,
  );
}

test('the recorded finding, exactly as recorded, passes while approved and unexpired', () => {
  const result = run();
  assert.deepEqual(result.errors, []);
  assert.equal(result.ok, true);
});

test('without owner approval it fails', () => {
  failsWith(run({ records: [structuredClone(RECORD)] }), /not approved by the owner/);
});

test('without any exception record the finding fails', () => {
  failsWith(run({ records: [] }), /not covered by any exception: picomatch/);
});

test('it fails the day after expiry, and passes on the expiry date', () => {
  failsWith(run({ today: '2026-12-16' }), /expired on 2026-12-15/);
  assert.equal(run({ today: '2026-12-15' }).ok, true);
});

test('an unrelated advisory fails, even on a recorded package', () => {
  const audit = structuredClone(AUDIT);
  audit.vulnerabilities.next = {
    name: 'next',
    severity: 'high',
    isDirect: true,
    via: [
      {
        source: 1,
        name: 'next',
        url: 'https://github.com/advisories/GHSA-xxxx-xxxx-xxxx',
        severity: 'high',
        range: '<99',
      },
    ],
    effects: [],
    range: '<99',
    nodes: ['node_modules/next'],
    fixAvailable: true,
  };
  audit.metadata.vulnerabilities.total = 3;
  failsWith(run({ audit }), /not covered by any exception: next/);

  const second = structuredClone(AUDIT);
  second.vulnerabilities.picomatch.via.push({
    source: 2,
    name: 'picomatch',
    url: 'https://github.com/advisories/GHSA-yyyy-yyyy-yyyy',
    severity: 'high',
    range: '<=4.0.7',
  });
  failsWith(run({ audit: second }), /another advisory|expected exactly 1/);
});

test('an extra installation path fails, in the audit or in the lockfile', () => {
  const audit = structuredClone(AUDIT);
  audit.vulnerabilities.picomatch.nodes.push('node_modules/picomatch');
  failsWith(run({ audit }), /installed at/);

  const lock = structuredClone(LOCK);
  delete lock.packages['node_modules/picomatch'].dev; // a production copy elsewhere
  failsWith(run({ lock }), /production copies of picomatch/);

  const another = structuredClone(LOCK);
  another.packages['node_modules/some-runtime-lib'] = {
    version: '1.0.0',
    dependencies: { '@parcel/watcher': '^2.0.0' },
  };
  failsWith(run({ lock: another }), /@parcel\/watcher is required in production by/);
});

test('a changed version or chain fails', () => {
  const lock = structuredClone(LOCK);
  lock.packages['node_modules/@parcel/watcher/node_modules/picomatch'].version = '4.0.6';
  failsWith(run({ lock }), /recorded 4\.0\.7/);

  const intl = structuredClone(LOCK);
  intl.packages['node_modules/next-intl'].version = '4.15.1';
  failsWith(run({ lock: intl }), /next-intl is 4\.15\.1/);

  const audit = structuredClone(AUDIT);
  audit.vulnerabilities['@parcel/watcher'].effects = ['next-intl'];
  failsWith(run({ audit }), /effects/);
});

test('a changed advisory range fails', () => {
  const audit = structuredClone(AUDIT);
  audit.vulnerabilities.picomatch.via[0].range = '<=4.0.9';
  failsWith(run({ audit }), /advisory range is now/);
});

test('a patched release, or a failed check for one, fails', () => {
  failsWith(
    run({ patchStatus: () => ({ latest: '4.0.8', affected: false }) }),
    /patched release exists/,
  );
  failsWith(
    run({ patchStatus: () => ({ error: 'network down' }) }),
    /cannot check for a patched release/,
  );
});

test('audit-service failures and unexpected output fail closed', () => {
  failsWith(run({ audit: 'npm ERR! audit endpoint returned an error' }), /did not return JSON/);
  failsWith(
    run({ audit: { error: { code: 'ENOAUDIT', summary: 'audit endpoint 503' } } }),
    /reported an error/,
  );
  const v1 = structuredClone(AUDIT);
  v1.auditReportVersion = 1;
  failsWith(run({ audit: v1 }), /auditReportVersion/);
  const counts = structuredClone(AUDIT);
  counts.metadata.vulnerabilities.total = 5;
  failsWith(run({ audit: counts }), /metadata reports 5/);
  failsWith(run({ exitCode: 2 }), /exited 2/);
  failsWith(
    run({
      audit: {
        auditReportVersion: 2,
        vulnerabilities: {},
        metadata: { vulnerabilities: { total: 0 } },
      },
      exitCode: 1,
    }),
    /no vulnerabilities/,
  );
});

test('a clean audit passes and asks for the record to be removed', () => {
  const clean = {
    auditReportVersion: 2,
    vulnerabilities: {},
    metadata: { vulnerabilities: { total: 0 } },
  };
  const result = run({ audit: clean, exitCode: 0 });
  assert.equal(result.ok, true);
  assert.ok(result.notes.some((n) => /remove the exception/.test(n)));
});
