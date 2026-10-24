import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { isolateLtr, splitLtrRuns } from '@/components/design/bidi';
import { useNoteText, useOutcomeText } from '@/components/design/note-text';
import type { DesignNote, Refusal } from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/** A value isolated from the right-to-left sentence around it (FSI … PDI). */
const iso = (value: string) => `⁨${value}⁩`;

/** Design notes arrive as codes and values, and read in the page's language. */

function Notes({ notes }: { notes: DesignNote[] }) {
  const noteText = useNoteText();
  return (
    <ul>
      {notes.map((note, index) => (
        <li key={index} data-testid={`note-${String(index)}`}>
          {noteText(note)}
        </li>
      ))}
    </ul>
  );
}

const INFERRED: DesignNote = {
  code: 'import_kind_inferred',
  params: { row: '3', load: 'AC 1', kind: 'air_conditioning' },
  text: 'Row 3 (AC 1): taken as air_conditioning from its description.',
};

describe('design notes', () => {
  it('renders a note in Arabic, its load kind translated too', () => {
    renderApp(<Notes notes={[INFERRED]} />, { locale: 'ar' });
    expect(screen.getByTestId('note-0').textContent).toBe(
      `الصف ${iso('3')} (${iso('AC 1')}): اعتُبر «${iso(ar.design.kind.air_conditioning)}» من وصفه.`,
    );
  });

  it('chooses given or typical for a suggested circuit', () => {
    const split = (source: string): DesignNote => ({
      code: 'split_points',
      params: { load: 'Sockets 1', points: '7', watts: '150', power: '1.05', source },
      text: '',
    });
    renderApp(<Notes notes={[split('given'), split('typical')]} />, { locale: 'ar' });
    expect(screen.getByTestId('note-0').textContent).toContain('من الوصف');
    expect(screen.getByTestId('note-1').textContent).toContain('قيمة نموذجية');
  });

  it('agrees the spare ways with their number', () => {
    const spare = (count: string): DesignNote => ({
      code: 'spare_ways',
      params: { count, percent: '20' },
      text: '',
    });
    renderApp(<Notes notes={[spare('1'), spare('2'), spare('3')]} />, { locale: 'ar' });
    expect(screen.getByTestId('note-0').textContent).toBe(
      `اترك مخرجاً احتياطياً واحداً (${iso('20')}%).`,
    );
    expect(screen.getByTestId('note-1').textContent).toBe(`اترك مخرجين احتياطيين (${iso('20')}%).`);
    expect(screen.getByTestId('note-2').textContent).toBe(`اترك 3 مخارج احتياطية (${iso('20')}%).`);
  });

  it.each(['ar', 'he'] as const)(
    'names the market and its code in %s, not in English',
    (locale) => {
      const market: DesignNote = {
        code: 'market_rules',
        params: {
          market: 'Palestine',
          regulation: 'IEC 60364 (adopted by the Palestine Standards Institution)',
          market_code: 'PS',
          regulation_code: 'PS',
        },
        text: 'Designed for Palestine, under IEC 60364 (adopted by the Palestine Standards Institution).',
      };
      const bundle = locale === 'ar' ? ar : he;
      renderApp(<Notes notes={[market]} />, { locale });
      const said = screen.getByTestId('note-0').textContent;
      expect(said).toContain(iso(bundle.design.markets.PS));
      expect(said).toContain(iso(bundle.design.regulation.PS));
      expect(said).not.toContain('Palestine');
      expect(said).not.toContain('adopted');
    },
  );

  it('keeps the English market names when a note carries no codes', () => {
    const market: DesignNote = {
      code: 'market_rules',
      params: { market: 'Israel', regulation: 'Israeli Electricity Law and Regulations' },
      text: '',
    };
    renderApp(<Notes notes={[market]} />, { locale: 'he' });
    expect(screen.getByTestId('note-0').textContent).toContain(iso('Israel'));
  });

  it('falls back to the English text for a code it has no sentence for', () => {
    renderApp(
      <Notes notes={[{ code: 'from_a_newer_api', params: {}, text: 'Something new.' }]} />,
      { locale: 'ar' },
    );
    expect(screen.getByTestId('note-0').textContent).toBe('Something new.');
  });

  it('has every code in every language', () => {
    const codes = Object.keys(en.design.note).sort();
    expect(Object.keys(ar.design.note).sort()).toEqual(codes);
    expect(Object.keys(he.design.note).sort()).toEqual(codes);
  });
});

function Said({ outcome }: { outcome: Refusal | { kind: 'failed' } }) {
  const say = useOutcomeText();
  return <p data-testid="said">{say(outcome)}</p>;
}

describe('design refusals', () => {
  it('says a coded refusal in Arabic, naming what it is about', () => {
    renderApp(
      <Said
        outcome={{
          kind: 'refused',
          detail: 'Pump: a motor starter needs a three-phase motor',
          code: 'starter_needs_three_phase',
          params: { subject: 'Pump' },
        }}
      />,
      { locale: 'ar' },
    );
    expect(screen.getByTestId('said').textContent).toBe(
      `${iso('Pump')}: ${ar.design.errors.starter_needs_three_phase}`,
    );
  });

  it('falls back to the server’s reason, then to the generic error', () => {
    renderApp(<Said outcome={{ kind: 'refused', detail: 'Something odd.' }} />, { locale: 'ar' });
    expect(screen.getByTestId('said').textContent).toBe('Something odd.');
  });

  it('says a failure as the generic error', () => {
    renderApp(<Said outcome={{ kind: 'failed' }} />, { locale: 'ar' });
    expect(screen.getByTestId('said').textContent).toBe(ar.design.error);
  });

  it('has every refusal in every language', () => {
    const codes = Object.keys(en.design.errors).sort();
    expect(Object.keys(ar.design.errors).sort()).toEqual(codes);
    expect(Object.keys(he.design.errors).sort()).toEqual(codes);
  });
});

describe('splitLtrRuns', () => {
  it('splits out each left-to-right run, its characters unchanged', () => {
    const citation = 'ABB catalogue (1SKC802027C0201), pp. 5, 7';
    expect(splitLtrRuns(`قبل (${isolateLtr(citation)}). بعد`)).toEqual([
      { text: 'قبل (', ltr: false },
      { text: citation, ltr: true },
      { text: '). بعد', ltr: false },
    ]);
  });

  it('leaves text with no run, or an unclosed one, as it is', () => {
    expect(splitLtrRuns('plain')).toEqual([{ text: 'plain', ltr: false }]);
    expect(splitLtrRuns('⁦open')).toEqual([{ text: '⁦open', ltr: false }]);
    expect(splitLtrRuns('')).toEqual([]);
  });
});
