import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import type { designProject, BoardDesignResponse } from '@/lib/design';

import { renderApp } from './helpers';

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
        notes: ['Fed from MDB.'],
      },
    ],
  },
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

function fillRow(description: string, power: string) {
  fireEvent.change(screen.getByLabelText('Circuit'), { target: { value: description } });
  fireEvent.change(screen.getByLabelText(/^Power.*kW/), { target: { value: power } });
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
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Tower' } });
    fireEvent.change(screen.getByLabelText('Board name'), { target: { value: 'MDB' } });
    fillRow('Lights', '1');

    fireEvent.click(screen.getByTestId('board-add'));
    // The new board is the one being edited, fed from the main board.
    expect(screen.getByLabelText<HTMLInputElement>('Board name').value).toBe('DB2');
    expect(screen.getByLabelText<HTMLSelectElement>('Fed from').value).toBe('MDB');
    fillRow('Sockets', '2');

    const submit = screen.getByRole('button', { name: 'Design the board' });
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
    fireEvent.change(screen.getByLabelText('Board name'), { target: { value: 'MDB' } });
    fireEvent.click(screen.getByTestId('board-add'));
    fireEvent.click(screen.getByTestId('board-add'));
    // DB3 is fed from MDB too; feed it from DB2 instead.
    fireEvent.change(screen.getByLabelText('Fed from'), { target: { value: 'DB2' } });

    const tabs = screen.getAllByRole('button', { pressed: false });
    fireEvent.click(tabs.find((tab) => tab.textContent === 'DB2') as HTMLElement);
    fireEvent.change(screen.getByLabelText('Board name'), { target: { value: 'DB-EAST' } });
    fireEvent.click(screen.getByRole('button', { name: 'DB3' }));
    expect(screen.getByLabelText<HTMLSelectElement>('Fed from').value).toBe('DB-EAST');

    fireEvent.click(screen.getByRole('button', { name: 'DB-EAST' }));
    fireEvent.click(screen.getByTestId('board-remove'));
    fireEvent.click(screen.getByRole('button', { name: 'DB3' }));
    expect(screen.getByLabelText<HTMLSelectElement>('Fed from').value).toBe('MDB');
  });

  it('cannot design while a board has an empty row', () => {
    render();
    fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Tower' } });
    fillRow('Lights', '1');
    fireEvent.click(screen.getByTestId('board-add'));
    expect(screen.getByRole('button', { name: 'Design the board' }).hasAttribute('disabled')).toBe(
      true,
    );
  });
});

describe('a motor row', () => {
  it('offers a starter only for a motor, and a starter makes it three-phase', () => {
    render();
    const starter = screen.getByLabelText<HTMLSelectElement>('Starter');
    expect(starter.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText('Load type'), { target: { value: 'motor' } });
    expect(starter.disabled).toBe(false);
    fireEvent.change(starter, { target: { value: 'star_delta' } });
    expect(screen.getByLabelText<HTMLSelectElement>('Phases').value).toBe('3');
    // Back to sockets: the starter goes with the motor.
    fireEvent.change(screen.getByLabelText('Load type'), { target: { value: 'socket' } });
    expect(starter.value).toBe('');
  });
});
