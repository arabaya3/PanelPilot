import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PlcPanel } from '@/components/plc-panel';
import type { writePlcProgram, DesignProject, PlcProgram } from '@/lib/design';

import { renderApp } from './helpers';

/** The control program under a designed board: written and checked; its files are not exported yet. */

const PROJECT = { info: { name: 'Pocket' }, boards: [], parts: [] } as unknown as DesignProject;

const PROGRAM: PlcProgram = {
  name: 'Pocket_Control',
  source:
    'PROGRAM Pocket_Control\nK_DB_K1 := EStop_OK AND (Man_DB_K1 OR (Auto_Mode AND Schedule_On));\nEND_PROGRAM\n',
  io: [
    { tag: 'EStop_OK', direction: 'input', board: '', device: '', description: 'E-stop' },
    { tag: 'Auto_Mode', direction: 'input', board: '', device: '', description: 'Auto' },
    { tag: 'Schedule_On', direction: 'input', board: '', device: '', description: 'Clock' },
    { tag: 'Man_DB_K1', direction: 'input', board: 'DB', device: '=DB-K1', description: 'x' },
    { tag: 'K_DB_K1', direction: 'output', board: 'DB', device: '=DB-K1', description: 'x' },
  ],
  validation: { status: 'valid', findings: [], dialect: 'iec-61131-3', checked_by: 'parser' },
};

function panel(overrides: Partial<Parameters<typeof PlcPanel>[0]> = {}) {
  renderApp(<PlcPanel token="tok" project={PROJECT} profile={null} {...overrides} />);
}

describe('plc panel', () => {
  it('writes the program and shows the checker verdict and the I/O count', async () => {
    const writeImpl = vi
      .fn<typeof writePlcProgram>()
      .mockResolvedValue({ kind: 'written', program: PROGRAM });
    panel({ writeImpl });
    fireEvent.click(screen.getByRole('button', { name: 'Write the program' }));

    expect((await screen.findByTestId('plc-status')).textContent).toContain('Checked');
    expect(screen.getByTestId('plc-source').textContent).toContain('EStop_OK AND');
    expect(screen.getByText('4 inputs, 1 outputs')).toBeTruthy();
    expect(writeImpl.mock.calls[0]?.[0].project).toBe(PROJECT);
  });

  it('shows an invalid verdict as an alert with its findings', async () => {
    const writeImpl = vi.fn<typeof writePlcProgram>().mockResolvedValue({
      kind: 'written',
      program: {
        ...PROGRAM,
        validation: {
          ...PROGRAM.validation,
          status: 'invalid',
          findings: [
            { code: 'syntax-error', message: 'could not parse', severity: 'error', line: 2 },
          ],
        },
      },
    });
    panel({ writeImpl });
    fireEvent.click(screen.getByRole('button', { name: 'Write the program' }));

    expect((await screen.findByRole('alert')).textContent).toContain('found errors');
    expect(screen.getByText('2: could not parse')).toBeTruthy();
  });

  it('says why the program was refused', async () => {
    const writeImpl = vi
      .fn<typeof writePlcProgram>()
      .mockResolvedValue({ kind: 'refused', detail: 'no circuit is PLC-switched' });
    panel({ writeImpl });
    fireEvent.click(screen.getByRole('button', { name: 'Write the program' }));

    expect((await screen.findByTestId('plc-message')).textContent).toBe(
      'no circuit is PLC-switched',
    );
  });

  it('shows the program and the I/O list as not exportable yet, and why', () => {
    panel();
    for (const testId of ['plc-export-st', 'plc-export-io']) {
      const button = screen.getByTestId(testId);
      expect((button as HTMLButtonElement).disabled).toBe(true);
      expect(button.getAttribute('aria-describedby')).toBe(
        screen.getByTestId('plc-unavailable').id,
      );
    }
    expect(screen.getByTestId('plc-unavailable').textContent).toBe(
      'Not available yet: this format cannot keep a visible draft warning once imported. Use the PDF or DXF for review.',
    );
  });
});
