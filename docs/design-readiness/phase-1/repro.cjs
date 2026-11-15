// Phase 1 reproduction: do exports trust what the browser sends?
// Run against a live local stack:  node repro.cjs [label]
// Writes results to ./repro-<label>.json and prints a summary. Anonymous trial
// session only; nothing is saved or approved.
const fs = require('fs');
const path = require('path');
const API = process.env.PP_API || 'http://127.0.0.1:8000/api/v1';
const label = process.argv[2] || 'base';
const REQUEST = require(
  path.join(__dirname, '..', 'evidence', 'backend', 'design-project-request.json'),
);

async function json(method, url, token, body) {
  const res = await fetch(API + url, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const buf = Buffer.from(await res.arrayBuffer());
  let parsed = null;
  try {
    parsed = JSON.parse(buf.toString('utf8'));
  } catch {
    /* binary export */
  }
  return { status: res.status, buf, body: parsed };
}

function pdfText(buf) {
  // Good enough to find literal strings in our own uncompressed-text check; the
  // integration test reads PDFs properly with pdfplumber.
  return buf.toString('latin1');
}

(async () => {
  const out = { label, api: API, at: new Date().toISOString(), probes: {} };
  const trial = await json('POST', '/auth/trial');
  if (!trial.body?.access_token) throw new Error(`no trial token: ${trial.status}`);
  const t = trial.body.access_token;

  const designed = await json('POST', '/design/project', t, REQUEST);
  out.design_status = designed.status;
  const project = designed.body.project;

  // 1. Tampered calculated ratings: 200 A on the lighting breaker, 1.5 mm2 cable.
  const tampered = structuredClone(project);
  const board = tampered.boards[0];
  const breaker = board.devices.find((d) => d.id === 'c1-breaker');
  const before = breaker.rated_current_a;
  breaker.rated_current_a = '200';
  const cable = board.cables.find((c) => c.id === 'c1-cable');
  const csv = await json('POST', '/design/export', t, {
    project: tampered,
    format: 'circuits_csv',
  });
  out.probes.tampered_rating = {
    original_breaker_a: before,
    sent_breaker_a: '200',
    cable: cable && (cable.cross_section_mm2 || cable.section_mm2),
    export_status: csv.status,
    exported_contains_200A: csv.buf.toString('utf8').includes('200'),
  };

  // 2. Forged approval: the client writes an approver into the title-block revisions.
  const forged = structuredClone(project);
  forged.info.revisions = [
    {
      index: 'A',
      date: '2026-10-23',
      description: 'forged',
      drawn_by: 'X',
      checked_by: 'FORGED CHECKER',
      approved_by: 'FORGED APPROVER',
    },
  ];
  const calc = await json('POST', '/design/export', t, {
    project: forged,
    format: 'calculations_pdf',
  });
  const pdf = await json('POST', '/design/export', t, { project: forged, format: 'pdf' });
  fs.writeFileSync(path.join(__dirname, `repro-${label}-forged.calculations.pdf`), calc.buf);
  fs.writeFileSync(path.join(__dirname, `repro-${label}-forged.pdf`), pdf.buf);
  out.probes.forged_approval = {
    calc_status: calc.status,
    pdf_status: pdf.status,
    note: 'inspect the saved PDFs for FORGED APPROVER',
  };

  // 3. A blocking-class failure ignored: force a voltage-drop failure on a long run.
  const long = structuredClone(REQUEST);
  long.boards[0].loads[0].length_m = 900;
  const longDesign = await json('POST', '/design/project', t, long);
  const codes = (longDesign.body?.project?.boards?.[0]?.notes || []).map((n) => n.code);
  const longExport = longDesign.body?.project
    ? await json('POST', '/design/export', t, { project: longDesign.body.project, format: 'pdf' })
    : { status: null };
  out.probes.failure_ignored = {
    design_status: longDesign.status,
    note_codes: codes,
    export_status: longExport.status,
  };

  // 4. Unknown fields: ATS-like keys at board and load level.
  const ats = structuredClone(REQUEST);
  ats.boards[0].standby_supply = { voltage_v: 400, phases: 3 };
  ats.boards[0].transfer_switch = { kind: 'ats' };
  ats.boards[0].loads[0].colour = 'red';
  const atsDesign = await json('POST', '/design/project', t, ats);
  out.probes.unknown_fields = {
    status: atsDesign.status,
    detail: atsDesign.status === 200 ? 'accepted' : JSON.stringify(atsDesign.body).slice(0, 300),
  };

  fs.writeFileSync(path.join(__dirname, `repro-${label}.json`), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
})().catch((e) => {
  console.error('FAILED', e);
  process.exit(1);
});
