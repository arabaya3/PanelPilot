// Phase 1 final attack run against the local stack (adapted from ../adversarial/attack2.cjs and ../repro.cjs).
// node final-attack.cjs            -> writes results.json and exports/ here.
// Credentials go only to .test-accounts.local (git-excluded); tokens and cookies stay in memory.
// Set SKIP_ROTATION=1 to skip the > 5 min genuine-rotation wait.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const API = process.env.PP_API || 'http://127.0.0.1:8000/api/v1';
const WEB = process.env.PP_WEB || 'http://127.0.0.1:3000/api/v1';
const ORIGIN = 'http://127.0.0.1:3000';
const OUT = __dirname;
const EXP = path.join(OUT, 'exports');
const REQUEST = require(path.join(__dirname, '..', '..', 'evidence', 'backend', 'design-project-request.json'));
const ACCOUNTS = path.join(OUT, '.test-accounts.local');
fs.mkdirSync(EXP, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// The design endpoints are throttled (DESIGN_POLICY, 240 per 5 min per tenant): a 429 there is the
// harness exceeding the budget, not an attack result, so it is waited out (Retry-After) and retried.
async function call(base, method, url, opts = {}) {
  for (let attempt = 0; ; attempt++) {
    const r = await call1(base, method, url, opts);
    if (r.status !== 429 || url.startsWith('/auth/') || attempt >= 10) return r;
    throttled++;
    await sleep(1000 * Math.min(120, Number(r.retryAfter) || 30));
  }
}
let throttled = 0;
async function call1(base, method, url, { token, cookie, body, headers } = {}) {
  let res;
  try {
    res = await fetch(base + url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(cookie ? { Cookie: `pp_refresh=${cookie}` } : {}),
        ...(headers || {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    return { status: null, buf: Buffer.from(''), body: { code: `network:${e.cause?.code || e.message}` }, setCookie: [] };
  }
  const buf = Buffer.from(await res.arrayBuffer());
  let parsed = null;
  try { parsed = JSON.parse(buf.toString('utf8')); } catch { /* file */ }
  return { status: res.status, type: res.headers.get('content-type'), buf, body: parsed, setCookie: res.headers.getSetCookie(), retryAfter: res.headers.get('retry-after') };
}
const api = (m, u, o) => call(API, m, u, o);
const SESSION_HDR = { 'X-PanelPilot-CSRF': '1', Origin: ORIGIN };
const sess = (m, u, o = {}) => call(WEB, m, u, { ...o, headers: { ...SESSION_HDR, ...(o.headers || {}) } });
function cookieOf(r) {
  const c = r.setCookie.find((x) => x.startsWith('pp_refresh='));
  if (!c) return { value: undefined, attrs: null };
  const value = c.split(';')[0].slice('pp_refresh='.length);
  // Attributes only, never the value.
  const attrs = c.split(';').slice(1).map((s) => s.trim()).filter((s) => !/^expires=/i.test(s));
  return { value: value || null, attrs };
}

const results = [];
function rec(id, attack, expected, r, pass, extra) {
  const row = {
    id, attack, expected,
    status: r?.status ?? null,
    code: r?.body?.code ?? null,
    actual: extra ?? (r ? `${r.status}${r.body?.code ? ' ' + r.body.code : ''}` : ''),
    result: pass ? 'PASS' : 'FAIL',
  };
  results.push(row);
  console.log(`${row.result} ${id} ${attack} -> ${row.actual}`);
  return pass;
}
const is = (r, s, code) => r.status === s && (code === undefined || r.body?.code === code);

const creds = [];
async function signup(n) {
  const email = `e2e-p1-final-attack-${n}-${Date.now()}@example.com`;
  const password = crypto.randomBytes(18).toString('base64url');
  for (;;) {
    const r = await sess('POST', '/auth/signup', { body: { email, password, full_name: `Final Attack ${n}` } });
    if (r.status === 429) { console.log('signup 429, waiting 60 s'); await sleep(60_000); continue; }
    if (r.status !== 201 || !r.body?.access_token) throw new Error(`signup ${r.status} ${r.body?.code}`);
    creds.push({ email, password });
    fs.writeFileSync(ACCOUNTS, creds.map((c) => `${c.email}\t${c.password}`).join('\n') + '\n');
    return { email, password, token: r.body.access_token, cookie: cookieOf(r).value, cookieAttrs: cookieOf(r).attrs };
  }
}
async function login(acct) {
  for (;;) {
    const r = await sess('POST', '/auth/login', { body: { email: acct.email, password: acct.password } });
    if (r.status === 429) { await sleep(30_000); continue; }
    if (r.status !== 200) throw new Error(`login ${r.status} ${r.body?.code}`);
    return { token: r.body.access_token, cookie: cookieOf(r).value };
  }
}
const save = (f, buf) => fs.writeFileSync(path.join(EXP, f), buf);
const FORMATS = ['pdf', 'calculations_pdf', 'dxf', 'qet', 'aml', 'devices_csv', 'parts_csv', 'cables_csv', 'circuits_csv',
  'terminals_csv', 'eplan_devices_csv', 'ace_components_csv', 'ace_terminals_csv', 'quotation_pdf', 'quotation_csv',
  'plc_st', 'plc_io_csv', 'json'];
const UNPROTECTED = ['qet', 'aml', 'devices_csv', 'parts_csv', 'cables_csv', 'circuits_csv', 'terminals_csv',
  'eplan_devices_csv', 'ace_components_csv', 'ace_terminals_csv', 'quotation_csv', 'plc_st', 'plc_io_csv'];
const PRICING = { currency: 'JOD' };
const withPricing = (f, b) => (f.startsWith('quotation') ? { ...b, pricing: PRICING } : b);
const FORGED = [{ index: 'A', date: '2026-10-23', description: 'forged', drawn_by: 'D', checked_by: 'FORGED CHECKER', approved_by: 'FORGED APPROVER' }];

(async () => {
  const meta = { api: API, web: WEB, at: new Date().toISOString() };
  const A = await signup(1);
  const B = await signup(2);
  meta.cookie_attributes = A.cookieAttrs;
  meta.signup_body_keys = 'access_token only (refresh in cookie)';

  // ---- 1. Snapshot integrity ------------------------------------------------
  const saved = await api('POST', '/design/projects', { token: A.token, body: { name: 'Final attack', request: REQUEST, note: 'r1' } });
  const pid = saved.body?.id;
  const rev1 = saved.body;
  meta.saved = { status: saved.status, export_status: rev1?.export_status, validated: rev1?.validated, rules_version: rev1?.rules_version, has_sha: !!rev1?.snapshot_sha256 };
  meta.snapshot_prefix = rev1?.snapshot_sha256?.slice(0, 12);
  rec('1.1', 'save project (revision 1)', '201 with snapshot hash', saved, saved.status === 201 && !!rev1?.snapshot_sha256);
  const ep = (n, p = pid) => `/design/projects/${p}/revisions/${n}/export`;
  const designed = await api('POST', '/design/project', { token: A.token, body: REQUEST });
  const project = designed.body.project;
  let r = await api('POST', ep(1), { token: A.token, body: { format: 'pdf', project } });
  rec('1.2a', 'revision export with client-supplied project', '422', r, r.status === 422);
  r = await api('POST', ep(1), { token: A.token, body: { format: 'pdf', snapshot_sha256: '0'.repeat(64) } });
  rec('1.2b', 'revision export with tampered snapshot_sha256', '422', r, r.status === 422);
  r = await api('POST', ep(1), { token: A.token, body: { format: 'pdf', approved_by: 'X', checked_by: 'Y' } });
  rec('1.2c', 'revision export with approved_by/checked_by', '422', r, r.status === 422);
  r = await api('POST', ep(1), { token: A.token, body: { format: 'pdf', rules_version: 'x' } });
  rec('1.2d', 'revision export with rules_version', '422', r, r.status === 422);
  const revPdf = await api('POST', ep(1), { token: A.token, body: { format: 'pdf' } });
  const revJson = await api('POST', ep(1), { token: A.token, body: { format: 'json' } });
  if (revPdf.status === 200) save('rev1-unapproved.pdf', revPdf.buf);
  if (revJson.status === 200) save('rev1-unapproved.json', revJson.buf);
  const rj = revJson.buf.toString('utf8');
  rec('1.3', 'revision export carries snapshot hash and rules version (JSON; PDF checked by inspect.py)', '200, hash and rules in file', revJson,
    revJson.status === 200 && rj.includes(rev1.snapshot_sha256.slice(0, 12)) && rj.includes(String(rev1.rules_version)),
    `pdf ${revPdf.status}, json ${revJson.status}, json has hash=${rj.includes(rev1.snapshot_sha256.slice(0, 12))} rules=${rj.includes(String(rev1.rules_version))}`);

  const tampered = structuredClone(project);
  const brd = tampered.boards[0];
  const breaker = brd.devices.find((d) => d.id === 'c1-breaker');
  breaker.rated_current_a = '200';
  const cable = brd.cables.find((c) => c.id === 'c1-cable');
  for (const k of Object.keys(cable)) if (/mm2|section/.test(k)) cable[k] = '1.5';
  tampered.info.revisions = FORGED;
  const dPdf = await api('POST', '/design/export', { token: A.token, body: { project: tampered, format: 'pdf' } });
  const dCalc = await api('POST', '/design/export', { token: A.token, body: { project: tampered, format: 'calculations_pdf' } });
  const dJson = await api('POST', '/design/export', { token: A.token, body: { project: tampered, format: 'json' } });
  if (dPdf.status === 200) save('draft-tampered-forged.pdf', dPdf.buf);
  if (dCalc.status === 200) save('draft-tampered-forged.calculations.pdf', dCalc.buf);
  if (dJson.status === 200) save('draft-tampered-forged.json', dJson.buf);
  const dj = dJson.body || {};
  const djs = dJson.buf.toString('utf8');
  const blockingInJson = JSON.stringify(dj.findings || dj).match(/"blocking"\s*:\s*true|"severity"\s*:\s*"blocking"/) !== null;
  rec('1.4', 'draft export, 200 A breaker on 1.5 mm2: DRAFT-marked with blocking finding (JSON; PDF by inspect.py)', '200, draft status, blocking finding', dJson,
    dJson.status === 200 && /^DRAFT/.test(String(dj.export_status?.label ?? '')) && blockingInJson,
    `pdf ${dPdf.status}, calc ${dCalc.status}, json ${dJson.status} label=${dj.export_status?.label} blocking=${blockingInJson}`);
  rec('1.5', 'forged approved_by/checked_by in draft never printed (JSON; PDFs by inspect.py)', 'absent', dJson,
    !/FORGED/.test(djs), `json contains FORGED=${/FORGED/.test(djs)}`);
  meta.cable_fields_tampered = Object.keys(cable).filter((k) => /mm2|section/.test(k));

  // ---- 2. Server-owned approval --------------------------------------------
  const appr = `/design/projects/${pid}/revisions/1/approval`;
  r = await api('POST', appr, { token: A.token, body: { approver: 'X', approved_snapshot_sha256: 'f'.repeat(64) } });
  rec('2.1a', 'approval with extra field approved_snapshot_sha256', '422', r, r.status === 422);
  r = await api('POST', appr, { token: A.token, body: { approver: 'X', approved_at: '2020-01-01', approved_account: 'x@y.z' } });
  rec('2.1b', 'approval with extra approved_at/approved_account', '422', r, r.status === 422);
  r = await api('POST', appr, { token: A.token, body: { approver: '   ' } });
  rec('2.2', 'whitespace approver', '422', r, r.status === 422);
  // The JSON/PDF carry a 12-hex prefix of the snapshot hash ('design 40beff92fcac').
  // Unapproved state of every format, for 2.5 (and kept for inspection).
  const unapprovedFiles = {};
  for (const f of ['pdf', 'calculations_pdf', 'quotation_pdf', 'dxf', 'json']) {
    const x = await api('POST', ep(1), { token: A.token, body: withPricing(f, { format: f }) });
    unapprovedFiles[f] = x.status;
    if (x.status === 200) save(`rev1-unapproved.${f.replace('_pdf', '.pdf')}`, x.buf);
  }
  const unapprovedText = ['pdf', 'calculations_pdf', 'quotation_pdf', 'dxf', 'json'].map((f) => {
    const p = path.join(EXP, `rev1-unapproved.${f.replace('_pdf', '.pdf')}`);
    return fs.existsSync(p) ? fs.readFileSync(p).toString('latin1') : '';
  }).join('\n');
  rec('2.5', 'unapproved revision never says REVIEW APPROVED (DXF/JSON raw; PDFs by inspect.py)', 'absent', null,
    !/REVIEW APPROVED/.test(unapprovedText), `statuses ${JSON.stringify(unapprovedFiles)}, stamp REVIEW APPROVED (case-sensitive; the DXF title-block field label 'Review approved' is not the stamp) =${/REVIEW APPROVED/.test(unapprovedText)}`);
  const ok = await api('POST', appr, { token: A.token, body: { approver: 'Eng. Final Approver' } });
  rec('2.3a', 'first approval', '200', ok, ok.status === 200);
  r = await api('POST', appr, { token: A.token, body: { approver: 'Second' } });
  rec('2.3b', 'second approval', '409', r, r.status === 409);
  const approvedText = [];
  const approvedStatus = {};
  for (const f of ['pdf', 'calculations_pdf', 'quotation_pdf', 'dxf', 'json']) {
    const x = await api('POST', ep(1), { token: A.token, body: withPricing(f, { format: f }) });
    approvedStatus[f] = x.status;
    if (x.status === 200) { save(`rev1-approved.${f.replace('_pdf', '.pdf')}`, x.buf); approvedText.push(x.buf.toString('latin1')); }
  }
  const aj = fs.existsSync(path.join(EXP, 'rev1-approved.json')) ? fs.readFileSync(path.join(EXP, 'rev1-approved.json'), 'utf8') : '';
  rec('2.4', 'approved export shows approver, hash, rules; no "@" in JSON (PDFs by inspect.py)', 'approver+hash+rules present, no @', null,
    aj.includes('Eng. Final Approver') && aj.includes(rev1.snapshot_sha256.slice(0, 12)) && aj.includes(String(rev1.rules_version)) && !aj.includes('@'),
    `statuses ${JSON.stringify(approvedStatus)}; json approver=${aj.includes('Eng. Final Approver')} hash=${aj.includes(rev1.snapshot_sha256.slice(0, 12))} rules=${aj.includes(String(rev1.rules_version))} at=${aj.includes('@')}`);
  meta.approval_response_contains_at = JSON.stringify(ok.body || {}).includes('@');
  meta.approval_response_contains_account_email = JSON.stringify(ok.body || {}).includes(A.email);

  // ---- 3. Tenant isolation ---------------------------------------------------
  const bodies = [];
  const t = async (id, label, m, u, body) => {
    const x = await api(m, u, { token: B.token, body });
    bodies.push(x.buf.toString('utf8'));
    rec(id, `B -> A's project: ${label}`, '404', x, x.status === 404);
  };
  await t('3.1', 'GET', 'GET', `/design/projects/${pid}`);
  await t('3.2', 'export pdf', 'POST', ep(1), { format: 'pdf' });
  await t('3.3', 'approve', 'POST', `/design/projects/${pid}/revisions/1/approval`, { approver: 'B' });
  await t('3.4', 'revise', 'POST', `/design/projects/${pid}/revisions`, { request: REQUEST });
  await t('3.5', 'delete', 'DELETE', `/design/projects/${pid}`);
  await t('3.6', 'quotation_pdf via revision export', 'POST', ep(1), { format: 'quotation_pdf', pricing: PRICING });
  const lst = await api('GET', '/design/projects', { token: B.token });
  bodies.push(lst.buf.toString('utf8'));
  rec('3.7', 'B project list', '200, empty', lst, lst.status === 200 && (lst.body?.items || lst.body?.projects || []).length === 0,
    `${lst.status} items=${(lst.body?.items || lst.body?.projects || []).length}`);
  const leak = bodies.join('\n');
  rec('3.8', 'B receives no approval identity', 'no approver/email in any B response', null,
    !leak.includes('Eng. Final Approver') && !leak.includes(A.email) && !leak.includes('@'),
    `approver=${leak.includes('Eng. Final Approver')} email=${leak.includes(A.email)}`);
  const still = await api('GET', `/design/projects/${pid}`, { token: A.token });
  meta.a_project_after_b_delete = still.status;

  // ---- 5. Execution refusal -----------------------------------------------
  // Revision 2 (unapproved) for the "not approved" case.
  const rev2 = await api('POST', `/design/projects/${pid}/revisions`, { token: A.token, body: { request: REQUEST, note: 'r2' } });
  const n2 = rev2.body?.revisions ? Math.max(...rev2.body.revisions.map((x) => x.number)) : 2;
  for (const [label, n] of [['approved rev 1', 1], [`unapproved rev ${n2}`, n2]]) {
    const bad = [];
    for (const f of FORMATS) {
      const x = await api('POST', ep(n), { token: A.token, body: withPricing(f, { format: f, purpose: 'execution' }) });
      if (!(x.status === 409 && x.body?.code === 'execution_package_incomplete' && x.body)) bad.push(`${f}:${x.status}`);
    }
    rec(`5.1-${n}`, `purpose=execution, ${label}, all ${FORMATS.length} formats`, '409 execution_package_incomplete, no file', null, bad.length === 0, bad.length ? bad.join(' ') : `all 409 execution_package_incomplete`);
  }
  for (const [label, run] of [
    ['draft /design/export', (f) => api('POST', '/design/export', { token: A.token, body: withPricing(f, { project, format: f }) })],
    ['revision export (approved rev 1)', (f) => api('POST', ep(1), { token: A.token, body: withPricing(f, { format: f }) })],
    [`revision export (unapproved rev ${n2})`, (f) => api('POST', ep(n2), { token: A.token, body: withPricing(f, { format: f }) })],
  ]) {
    const bad = [];
    for (const f of UNPROTECTED) {
      const x = await run(f);
      if (!(x.status === 409 && x.body?.code === 'export_format_unprotected')) bad.push(`${f}:${x.status}`);
    }
    rec(`5.2 ${label}`, `${UNPROTECTED.length} CSV/QET/AML/PLC formats on ${label}`, '409 export_format_unprotected', null, bad.length === 0, bad.length ? bad.join(' ') : 'all 409 export_format_unprotected');
  }
  r = await api('POST', '/design/plc', { token: A.token, body: { project } });
  rec('5.3', '/design/plc', '409 export_format_unprotected', r, is(r, 409, 'export_format_unprotected'));

  // ---- 6. Quotation estimate ---------------------------------------------
  const tq = structuredClone(project);
  tq.boards[0].devices.find((d) => d.id === 'c1-breaker').rated_current_a = '200';
  r = await api('POST', '/design/quotation', { token: A.token, body: { project: tq, pricing: PRICING } });
  rec('6.1', '/design/quotation with tampered project', '200 provisional_estimate, blocking true', r,
    r.status === 200 && r.body?.status === 'provisional_estimate' && r.body?.blocking === true, `${r.status} status=${r.body?.status} blocking=${r.body?.blocking}`);
  const oob = {};
  for (const [k, v] of [['markup_percent', 501], ['vat_percent', 101], ['vat_percent', -1], ['labour_per_board', 1e30], ['currency', 'jod']]) {
    const x = await api('POST', '/design/quotation', { token: A.token, body: { project, pricing: { ...PRICING, [k]: v } } });
    oob[`${k}=${v}`] = x.status;
  }
  rec('6.2', 'out-of-bound pricing', '422 each', null, Object.values(oob).every((s) => s === 422), JSON.stringify(oob));

  // ---- 7. Concurrency ----------------------------------------------------
  const par = await Promise.all(Array.from({ length: 4 }, (_, i) =>
    api('POST', `/design/projects/${pid}/revisions`, { token: A.token, body: { request: REQUEST, note: `par${i}` } })));
  const nums = par.map((x) => (x.body?.revisions ? Math.max(...x.body.revisions.map((y) => y.number)) : x.status));
  rec('7.1', '4 parallel revision saves', '4 x 201, distinct numbers', null,
    par.every((x) => x.status === 201) && new Set(nums).size === 4, `statuses ${par.map((x) => x.status)} numbers ${nums}`);
  const target = Math.max(...nums.filter((x) => typeof x === 'number'));
  const ap = await Promise.all(Array.from({ length: 3 }, (_, i) =>
    api('POST', `/design/projects/${pid}/revisions/${target}/approval`, { token: A.token, body: { approver: `Par ${i}` } })));
  const st = ap.map((x) => x.status).sort();
  rec('7.2', `3 parallel approvals of rev ${target}`, 'one 200, two 409', null, JSON.stringify(st) === '[200,409,409]', `statuses ${st}`);

  // ---- 4. Logout and replay -------------------------------------------------
  // A1 = signup session; A2, A3 = logins.
  const A2 = await login(A);
  const A3 = await login(A);
  r = await call(WEB, 'POST', '/auth/session/refresh', { cookie: A.cookie, headers: { Origin: ORIGIN } });
  rec('4.7a', 'refresh without X-PanelPilot-CSRF (trusted Origin)', '403 csrf_header_missing', r, is(r, 403, 'csrf_header_missing'));
  r = await call(WEB, 'POST', '/auth/session/refresh', { cookie: A.cookie, headers: { 'X-PanelPilot-CSRF': '1', Origin: 'https://evil.example' } });
  rec('4.7b', 'refresh with foreign Origin', '403 csrf_origin_refused', r, is(r, 403, 'csrf_origin_refused'));
  r = await call(API, 'POST', '/auth/session/refresh', { cookie: A.cookie, headers: { 'X-PanelPilot-CSRF': '1', Origin: 'https://evil.example' } });
  rec('4.7c', 'refresh with foreign Origin, direct to API', '403 csrf_origin_refused', r, is(r, 403, 'csrf_origin_refused'));

  const conc = await Promise.all(Array.from({ length: 5 }, () => sess('POST', '/auth/session/refresh', { cookie: A3.cookie })));
  const concCookies = new Set(conc.map((x) => cookieOf(x).value));
  rec('7.3', '5 parallel refreshes, same cookie', '5 x 200', null, conc.every((x) => x.status === 200),
    `statuses ${conc.map((x) => x.status)}; distinct cookies set=${concCookies.size}`);
  const A3cookie = cookieOf(conc[0]).value || A3.cookie;
  const A3token = conc[0].body?.access_token || A3.token;

  r = await sess('POST', '/auth/session/logout', { token: A.token, cookie: A.cookie });
  rec('4.1', 'logout session 1', '204', r, r.status === 204);
  r = await api('GET', '/design/projects', { token: A.token });
  rec('4.2', 'replay session-1 access token', '401 session_ended', r, is(r, 401, 'session_ended'));
  r = await sess('POST', '/auth/session/refresh', { cookie: A.cookie });
  rec('4.3', 'replay session-1 refresh cookie', '401', r, r.status === 401);
  const s2a = await api('GET', '/design/projects', { token: A2.token });
  const s2b = await sess('POST', '/auth/session/refresh', { cookie: A2.cookie, token: A2.token });
  rec('4.4', 'session 2 survives logout of session 1', '200 / 200', s2a, s2a.status === 200 && s2b.status === 200, `api ${s2a.status}, refresh ${s2b.status}`);
  const A2token = s2b.body?.access_token || A2.token;
  const A2cookie = cookieOf(s2b).value || A2.cookie;
  r = await sess('POST', '/auth/session/logout-everywhere', { token: A2token, cookie: A2cookie });
  const e2 = await api('GET', '/design/projects', { token: A2token });
  const e3 = await api('GET', '/design/projects', { token: A3token });
  const e2r = await sess('POST', '/auth/session/refresh', { cookie: A2cookie });
  const e3r = await sess('POST', '/auth/session/refresh', { cookie: A3cookie });
  rec('4.5', 'logout-everywhere ends sessions 2 and 3', '204; then 401 for both tokens and cookies', r,
    r.status === 204 && [e2, e3, e2r, e3r].every((x) => x.status === 401),
    `logout ${r.status}; tokens ${e2.status}/${e3.status} (${e2.body?.code}/${e3.body?.code}); cookies ${e2r.status}/${e3r.status}`);

  // Genuine rotation on account B: S1 is rotated after ROTATE_AFTER, its stale cookie replayed.
  if (!process.env.SKIP_ROTATION) {
    const S1 = await login(B);
    const S2 = await login(B);
    const young = await sess('POST', '/auth/session/refresh', { cookie: S1.cookie });
    meta.young_refresh_keeps_cookie = cookieOf(young).value === S1.cookie;
    console.log('waiting 310 s for ROTATE_AFTER (5 min)');
    await sleep(310_000);
    const rot = await sess('POST', '/auth/session/refresh', { cookie: S1.cookie });
    const c1 = cookieOf(rot).value;
    const rotated = rot.status === 200 && !!c1 && c1 !== S1.cookie;
    rec('4.6a', 'genuine rotation after > 5 min', '200, new cookie', rot, rotated, `${rot.status}, cookie changed=${rotated}`);
    await sleep(12_000); // past REFRESH_REUSE_GRACE (10 s)
    const stale = await sess('POST', '/auth/session/refresh', { cookie: S1.cookie });
    const after = await sess('POST', '/auth/session/refresh', { cookie: c1 });
    const tok = await api('GET', '/design/projects', { token: rot.body?.access_token });
    const other = await sess('POST', '/auth/session/refresh', { cookie: S2.cookie });
    const otherApi = await api('GET', '/design/projects', { token: S2.token });
    rec('4.6b', 'stale cookie replayed after rotation ends only that session', 'stale 401; S1 live cookie+token 401; S2 200',
      stale, stale.status === 401 && after.status === 401 && tok.status === 401 && other.status === 200 && otherApi.status === 200,
      `stale ${stale.status} ${stale.body?.code}; S1 new cookie ${after.status}; S1 token ${tok.status}; S2 refresh ${other.status}; S2 api ${otherApi.status}`);
  }

  meta.design_429_retries = throttled;
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ meta, results }, null, 2));
  console.log(`done: ${results.filter((x) => x.result === 'FAIL').length} FAIL of ${results.length}`);
})().catch((e) => {
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify({ error: e.message, results }, null, 2));
  console.error('FAILED', e.message);
  process.exit(1);
});
