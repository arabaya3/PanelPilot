import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { EnglishExportsNote } from '@/components/design/english-exports-note';
import { QuotationPanel } from '@/components/quotation-panel';
import type { DesignProject } from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/** Exported PDFs are English whatever the interface language; ar and he users are told so. */

const PROJECT = { info: { name: 'Pocket' }, boards: [], parts: [] } as unknown as DesignProject;

describe('English-only exports note', () => {
  it.each([
    ['ar', ar.design.exports.englishOnly],
    ['he', he.design.exports.englishOnly],
  ] as const)('is shown, localized, in %s', (locale, text) => {
    renderApp(<EnglishExportsNote />, { locale });
    expect(screen.getByTestId('english-exports-note').textContent).toBe(text);
    expect(text).not.toBe(en.design.exports.englishOnly);
  });

  it.each([
    ['en', en.design.exports.englishOnly],
    ['ar', ar.design.exports.englishOnly],
    ['he', he.design.exports.englishOnly],
  ] as const)('says which exports are English and names every format, in %s', (_locale, text) => {
    // The drawing set and calculations follow the drawing language, so the
    // note must not call every export English; it names DXF and JSON too.
    expect(text).toContain('DXF');
    expect(text).toContain('JSON');
  });

  it('does not claim the drawing set and calculations are English', () => {
    expect(en.design.exports.englishOnly).toContain("company profile's drawing language");
    expect(en.design.exports.englishOnly).not.toMatch(/^Exported documents/);
  });

  it('is omitted in en', () => {
    renderApp(<EnglishExportsNote />, { locale: 'en' });
    expect(screen.queryByTestId('english-exports-note')).toBeNull();
  });

  it('sits next to the quotation export in ar and not in en', () => {
    const { unmount } = renderApp(
      <QuotationPanel token="tok" project={PROJECT} profile={null} saveImpl={vi.fn()} />,
      { locale: 'ar' },
    );
    expect(screen.getByTestId('quote-english').textContent).toBe(ar.design.exports.englishOnly);
    unmount();
    renderApp(<QuotationPanel token="tok" project={PROJECT} profile={null} saveImpl={vi.fn()} />, {
      locale: 'en',
    });
    expect(screen.queryByTestId('quote-english')).toBeNull();
  });
});
