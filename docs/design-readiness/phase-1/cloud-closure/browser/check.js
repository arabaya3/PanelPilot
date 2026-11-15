const { chromium } = require('playwright');
const OUT = '/home/user/PanelPilot/docs/design-readiness/phase-1/cloud-closure/browser';
const log = [];
const ok = (l, name, cond, info = '') => {
  log.push(`${cond ? 'PASS' : 'FAIL'} [${l}] ${name}${info ? ' — ' + info : ''}`);
};
(async () => {
  process.on('unhandledRejection', (e) => {
    console.log(log.join('\n'));
    console.log('ERR', String(e).slice(0, 300));
    process.exit(1);
  });
  const b = await chromium.launch();
  for (const l of ['en', 'ar', 'he']) {
    const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript((loc) => {
      try {
        localStorage.setItem('panelpilot-locale', loc);
      } catch {}
    }, l);
    const p = await ctx.newPage();
    await p.goto('http://localhost:3000/design', { waitUntil: 'networkidle' });
    ok(
      l,
      'html lang/dir',
      true,
      `lang=${await p.getAttribute('html', 'lang')} dir=${await p.getAttribute('html', 'dir')}`,
    );
    // Sign in (the first primary button in the trial notice).
    await p.locator('[data-testid="design-trial-mode"] button.btn-primary').click();
    await p.locator('input[type=email]').fill(`owner2-${l}@synthetic-example.com`);
    await p.locator('input[type=password]').fill('throwaway-pass-123');
    await p.locator('form button[type=submit]').click();
    await p.waitForSelector('[data-testid="project-' + '"]', { timeout: 1 }).catch(() => {});
    await p.waitForTimeout(2500);
    const rows = p.locator('[data-testid^="project-"]');
    // Open "Unknown schema".
    const unknownRow = p
      .locator('[data-testid^="project-"]', { hasText: 'Unknown schema' })
      .first();
    await unknownRow.locator('button').first().click();
    const notice = p.getByTestId('opened-schema-unknown');
    await notice.waitFor({ timeout: 10000 }).catch(() => {});
    const text = (await notice.count()) ? await notice.innerText() : '';
    ok(l, 'unknown schema said in the overview on open', text.includes('pp-design/9'), text);
    const inOverview = await p.evaluate(
      () =>
        !!document.querySelector(
          '[data-testid="view-overview"] [data-testid="opened-schema-unknown"]',
        ) && !document.querySelector('[data-testid="view-overview"]').hidden,
    );
    ok(l, 'notice is in the visible overview', inOverview);
    await p.screenshot({ path: `${OUT}/${l}-unknown-schema-overview.png`, fullPage: false });
    // Open "Renderer stale" (approval recorded no renderer).
    const staleRow = p.locator('[data-testid^="project-"]', { hasText: 'Renderer stale' }).first();
    await staleRow.locator('button').first().click();
    await p.waitForTimeout(2500);
    const body = await p.locator('main').innerText();
    const part = {
      en: 'how exports are worded and drawn',
      ar: 'صياغة ملفات التصدير ورسمها',
      he: 'הניסוח והשרטוט של קובצי הייצוא',
    }[l];
    ok(l, 'approval not current names the renderer', body.includes(part));
    await p.screenshot({ path: `${OUT}/${l}-renderer-stale.png`, fullPage: true });
    // Company settings: a refused save focuses the field it names.
    await p.locator('[role=tab]').nth(1).click();
    await p.waitForTimeout(500);
    ok(
      l,
      'schedule view visible for settings',
      await p.evaluate(() => !document.querySelector('[data-testid="view-schedule"]').hidden),
      await p.evaluate(() =>
        [...document.querySelectorAll('[data-testid^="view-"]')]
          .filter((e) => !e.hidden)
          .map((e) => e.dataset.testid)
          .join(','),
      ),
    );
    await p.evaluate(() => {
      let e = document.querySelector('[data-testid="company-settings"]');
      while (e) {
        if (e.tagName === 'DETAILS') e.open = true;
        e = e.parentElement;
      }
      const j = document.querySelector('[data-testid="company-settings"] details');
      if (j) j.open = true;
    });
    const ta = p.locator('[data-testid="company-settings"] textarea');
    console.log(
      'DIAG',
      await p.evaluate(() => {
        const t = document.querySelector('[data-testid="company-settings"] textarea');
        const out = [];
        let e = t;
        while (e) {
          const cs = getComputedStyle(e);
          if (
            e.hidden ||
            cs.display === 'none' ||
            cs.visibility === 'hidden' ||
            (e.tagName === 'DETAILS' && !e.open)
          )
            out.push(
              e.tagName +
                '#' +
                ((e.dataset && e.dataset.testid) || e.id || '') +
                ':' +
                (e.hidden ? 'hidden' : cs.display),
            );
          e = e.parentElement;
        }
        return out.join(' < ');
      }),
    );
    await ta.fill('{"key":"synthetic-co","name":"Synthetic Co","max_circuits_per_rcd":"many"}');
    await p.evaluate(() => {
      const j = document.querySelector('[data-testid="company-settings"] details');
      if (j) j.open = false;
    });
    await p
      .locator('[data-testid="company-settings"] button.btn-secondary:not([aria-label])')
      .last()
      .click();
    await p
      .locator('[data-testid="company-settings"] [role=alert]')
      .waitFor({ timeout: 10000 })
      .catch(() => {});
    const focused = await p.evaluate(() => {
      const a = document.activeElement;
      return {
        tag: a?.tagName,
        invalid: a?.getAttribute('aria-invalid'),
        label: a?.id
          ? document.querySelector(`label[for="${a.id}"]`)?.textContent
          : a?.getAttribute('aria-label'),
        alert: document.querySelector('[data-testid="company-settings"] [role=alert]')?.textContent,
      };
    });
    ok(
      l,
      'refused settings save focuses the field',
      focused.tag === 'INPUT' && focused.invalid === 'true',
      JSON.stringify(focused),
    );
    await p
      .locator('[data-testid="company-settings"]')
      .screenshot({ path: `${OUT}/${l}-settings-focus.png` });
    // Unknown setting: JSON opened and focused.
    await p.evaluate(() => {
      const j = document.querySelector('[data-testid="company-settings"] details');
      if (j) j.open = true;
    });
    await ta.fill('{"key":"synthetic-co","name":"Synthetic Co","colour":"blue"}');
    await p.evaluate(() => {
      const j = document.querySelector('[data-testid="company-settings"] details');
      if (j) j.open = false;
    });
    await p
      .locator('[data-testid="company-settings"] button.btn-secondary:not([aria-label])')
      .last()
      .click();
    await p.waitForTimeout(2000);
    const f2 = await p.evaluate(() => ({
      tag: document.activeElement?.tagName,
      open: document.querySelector('[data-testid="company-settings"] details')?.open,
    }));
    ok(
      l,
      'unknown setting opens and focuses the JSON',
      f2.tag === 'TEXTAREA' && f2.open === true,
      JSON.stringify(f2),
    );
    await ctx.close();
  }
  await b.close();
  console.log(log.join('\n'));
})();
