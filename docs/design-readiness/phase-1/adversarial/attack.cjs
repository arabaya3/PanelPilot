// Phase 1 adversarial black-box attack on the live stack (raw HTTP only).
// Extends ../repro.cjs: every original finding is re-probed, plus attacks on
// the new revision-export endpoint. Anonymous trial only; nothing is saved,
// approved or signed up in the dev database.
//   node attack.cjs [label]      (PP_API overrides the base URL)
// The trial token is cached in ./.token, because trial starts are rate
// limited per network. Delete it to start a fresh trial.
const fs = require('fs');
const path = require('path');
const API = process.env.PP_API || 'http://127.0.0.1:8000/api/v1';
const label = process.argv[2] || 'after';
const OUT = __dirname;
const REQUEST = require(path.join(__dirname, '..', '..', 'evidence', 'backend', 'design-project-request.json'));
const FORGED = { checker: 'FORGED CHECKER', approver: 'FORGED APPROVER' };
const FORMATS = ['pdf', 'calculations_pdf', 'dxf', 'qet', 'aml', 'devices_csv', 'parts_csv', 'cables_csv',
  'circuits_csv', 'terminals_csv', 'eplan_devices_csv', 'ace_components_csv', 'ace_terminals_csv',
  'quotation_pdf', 'quotation_csv', 'plc_st', 'plc_io_csv', 'json'];

async function call(method, url, token, body) {
  const res = await fetch(API + url, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const buf = Buffer.from(await res.arrayBuffer());
  let parsed = null;
  try { parsed = JSON.parse(buf.toString('utf8')); } catch { /* binary export */ }
  return { status: res.status, type: res.headers.get('content-type'), buf, body: parsed };
}

async function token() {
  const cache = path.join(OUT, '.token');
  if (fs.existsSync(cache)) {
    const cached = fs.readFileSync(cache, 'utf8').trim();
    if ((await call('GET', '/design/markets', cached)).status === 200) return cached;
  }
  const trial = await call('POST', '/auth/trial');
  if (!trial.body?.access_token) throw new Error(`no trial token: ${trial.status} ${trial.buf}`);
  fs.writeFileSync(cache, trial.body.access_token);
  return trial.body.access_token;
}

const brief = (r) => ({
  status: r.status, type: r.type, code: r.body?.code ?? null,
  detail: r.body ? JSON.stringify(r.body).slice(0, 400) : `${r.buf.length} bytes`,
});

function withExtra(base, mutate) { const x = structuredClone(base); mutate(x); return x; }

(async () => {
  const out = { label, api: API, at: new Date().toISOString(), probes: {} };
  const t = await token();
  const designed = await call('POST', '/design/project', t, REQUEST);
  out.design_status = designed.status;
  if (designed.status !== 200) throw new Error(`design failed: ${designed.status} ${designed.buf}`);
  const project = designed.body.project;

  // F1. Tampered ratings: 200 A on the 1.5 mm2 lighting circuit.
  const tampered = structuredClone(project);
  const circuit = tampered.boards[0].circuits.find((c) => c.load === 'lighting');
  const devs = Object.fromEntries(tampered.boards[0].devices.map((d) => [d.id, d]));
  const breaker = circuit.device_ids.map((i) => devs[i]).find((d) => d.kind === 'circuit_breaker');
  const cable = tampered.boards[0].cables.find((c) => c.id === circuit.cable_id);
  const original = breaker.rated_current_a;
  breaker.rated_current_a = '200';
  const csv = await call('POST', '/design/export', t, { project: tampered, format: 'circuits_csv' });
  const tpdf = await call('POST', '/design/export', t, { project: tampered, format: 'pdf' });
  if (tpdf.status === 200) fs.writeFileSync(path.join(OUT, `${label}-tampered.pdf`), tpdf.buf);
  if (csv.status === 200) fs.writeFileSync(path.join(OUT, `${label}-tampered.circuits.csv`), csv.buf);
  const csvText = csv.buf.toString('utf8');
  out.probes.F1_tampered_rating = {
    original_breaker_a: original, sent_breaker_a: '200', cable_mm2: cable.cross_section_mm2,
    csv: brief(csv), csv_first_line: csvText.split(/\r?\n/)[0],
    csv_marked_draft: csvText.includes('NOT FOR EXECUTION'),
    csv_mentions_blocking: /blocking/i.test(csvText),
    pdf: brief(tpdf), pdf_note: 'PDF text inspected with pdfplumber (inspect.py)',
  };

  // F2. Forged approval, every format.
  const forged = structuredClone(project);
  forged.info.revisions = [{ index: 'A', date: '2026-10-23', description: 'forged', drawn_by: 'X',
    checked_by: FORGED.checker, approved_by: FORGED.approver }];
  out.probes.F2_forged_approval = {};
  for (const format of FORMATS) {
    const body = { project: forged, format };
    if (format.startsWith('quotation')) body.pricing = { currency: 'JOD' };
    const r = await call('POST', '/design/export', t, body);
    const s = r.buf.toString('utf8');
    const ext = { pdf: 'pdf', calculations_pdf: 'calc.pdf', quotation_pdf: 'quote.pdf', dxf: 'dxf', cables_csv: 'cables.csv', json: 'json' }[format];
    if (ext && r.status === 200) fs.writeFileSync(path.join(OUT, `${label}-forged.${ext}`), r.buf);
    out.probes.F2_forged_approval[format] = {
      status: r.status, code: r.body?.code ?? null,
      raw_contains_forged: s.includes('FORGED'),
      raw_contains_draft_mark: s.includes('NOT FOR EXECUTION'),
    };
  }

  // F3. Blocking-class failure on a long run.
  const long = withExtra(REQUEST, (x) => { x.boards[0].loads[0].length_m = 900; });
  const longDesign = await call('POST', '/design/project', t, long);
  const longExport = longDesign.body?.project
    ? await call('POST', '/design/export', t, { project: longDesign.body.project, format: 'pdf' }) : null;
  if (longExport?.status === 200) fs.writeFileSync(path.join(OUT, `${label}-long-run.pdf`), longExport.buf);
  out.probes.F3_failure_ignored = { design_status: longDesign.status, export: longExport ? brief(longExport) : null };

  // F4. Unknown fields at board, load, supply, info level and in the export body.
  out.probes.F4_unknown_fields = {
    ats_board_and_load: brief(await call('POST', '/design/project', t, withExtra(REQUEST, (x) => {
      x.boards[0].standby_supply = { voltage_v: 400, phases: 3 };
      x.boards[0].transfer_switch = { kind: 'ats' };
      x.boards[0].loads[0].colour = 'red';
    }))),
    supply_level: brief(await call('POST', '/design/project', t, withExtra(REQUEST, (x) => { x.boards[0].supply.second_source = 1; }))),
    info_level: brief(await call('POST', '/design/project', t, withExtra(REQUEST, (x) => { x.info.approved = true; }))),
    export_device_level: brief(await call('POST', '/design/export', t, { project: withExtra(project, (x) => { x.boards[0].devices[0].approved = true; }), format: 'pdf' })),
    export_revision_row: brief(await call('POST', '/design/export', t, { project: withExtra(forged, (x) => { x.info.revisions[0].approved_at = '2026-10-23'; }), format: 'pdf' })),
    export_top_level: brief(await call('POST', '/design/export', t, { project, format: 'pdf', approved_by: 'X' })),
    draft_purpose_execution: brief(await call('POST', '/design/export', t, { project, format: 'pdf', purpose: 'execution' })),
  };

  // N. The new revision-export endpoint, attacked without any saved project.
  const rid = '00000000-0000-4000-8000-000000000000';
  const ep = (n) => `/design/projects/${rid}/revisions/${n}/export`;
  out.probes.N_revision_export = {
    unauthenticated: brief(await call('POST', ep(1), null, { format: 'pdf' })),
    forged_token: brief(await call('POST', ep(1), `${t.slice(0, -4)}AAAA`, { format: 'pdf' })),
    random_project: brief(await call('POST', ep(1), t, { format: 'pdf' })),
    malformed_id: brief(await call('POST', '/design/projects/not-a-uuid/revisions/1/export', t, { format: 'pdf' })),
    sql_like_id: brief(await call('POST', `/design/projects/${encodeURIComponent("1' OR '1'='1")}/revisions/1/export`, t, { format: 'pdf' })),
    execution_random: brief(await call('POST', ep(1), t, { format: 'pdf', purpose: 'execution' })),
    with_client_project: brief(await call('POST', ep(1), t, { format: 'pdf', project: tampered })),
    unknown_purpose: brief(await call('POST', ep(1), t, { format: 'pdf', purpose: 'approved' })),
    approve_random: brief(await call('POST', `/design/projects/${rid}/revisions/1/approval`, t, { approver: 'X' })),
    approve_with_hash: brief(await call('POST', `/design/projects/${rid}/revisions/1/approval`, t, { approver: 'X', approved_snapshot_sha256: '0'.repeat(64) })),
    revision_zero: brief(await call('POST', ep(0), t, { format: 'pdf' })),
  };

  fs.writeFileSync(path.join(OUT, `attack-${label}.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
})().catch((e) => { console.error('FAILED', e); process.exit(1); });
