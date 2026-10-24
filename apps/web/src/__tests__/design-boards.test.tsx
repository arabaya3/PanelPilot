import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import type { designProject, BoardDesignResponse } from '@/lib/design';

import { renderApp } from './helpers';

/*
 * Queries are scoped to the part of the view they read: the whole workspace is rendered,
 * and a page-wide label or role query costs tens of milliseconds a call in
 * jsdom.
 */
const overviewView = () => within(screen.getByTestId('view-overview'));
const boardTabs = () => within(screen.getByTestId('board-tabs'));
const boardDetails = () => within(screen.getByTestId('board-details'));
const firstRow = () => within(screen.getByTestId('design-load-0'));

/** `/design` with more than one board: tabs, a feeding board, one request. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const DESIGNED = {
  project: {
    info: { name: 'Tower' },
    profile: 'iec-default',
    parts: [],
    boards: [
      { id: 'MDB', name: 'MDB', devices: [], cables: [], circuits: [], notes: [] },
      {
        id: 'DB2',
        name: 'DB2',
        fed_from: 'MDB',
        devices: [],
        cables: [],
        circuits: [],
        notes: [{ code: 'from_a_newer_api', params: {}, text: 'Fed from MDB.' }],
      },
    ],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

function fillRow(description: string, power: string) {
  const row = within(screen.getByTestId('design-load-0'));
  fireEvent.change(row.getByLabelText('Circuit'), { target: { value: description } });
  fireEvent.change(row.getByLabelText(/^Power.*kW/), { target: { value: power } });
}

function render(designImpl = vi.fn<typeof designProject>()) {
  renderApp(
    <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
  );
  return designImpl;
}

describe('a project of boards', () => {
  it('sends every board, a sub-board naming the board that feeds it', async () => {
    const designImpl = render(
      vi.fn<typeof designProject>().mockResolvedValue({ kind: 'designed', response: DESIGNED }),
    );
    fireEvent.change(overviewView().getByLabelText('Project name'), { target: { value: 'Tower' } });
    fireEvent.change(boardDetails().getByLabelText('Board name'), { target: { value: 'MDB' } });
    fillRow('Lights', '1');

    fireEvent.click(screen.getByTestId('board-add'));
    // The new board is the one being edited, fed from the main board.
    expect(boardDetails().getByLabelText<HTMLInputElement>('Board name').value).toBe('DB2');
    expect(boardDetails().getByLabelText<HTMLSelectElement>('Fed from').value).toBe('MDB');
    fillRow('Sockets', '2');

    const submit = screen.getByText('Design the board', { selector: 'button' });
    await waitFor(() => {
      expect(submit.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.click(submit);
    await screen.findByTestId('design-result');

    const request = designImpl.mock.calls[0]?.[0].request;
    expect(request?.boards.map((board) => [board.name, board.fed_from])).toEqual([
      ['MDB', null],
      ['DB2', 'MDB'],
    ]);
    expect(request?.boards[1]?.loads[0]?.description).toBe('Sockets');
    expect(screen.getByTestId('design-board-DB2').textContent).toContain('fed from MDB');
  });

  it('keeps a sub-board fed when its supply is renamed, and lifts it when removed', () => {
    render();
    // The board tabs live in the load schedule view.
    fireEvent.click(
      screen.getByText('Load schedule', { selector: '[role="tab"], [role="tab"] *' }),
    );
    fireEvent.change(boardDetails().getByLabelText('Board name'), { target: { value: 'MDB' } });
    fireEvent.click(screen.getByTestId('board-add'));
    fireEvent.click(screen.getByTestId('board-add'));
    // DB3 is fed from MDB too; feed it from DB2 instead.
    fireEvent.change(boardDetails().getByLabelText('Fed from'), { target: { value: 'DB2' } });

    const tabs = boardTabs().getAllByRole('button', { pressed: false });
    fireEvent.click(tabs.find((tab) => tab.textContent === 'DB2') as HTMLElement);
    fireEvent.change(boardDetails().getByLabelText('Board name'), { target: { value: 'DB-EAST' } });
    fireEvent.click(boardTabs().getByRole('button', { name: 'DB3' }));
    expect(boardDetails().getByLabelText<HTMLSelectElement>('Fed from').value).toBe('DB-EAST');

    fireEvent.click(boardTabs().getByRole('button', { name: 'DB-EAST' }));
    fireEvent.click(screen.getByTestId('board-remove'));
    fireEvent.click(boardTabs().getByRole('button', { name: 'DB3' }));
    expect(boardDetails().getByLabelText<HTMLSelectElement>('Fed from').value).toBe('MDB');
  });

  it('asks a fed board for its feeder length, and each row for its cable length', () => {
    render();
    expect(boardDetails().queryByLabelText(/^Feeder length/)).toBeNull();
    fireEvent.change(firstRow().getByLabelText(/^Cable length/), { target: { value: '40' } });
    fireEvent.change(boardDetails().getByLabelText('Board name'), { target: { value: 'MDB' } });
    fireEvent.click(screen.getByTestId('board-add'));
    fireEvent.change(boardDetails().getByLabelText(/^Feeder length/), { target: { value: '80' } });
    expect(boardDetails().getByLabelText<HTMLInputElement>(/^Feeder length/).value).toBe('80');
    fireEvent.change(boardDetails().getByLabelText('Fed from'), { target: { value: '' } });
    expect(boardDetails().queryByLabelText(/^Feeder length/)).toBeNull();
  });

  it('cannot design while a board has an empty row', () => {
    render();
    fireEvent.change(overviewView().getByLabelText('Project name'), { target: { value: 'Tower' } });
    fillRow('Lights', '1');
    fireEvent.click(screen.getByTestId('board-add'));
    expect(
      screen.getByText('Design the board', { selector: 'button' }).hasAttribute('disabled'),
    ).toBe(true);
  });
});

describe('a motor row', () => {
  it('offers a starter only for a motor, and a starter makes it three-phase', () => {
    render();
    const starter = firstRow().getByLabelText<HTMLSelectElement>('Starter');
    expect(starter.disabled).toBe(true);
    fireEvent.change(firstRow().getByLabelText('Load type'), { target: { value: 'motor' } });
    expect(starter.disabled).toBe(false);
    fireEvent.change(starter, { target: { value: 'star_delta' } });
    expect(firstRow().getByLabelText<HTMLSelectElement>('Phases').value).toBe('3');
    // Back to sockets: the starter goes with the motor.
    fireEvent.change(firstRow().getByLabelText('Load type'), { target: { value: 'socket' } });
    expect(starter.value).toBe('');
  });
});
