import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { DrawingSheets } from '@/components/design/design-result';
import { numberProblem } from '@/components/design/load-rows';
import { fitZoom, stepZoom, urlForView, viewFromSearch } from '@/components/design/workspace';
import type { BoardDesignResponse } from '@/lib/design';

import { renderApp } from './helpers';

/** `/design` as a workspace: views in the address, unsaved work guarded, sheets zoomed. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const TWO_BOARDS = {
  project: {
    info: { name: 'Tower' },
    profile: 'iec-default',
    parts: [],
    boards: [
      {
        id: 'MDB',
        name: 'MDB',
        supply: { voltage_v: '400', phases: 3, frequency_hz: '50', earthing: 'TN-S' },
        devices: [],
        cables: [],
        circuits: [],
        notes: [],
      },
      { id: 'DB2', name: 'DB2', fed_from: 'MDB', devices: [], cables: [], circuits: [], notes: [] },
    ],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

function renderScreen() {
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
    />,
  );
}

afterEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('workspace views', () => {
  it('opens on the view the address names, and writes the chosen view back', async () => {
    window.history.replaceState(null, '', '/design?view=materials&x=1');
    renderScreen();
    const materials = screen.getByRole('tab', { name: 'Materials' });
    await waitFor(() => {
      expect(materials.getAttribute('aria-selected')).toBe('true');
    });
    expect(screen.getByTestId('view-materials').hidden).toBe(false);
    expect(screen.getByTestId('view-overview').hidden).toBe(true);

    fireEvent.click(screen.getByRole('tab', { name: 'Drawings' }));
    expect(window.location.search).toBe('?view=drawings&x=1');
    expect(screen.getByTestId('view-drawings').hidden).toBe(false);
    expect(screen.getByTestId('view-materials').hidden).toBe(true);

    // The overview is the default, so it leaves the address plain.
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    expect(window.location.search).toBe('?x=1');
  });

  it('keeps what was typed when the view changes', () => {
    renderScreen();
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Tower' } });
    fireEvent.click(screen.getByRole('tab', { name: /Load schedule/ }));
    fireEvent.click(screen.getByRole('tab', { name: 'Overview' }));
    expect(screen.getByLabelText<HTMLInputElement>('Project name').value).toBe('Tower');
  });

  it('reads the view from a query string, falling back to the overview', () => {
    expect(viewFromSearch('?view=schedule')).toBe('schedule');
    expect(viewFromSearch('?view=nonsense')).toBe('overview');
    expect(viewFromSearch('')).toBe('overview');
    expect(urlForView('https://x.test/design?view=review#top', 'overview')).toBe('/design#top');
  });
});

describe('unsaved changes', () => {
  function leave(): boolean {
    const event = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  }

  it('asks before leaving only once something has been entered', async () => {
    renderScreen();
    await screen.findByTestId('design-incomplete');
    expect(leave()).toBe(false);
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Tower' } });
    expect(await screen.findByText('Unsaved changes')).toBeTruthy();
    expect(leave()).toBe(true);
    // Undoing the edit is not unsaved work.
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: '' } });
    await waitFor(() => {
      expect(screen.queryByText('Unsaved changes')).toBeNull();
    });
    expect(leave()).toBe(false);
  });

  it('does not count a detected market as an edit', async () => {
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        marketsImpl={vi.fn().mockResolvedValue([
          {
            code: 'SA',
            name: 'Saudi Arabia',
            voltage_v: 400,
            frequency_hz: 60,
            earthing: 'TN-S',
            regulation: 'SBC 401',
            currency: 'SAR',
          },
        ])}
        readMarketImpl={() => 'SA'}
      />,
    );
    await screen.findByTestId('market-basis');
    expect(screen.queryByText('Unsaved changes')).toBeNull();
    expect(leave()).toBe(false);
  });
});

describe('the drawing sheets', () => {
  it('zooms in and out in steps, and fits back', () => {
    renderApp(<DrawingSheets response={TWO_BOARDS} />);
    const level = screen.getByTestId('zoom-level');
    const content = screen.getByTestId('sheet-content');
    expect(level.textContent).toBe('100 %');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
    expect(level.textContent).toBe('125 %');
    expect(content.style.transform).toBe('scale(1.25)');
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    expect(level.textContent).toBe('75 %');
    // Unmeasurable here (no layout in jsdom), so fit leaves the natural size.
    fireEvent.click(screen.getByRole('button', { name: 'Fit to width' }));
    expect(level.textContent).toBe('100 %');
    expect(screen.getByTestId('sheet-frame').getAttribute('dir')).toBe('ltr');
  });

  it('shows one board per sheet, with that board in the inspector', () => {
    renderApp(<DrawingSheets response={TWO_BOARDS} />);
    const inspector = screen.getByTestId('board-inspector');
    expect(inspector.textContent).toContain('MDB');
    expect(inspector.textContent).toContain('400 V');
    expect(screen.getByTestId('design-board-DB2').parentElement?.hidden).toBe(true);
    fireEvent.change(screen.getByLabelText('Sheet'), { target: { value: '1' } });
    expect(screen.getByTestId('design-board-DB2').parentElement?.hidden).toBe(false);
    expect(screen.getByTestId('board-inspector').textContent).toContain('Fed from');
  });

  it('steps within the zoom range and never fits larger than natural size', () => {
    expect(stepZoom(2, 1)).toBe(2);
    expect(stepZoom(0.5, -1)).toBe(0.5);
    expect(stepZoom(0.9, 1)).toBe(1);
    expect(fitZoom(500, 1000)).toBe(0.5);
    expect(fitZoom(100, 1000)).toBe(0.5);
    expect(fitZoom(2000, 1000)).toBe(1);
    expect(fitZoom(0, 1000)).toBe(1);
  });
});

describe('load schedule validation', () => {
  it('flags a number the API would refuse, beside its input', () => {
    expect(numberProblem('', 'positive')).toBeNull();
    expect(numberProblem('1.5', 'positive')).toBeNull();
    expect(numberProblem('-2', 'positive')).toBe('positive');
    expect(numberProblem('abc', 'positive')).toBe('positive');
    expect(numberProblem('1.2', 'powerFactor')).toBe('powerFactor');
    expect(numberProblem('0.85', 'powerFactor')).toBeNull();
  });

  it('describes an invalid power by its message', () => {
    renderScreen();
    const power = screen.getByLabelText<HTMLInputElement>(/^Power.*kW/);
    fireEvent.change(power, { target: { value: '0' } });
    expect(power.getAttribute('aria-invalid')).toBe('true');
    const message = document.getElementById(power.getAttribute('aria-describedby') ?? '');
    expect(message?.textContent).toBe('Enter a number above 0.');
  });
});

describe('design notes under a right-to-left sheet (D8)', () => {
  const WITH_NOTES = {
    ...TWO_BOARDS,
    project: {
      ...TWO_BOARDS.project,
      boards: (TWO_BOARDS.project.boards ?? []).map((board, index) =>
        index === 0
          ? {
              ...board,
              notes: [
                { code: 'spare_ways', params: { count: '2', percent: '20' }, text: '' },
                { code: 'from_a_newer_api', params: {}, text: 'Something new (32 A).' },
              ],
            }
          : board,
      ),
    },
  } as typeof TWO_BOARDS;

  it('lays out each translated note right to left while the sheet stays left to right', () => {
    renderApp(<DrawingSheets response={WITH_NOTES} />, { locale: 'he' });
    expect(screen.getByTestId('sheet-frame').getAttribute('dir')).toBe('ltr');
    const notes = within(screen.getByTestId('design-notes')).getAllByRole('listitem');
    expect(notes[0]?.getAttribute('dir')).toBe('rtl');
    // The server's English, for a code the page cannot translate, reads as it is written.
    expect(notes[1]?.getAttribute('dir')).toBe('auto');
    expect(screen.getByTestId('design-notes-block').getAttribute('dir')).toBe('rtl');
  });

  it('leaves the notes to the sheet in English', () => {
    renderApp(<DrawingSheets response={WITH_NOTES} />, { locale: 'en' });
    const notes = within(screen.getByTestId('design-notes')).getAllByRole('listitem');
    expect(notes[0]?.getAttribute('dir')).toBe('auto');
    expect(screen.getByTestId('design-notes-block').getAttribute('dir')).toBeNull();
  });
});

describe('the enclosure note’s citation under a right-to-left sheet (D10)', () => {
  // As the API sends it (`app/design/enclosure.py`, SOURCE).
  const SOURCE =
    'ABB Distribution Boards catalogue (1SKC802027C0201), Mini Center compact, pp. 5, 7';
  const WITH_ENCLOSURE = {
    ...TWO_BOARDS,
    project: {
      ...TWO_BOARDS.project,
      boards: (TWO_BOARDS.project.boards ?? []).map((board, index) =>
        index === 0
          ? {
              ...board,
              notes: [
                {
                  code: 'enclosure_selected',
                  params: {
                    type: 'GDMS332 RX',
                    order: '2CVA250001P0001',
                    rows: '2',
                    size: '498 x 400 x 117',
                    free: '19',
                    source: SOURCE,
                  },
                  text: '',
                },
              ],
            }
          : board,
      ),
    },
  } as typeof TWO_BOARDS;

  const codePoints = (text: string) => Array.from(text, (char) => char.codePointAt(0));

  it.each(['ar', 'he'] as const)(
    'keeps the product, its order code and the citation each whole and left to right, characters unchanged (%s)',
    (locale) => {
      renderApp(<DrawingSheets response={WITH_ENCLOSURE} />, { locale });
      const [note] = within(screen.getByTestId('design-notes')).getAllByRole('listitem');
      if (!note) throw new Error('no note');
      expect(note.getAttribute('dir')).toBe('rtl');
      const runs = within(note).getAllByTestId('note-ltr-run');
      for (const run of runs) {
        expect(run.tagName).toBe('BDI');
        expect(run.getAttribute('dir')).toBe('ltr');
      }
      expect(runs.map((run) => codePoints(run.textContent))).toEqual(
        ['ABB Mini Center compact GDMS332 RX', '2CVA250001P0001', SOURCE].map(codePoints),
      );
      // The range and its type are one run, not two placed apart.
      expect(note.textContent).not.toContain('compact ‏');
      // Still isolated from the sentence in the text itself: FSI, then the run, then PDI.
      expect(note.textContent).toContain(`⁨${SOURCE}⁩`);
    },
  );

  it('leaves the citation inline, without marks, in English', () => {
    renderApp(<DrawingSheets response={WITH_ENCLOSURE} />, { locale: 'en' });
    const note = within(screen.getByTestId('design-notes')).getByRole('listitem');
    expect(within(note).queryByTestId('note-ltr-run')).toBeNull();
    expect(note.textContent).toContain(`(${SOURCE})`);
    expect(note.textContent).toContain(
      'Enclosure: ABB Mini Center compact GDMS332 RX (2CVA250001P0001),',
    );
    expect(note.textContent).not.toMatch(/[⁦-⁩]/);
  });
});
