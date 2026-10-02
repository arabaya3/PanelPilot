import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MarkupsPanel } from '@/components/design/markups-panel';
import { applySuggestion, blankBoard, blankLoad } from '@/components/design/schedule';
import { readMarkups, type DesignProject } from '@/lib/design';

import { renderApp } from './helpers';

/** A reviewed drawing set's comments, listed on the drawings they sit on. */

const PROJECT = { info: { name: 'Tower' }, boards: [], parts: [] } as unknown as DesignProject;

describe('the markups panel', () => {
  it('lists each comment with its page, board and label', async () => {
    const readImpl = vi.fn<typeof readMarkups>().mockResolvedValue({
      kind: 'read',
      report: {
        matched: true,
        markups: [
          {
            page: 3,
            kind: 'Text',
            author: 'Consultant',
            text: 'Make this C20',
            sheet: 'Main power',
            board: 'MDB',
            near: '-Q3',
          },
        ],
      },
    });
    renderApp(<MarkupsPanel token="tok" project={PROJECT} profile={null} readImpl={readImpl} />);
    const file = new File(['%PDF'], 'set.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Reviewed drawing set (PDF)'), {
      target: { files: [file] },
    });
    const result = await screen.findByTestId('markups-result');
    expect(result.textContent).toContain('1 comment, each placed on its drawing.');
    expect(result.textContent).toContain('Page 3 · MDB · near -Q3 · Consultant');
    expect(result.textContent).toContain('Make this C20');
    expect(readImpl.mock.calls[0]?.[0]).toMatchObject({ filename: 'set.pdf', project: PROJECT });
  });

  it('says a refusal in the reader’s words', async () => {
    const readImpl = vi.fn<typeof readMarkups>().mockResolvedValue({
      kind: 'refused',
      detail: 'x',
      code: 'markups_unreadable',
    });
    renderApp(<MarkupsPanel token="tok" project={PROJECT} profile={null} readImpl={readImpl} />);
    fireEvent.change(screen.getByLabelText('Reviewed drawing set (PDF)'), {
      target: { files: [new File(['x'], 'x.pdf')] },
    });
    expect((await screen.findByTestId('markups-message')).textContent).toBe(
      'The file could not be read as a PDF.',
    );
  });
});

describe('a suggested change', () => {
  function boards() {
    const main = blankBoard(0, 1, 'MDB');
    main.loads = [
      { ...blankLoad(1), description: 'Lights', power: '1' },
      { ...blankLoad(2), description: 'Pump', power: '4' },
    ];
    return [main, blankBoard(3, 4, 'DB1', 'MDB')];
  }

  it('is offered and applied with one click', async () => {
    const readImpl = vi.fn<typeof readMarkups>().mockResolvedValue({
      kind: 'read',
      report: {
        matched: true,
        markups: [
          {
            page: 4,
            kind: 'Text',
            author: '',
            text: 'pump is 5.5 kW',
            sheet: 'Distribution loads',
            board: 'MDB',
            near: '-Q4',
            suggestion: {
              board: 'MDB',
              circuit: 'Pump',
              load_index: 1,
              field: 'power_kw',
              value: '5.5',
            },
          },
        ],
      },
    });
    const onApply = vi.fn().mockReturnValue(true);
    renderApp(
      <MarkupsPanel
        token="tok"
        project={PROJECT}
        profile={null}
        onApply={onApply}
        readImpl={readImpl}
      />,
    );
    fireEvent.change(screen.getByLabelText('Reviewed drawing set (PDF)'), {
      target: { files: [new File(['%PDF'], 'set.pdf')] },
    });
    const offered = await screen.findByTestId('markup-suggestion-0');
    expect(offered.textContent).toContain('Suggested: Pump at 5.5 kW.');
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ field: 'power_kw' }));
    expect(offered.textContent).toContain('Applied to the schedule');
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

  it('changes the row it names, found by description when rows moved', () => {
    const changed = applySuggestion(boards(), {
      board: 'MDB',
      circuit: 'Pump',
      load_index: 0,
      field: 'length_m',
      value: '45',
    });
    expect(changed?.[0]?.loads[1]).toMatchObject({ description: 'Pump', length: '45' });
    const removed = applySuggestion(boards(), {
      board: 'MDB',
      circuit: 'Lights',
      load_index: 0,
      field: 'remove',
    });
    expect(removed?.[0]?.loads.map((load) => load.description)).toEqual(['Pump']);
    const feeder = applySuggestion(boards(), {
      board: 'DB1',
      circuit: 'Feeder to DB1',
      load_index: null,
      field: 'feeder_length_m',
      value: '80',
    });
    expect(feeder?.[1]?.feederLength).toBe('80');
    const starter = applySuggestion(boards(), {
      board: 'MDB',
      circuit: 'Pump',
      load_index: 1,
      field: 'starter',
      value: 'star_delta',
    });
    expect(starter?.[0]?.loads[1]?.starter).toBe('star_delta');
  });

  it('is refused when its circuit or board is gone', () => {
    expect(
      applySuggestion(boards(), {
        board: 'MDB',
        circuit: 'Chiller',
        load_index: 5,
        field: 'power_kw',
        value: '9',
      }),
    ).toBeNull();
    expect(applySuggestion(boards(), { board: 'XX', circuit: 'Pump', field: 'remove' })).toBeNull();
  });
});

describe('readMarkups', () => {
  it('sends the PDF with the project and settings', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ markups: [], matched: false })));
    const outcome = await readMarkups({
      token: 't',
      fetchImpl,
      file: new Blob(['%PDF']),
      filename: 'set.pdf',
      project: PROJECT,
      profile: { key: 'acme' },
    });
    expect(outcome.kind).toBe('read');
    const body = fetchImpl.mock.calls[0]?.[1]?.body as FormData;
    expect(body.get('project')).toBe(JSON.stringify(PROJECT));
    expect(body.get('profile')).toBe('{"key":"acme"}');
  });
});
