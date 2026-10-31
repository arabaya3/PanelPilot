// HTTPS flow on the production build behind Caddy: approval, export, legacy recovery.
const { chromium, request } = require('playwright');
const { execFileSync } = require('child_process');
const base = 'https://localhost:8443';
const OUT = process.argv[2];
const log = [];
const ok = (name, cond, info = '') =>
  log.push(`${cond ? 'PASS' : 'FAIL'} ${name}${info ? ' — ' + info : ''}`);
const sql = (q) =>
  execFileSync('psql', [
    '-qtA',
    'postgresql://panelpilot:throwaway@localhost/panelpilot_phase1_https',
    '-c',
    q,
  ])
    .toString()
    .trim();
(async () => {
  process.on('unhandledRejection', (e) => {
    console.log(log.join('\n'));
    console.log('ERR', String(e).slice(0, 400));
    process.exit(1);
  });
  const api = await request.newContext({
    ignoreHTTPSErrors: true,
    extraHTTPHeaders: { origin: base },
  });
  const email = `https-flow-${Date.now()}@synthetic-example.com`;
  const r = await api.post(base + '/api/v1/auth/signup', {
    data: { email, password: 'throwaway-pass-123', full_name: 'Synthetic Reviewer' },
  });
  ok('signup through the proxy', r.status() === 201, String(r.status()));
  const tok = (await r.json()).access_token;
  const req = {
    info: { name: 'HTTPS flow' },
    boards: [
      {
        name: 'MCC1',
        loads: [
          { description: 'Pump', load: 'motor', power_kw: '5.5', phases: 3, starter: 'dol' },
          { description: 'Sockets', load: 'socket', power_kw: '3' },
        ],
      },
    ],
  };
  const saved = await (
    await api.post(base + '/api/v1/design/projects', {
      headers: { authorization: 'Bearer ' + tok },
      data: { name: 'HTTPS flow', request: req },
    })
  ).json();
  const b = await chromium.launch();
  const ctx = await b.newContext({
    ignoreHTTPSErrors: true,
    acceptDownloads: true,
    viewport: { width: 1280, height: 900 },
  });
  const p = await ctx.newPage();
  await p.goto(base + '/design', { waitUntil: 'networkidle' });
  await p.getByRole('button', { name: 'Sign in to save to your account' }).click();
  await p.locator('input[type=email]').fill(email);
  await p.locator('input[type=password]').fill('throwaway-pass-123');
  await p.locator('form button[type=submit]').click();
  await p.waitForTimeout(3000);
  const cookie = (await ctx.cookies()).find((c) => c.name.includes('pp_refresh'));
  ok(
    'refresh cookie is __Host-, Secure, HttpOnly, Strict, Path=/',
    cookie &&
      cookie.name === '__Host-pp_refresh' &&
      cookie.secure &&
      cookie.httpOnly &&
      cookie.sameSite === 'Strict' &&
      cookie.path === '/',
    JSON.stringify(
      cookie && [cookie.name, cookie.secure, cookie.httpOnly, cookie.sameSite, cookie.path],
    ),
  );
  const open = async () => {
    await p.getByRole('tab', { name: 'Overview' }).click();
    const b = p
      .locator('[data-testid^="project-"]', { hasText: 'HTTPS flow' })
      .first()
      .getByRole('button', { name: 'Open' });
    await b.waitFor({ timeout: 10000 }).catch(() => {});
    if ((await b.count()) && (await b.isEnabled())) {
      await b.click();
    } else {
      log.push('NOTE open: the project was already open after reload');
    }
    await p.waitForTimeout(2500);
  };
  await open();
  await p.getByRole('button', { name: 'Approve revision 1' }).click();
  await p.waitForTimeout(2500);
  const download = async (fmt, file) => {
    await p.getByRole('tab', { name: 'Review & export' }).click();
    await p.waitForTimeout(500);
    const [d] = await Promise.all([
      p.waitForEvent('download', { timeout: 15000 }).catch(() => null),
      p.getByTestId(`revision-export-${fmt}`).click(),
    ]);
    if (d) await d.saveAs(`${OUT}/${file}`);
    return d !== null;
  };
  ok('approved: drawing PDF downloads', await download('pdf', 'rev1-approved.pdf'));
  ok('approved: DXF downloads', await download('dxf', 'rev1-approved.dxf'));
  ok('approved: JSON downloads', await download('json', 'rev1-approved.json'));
  const status = JSON.parse(require('fs').readFileSync(`${OUT}/rev1-approved.json`)).export_status;
  ok(
    'JSON says REVIEW APPROVED with design hash and renderer',
    status.label.startsWith('REVIEW APPROVED') &&
      /renderer \d{4}\.\d\d\.\d\d\.\d/.test(status.identity),
    status.identity,
  );
  await p.screenshot({ path: `${OUT}/1-approved-review.png`, fullPage: true });
  const qetDisabled = await p.getByTestId('revision-export-qet').isDisabled();
  ok('QElectroTech stays unavailable', qetDisabled);
  await p.getByTestId('execution-export').click();
  await p.waitForTimeout(1500);
  const reviewText = await p.getByTestId('view-review').innerText();
  ok(
    'execution export refused in the page',
    /No execution package can be exported yet|execution package/i.test(reviewText),
  );
  // Legacy: the approval loses its renderer record, as an approval made before c5d1e8a3f7b2.
  sql(
    `update design_project_revisions set approved_renderer_version = null where project_id = '${saved.id}'`,
  );
  await p.reload({ waitUntil: 'networkidle' });
  await p.waitForTimeout(2500);
  ok(
    'reload keeps the sign-in (cookie restore through the proxy)',
    await p.getByRole('button', { name: 'Sign out', exact: true }).isVisible(),
  );
  await open();
  const body = await p.locator('main').innerText();
  ok(
    'legacy approval shown as no longer counting, naming the renderer',
    body.includes('no longer counts') && body.includes('how exports are worded and drawn'),
  );
  await p.screenshot({ path: `${OUT}/2-legacy-not-current.png`, fullPage: true });
  const got = await download('pdf', 'rev1-legacy.pdf');
  const refusal = await p.getByTestId('view-review').innerText();
  ok(
    'legacy revision export refused, no file',
    !got && /rules or company profile changed|save a new revision/i.test(refusal),
  );
  await p.screenshot({ path: `${OUT}/3-legacy-export-refused.png`, fullPage: true });
  // Recovery in the page: save as a new revision, approve it, export.
  await p.getByRole('tab', { name: 'Overview' }).click();
  await p.waitForTimeout(500);
  const saveBtn = p.getByRole('button', { name: 'Save as new revision' });
  await saveBtn.click();
  await p.waitForTimeout(3000);
  await p.getByRole('button', { name: 'Approve revision 2' }).click();
  await p.waitForTimeout(2500);
  ok('revision 2 approved: PDF downloads', await download('pdf', 'rev2-approved.pdf'));
  ok('revision 2: JSON downloads', await download('json', 'rev2-approved.json'));
  const s2 = JSON.parse(require('fs').readFileSync(`${OUT}/rev2-approved.json`)).export_status;
  ok(
    'revision 2 export is REVIEW APPROVED, revision 02',
    s2.label.startsWith('REVIEW APPROVED') && s2.identity.includes('revision 02'),
    s2.identity,
  );
  const rows = sql(
    `select number || ':' || (approved_at is not null) || ':' || coalesce(approved_renderer_version,'NULL') from design_project_revisions where project_id='${saved.id}' order by number`,
  );
  ok(
    'history keeps revision 1 approved (no renderer) beside revision 2',
    rows.split('\n').length === 2 && rows.startsWith('1:true:NULL'),
    rows.replace('\n', ' '),
  );
  await p.screenshot({ path: `${OUT}/4-recovered.png`, fullPage: true });
  await b.close();
  console.log(log.join('\n'));
})();
