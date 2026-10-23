// Phase 1 adversarial live attack, part 2: saved-revision paths (signed-up disposable accounts).
// node attack2.cjs [outdir]   (default: this folder; writes attack2.json and exports/). Disposable signed-up accounts, random passwords; no token is stored.
const fs = require('fs');
const path = require('path');
const API = process.env.PP_API || 'http://127.0.0.1:8000/api/v1';
const OUT = process.argv[2] || __dirname;
const REQUEST = require(require('path').join(__dirname, '..', '..', 'evidence', 'backend', 'design-project-request.json'));
fs.mkdirSync(path.join(OUT, 'exports'), { recursive: true });

async function call(method, url, token, body, raw) {
  let res;
  try { res = await fetch(API + url, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: raw !== undefined ? raw : body === undefined ? undefined : JSON.stringify(body),
  }); } catch (e) {
    // A server may close the socket on an oversized body: record it, not abort the run.
    return { status: null, type: null, buf: Buffer.from(String(e.cause?.code || e.message)), body: { code: `network:${e.cause?.code || e.message}` } };
  }
  const buf = Buffer.from(await res.arrayBuffer());
  let parsed = null;
  try { parsed = JSON.parse(buf.toString('utf8')); } catch { /* file */ }
  return { status: res.status, type: res.headers.get('content-type'), buf, body: parsed };
}
const brief = (r) => ({ status: r.status, type: r.type, code: r.body?.code ?? null,
  detail: r.body ? JSON.stringify(r.body).slice(0, 300) : `${r.buf.length} bytes` });

async function account(tag) {
  const email = `e2e-p1a-${tag}-${Date.now()}@example.com`;
  const r = await call('POST', '/auth/signup', null, { email, password: require('crypto').randomBytes(18).toString('base64url'), full_name: 'Adv ' + tag });
  if (!r.body?.access_token) throw new Error(`signup ${r.status} ${r.buf.toString().slice(0, 200)}`);
  return r.body.access_token;
}

const FORMATS = ['pdf', 'calculations_pdf', 'dxf', 'qet', 'aml', 'devices_csv', 'parts_csv', 'cables_csv',
  'circuits_csv', 'terminals_csv', 'eplan_devices_csv', 'ace_components_csv', 'ace_terminals_csv',
  'quotation_pdf', 'quotation_csv', 'plc_st', 'plc_io_csv', 'json'];
const PRICING = { currency: 'JOD' };

(async () => {
  const out = { api: API, at: new Date().toISOString(), probes: {} };
  const a = await account('a');
  const b = await account('b');
  const P = out.probes;

  // Save: smuggle a client design + approval fields.
  P.save_with_client_project = brief(await call('POST', '/design/projects', a, { name: 'ADV', request: REQUEST, project: { boards: [] } }));
  P.save_with_snapshot_field = brief(await call('POST', '/design/projects', a, { name: 'ADV', request: REQUEST, design_snapshot: {}, snapshot_sha256: '0'.repeat(64) }));
  P.save_with_approval_fields = brief(await call('POST', '/design/projects', a, { name: 'ADV', request: REQUEST, approved_by: 'X', approved_snapshot_sha256: '0'.repeat(64) }));
  const reqForged = structuredClone(REQUEST);
  reqForged.info = { ...(reqForged.info || {}), revisions: [{ index: 'A', date: '2026-10-23', description: 'x', drawn_by: 'D', checked_by: 'FORGED CHECKER', approved_by: 'FORGED APPROVER' }] };
  P.save_request_info_revisions_forged = brief(await call('POST', '/design/projects', a, { name: 'ADV', request: reqForged }));
  const reqDesigned = structuredClone(REQUEST); reqDesigned.boards[0].devices = [{ id: 'x', rated_current_a: '200' }];
  P.save_request_with_design_fields = brief(await call('POST', '/design/projects', a, { name: 'ADV', request: reqDesigned }));

  const saved = await call('POST', '/design/projects', a, { name: 'ADV live', request: REQUEST, note: 'r1' });
  P.save_ok = brief(saved);
  const pid = saved.body?.id;
  const rev1 = saved.body?.revisions?.find((r) => r.number === 1) || {};
  P.saved_shape = { keys: Object.keys(saved.body || {}), validated: saved.body?.validated, export_status: saved.body?.export_status,
    snapshot_sha256: saved.body?.snapshot_sha256, rules_version: saved.body?.rules_version, findings: (saved.body?.findings || []).length };
  const ep = (n, p = pid) => `/design/projects/${p}/revisions/${n}/export`;

  // Revision number fuzz.
  P.revnum = {};
  for (const n of ['-1', '0', '99999999999999999999', '2147483648', '1.5', 'abc', '1e3', '%20', '01']) {
    P.revnum[n] = { export: brief(await call('POST', ep(n), a, { format: 'pdf' })), approval: n === '01' ? 'skipped (would approve revision 1)' : brief(await call('POST', `/design/projects/${pid}/revisions/${n}/approval`, a, { approver: 'X' })) };
  }
  // Cross tenant.
  P.cross_tenant = {
    get: brief(await call('GET', `/design/projects/${pid}`, b)),
    export: brief(await call('POST', ep(1), b, { format: 'pdf' })),
    approve: brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, b, { approver: 'B' })),
    revise: brief(await call('POST', `/design/projects/${pid}/revisions`, b, { request: REQUEST })),
    delete: brief(await call('DELETE', `/design/projects/${pid}`, b)),
  };
  // Approval body injection.
  P.approval_injection = {
    extra_hash: brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: 'X', approved_snapshot_sha256: 'f'.repeat(64) })),
    extra_at: brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: 'X', approved_at: '2020-01-01' })),
    empty: brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: '   ' })),
    long: brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: 'X'.repeat(101) })),
  };
  // Export format fuzz.
  P.format_fuzz = {};
  for (const f of ['PDF', 'pdf ', '', null, 7, ['pdf'], 'pdf;rm', '../json', 'execution_pdf', 'svg']) {
    P.format_fuzz[JSON.stringify(f)] = brief(await call('POST', ep(1), a, { format: f }));
  }
  P.format_fuzz.draft_list = brief(await call('POST', '/design/export', a, { project: {}, format: ['pdf', 'dxf'] }));
  // Oversized bodies.
  const big = structuredClone(REQUEST); big.info = { ...(big.info || {}), name: 'N'.repeat(5_000_000) };
  P.oversized_save = brief(await call('POST', '/design/projects', a, { name: 'big', request: big }));
  P.oversized_raw_20mb = brief(await call('POST', ep(1), a, undefined, JSON.stringify({ format: 'pdf', pad: 'x'.repeat(20_000_000) })));
  const many = structuredClone(REQUEST); many.boards[0].loads = Array.from({ length: 2000 }, () => structuredClone(REQUEST.boards[0].loads[0]));
  P.many_loads = brief(await call('POST', '/design/projects', a, { name: 'many', request: many }));

  // Exports: unapproved revision, every format; execution purpose.
  const states = {};
  const dump = async (state, n) => {
    states[state] = {};
    for (const f of FORMATS) {
      const body = { format: f }; if (f.startsWith('quotation')) body.pricing = PRICING;
      const r = await call('POST', ep(n), a, body);
      states[state][f] = brief(r);
      if (r.status === 200) fs.writeFileSync(path.join(OUT, 'exports', `${state}.${f}`), r.buf);
    }
  };
  await dump('unapproved', 1);
  P.execution_unapproved = brief(await call('POST', ep(1), a, { format: 'pdf', purpose: 'execution' }));
  // Draft exports of the same design with forged rows.
  const designed = await call('POST', '/design/project', a, REQUEST);
  const proj = designed.body.project;
  proj.info.revisions = reqForged.info.revisions;
  states.draft = {};
  for (const f of FORMATS) {
    const body = { project: proj, format: f }; if (f.startsWith('quotation')) body.pricing = PRICING;
    const r = await call('POST', '/design/export', a, body);
    states.draft[f] = brief(r);
    if (r.status === 200) fs.writeFileSync(path.join(OUT, 'exports', `draft.${f}`), r.buf);
  }
  // Approve and export.
  P.approve = brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: 'Eng. Live Approver' }));
  P.approve_again = brief(await call('POST', `/design/projects/${pid}/revisions/1/approval`, a, { approver: 'Second' }));
  await dump('approved', 1);
  P.execution_approved = brief(await call('POST', ep(1), a, { format: 'pdf', purpose: 'execution' }));
  P.export_with_client_revision_fields = brief(await call('POST', ep(1), a, { format: 'pdf', approved_by: 'X' }));
  P.get_after_approval = (() => 0)();
  const g = await call('GET', `/design/projects/${pid}`, a);
  P.get_after_approval = { status: g.status, approved_snapshot_sha256: g.body?.approved_snapshot_sha256, snapshot_sha256: g.body?.snapshot_sha256, export_status: g.body?.export_status, validated: g.body?.validated };

  // Replay after a company-profile change.
  const cs = await call('GET', '/design/company-settings', a);
  P.company_settings_get = brief(cs);
  if (cs.status === 200) {
    const changed = { ...cs.body };
    const k = Object.keys(changed).find((x) => typeof changed[x] === 'string' && /name/i.test(x));
    if (k) changed[k] = 'Changed Co ' + Date.now();
    P.company_settings_put = brief(await call('PUT', '/design/company-settings', a, changed));
    P.replay_after_profile_change = brief(await call('POST', ep(1), a, { format: 'pdf' }));
  }
  out.states = states;
  fs.writeFileSync(path.join(OUT, 'attack2.json'), JSON.stringify(out, null, 2));
  console.log('pid', pid ? 'ok' : 'none');
})().catch((e) => { console.error('FAILED', e.message); process.exit(1); });
