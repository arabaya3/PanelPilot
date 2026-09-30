import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CalcScreen } from '@/components/calc-screen';
import { buildBom, type PanelBomResponse } from '@/lib/calculations';

import { renderApp } from './helpers';

/** The BOM tab of `/calc`: `/calculations/panel-bom` answered 501 and had no page. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const RITTAL = {
  document_id: 'rittal',
  document_title: 'Enclosure and process cooling',
  manufacturer: 'Rittal',
  page: 40,
};

const BUILT: PanelBomResponse = {
  result: {
    lines: [
      {
        part_reference: 'ACS880-01-045A-3',
        description: 'M-101: drive for conveyor',
        quantity: 1,
        source: RITTAL,
      },
      {
        part_reference: 'Cooling 659 W',
        description: 'Active cooling',
        quantity: 1,
        source: RITTAL,
      },
    ],
    heat_load_w: '900',
    cooling_required_w: '659.1',
    notes: ['Protective devices, contactors and terminals are not included.'],
  },
  sources: [RITTAL],
};

async function openBomTab(buildImpl: typeof buildBom) {
  renderApp(<CalcScreen buildImpl={buildImpl} acquireImpl={vi.fn().mockResolvedValue(READY)} />);
  fireEvent.click(screen.getByTestId('calc-tab-bom'));
  fireEvent.change(screen.getByLabelText('Tag'), { target: { value: 'M-101' } });
  fireEvent.change(screen.getByLabelText('Description'), { target: { value: 'conveyor' } });
  fireEvent.change(screen.getByLabelText(/^Nameplate current/), { target: { value: '40' } });
  fireEvent.change(screen.getByLabelText(/^Heat given off/), { target: { value: '900' } });
  fireEvent.click(screen.getByLabelText('Variable speed (drive)'));
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Build the BOM' }).hasAttribute('disabled')).toBe(
      false,
    );
  });
}

describe('panel BOM', () => {
  it('sends the loads and enclosure, and shows the lines, cooling and notes', async () => {
    const buildImpl = vi.fn().mockResolvedValue({ kind: 'built', response: BUILT });
    await openBomTab(buildImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Build the BOM' }));

    await screen.findByTestId('bom-result');
    expect(buildImpl).toHaveBeenCalledWith({
      token: 'tok',
      request: {
        loads: [
          {
            tag: 'M-101',
            description: 'conveyor',
            power_kw: null,
            current_a: '40',
            dissipation_w: '900',
            variable_speed: true,
          },
        ],
        constraints: {
          width_mm: 800,
          height_mm: 2000,
          depth_mm: 600,
          ingress_rating: 'IP54',
          placement: 'single_wall',
          cable_installation_method: 'C',
          supply_voltage_v: '400',
          preferred_vendors: [],
          ambient_temp_c: '35',
          max_internal_temp_c: '50',
        },
      },
    });
    expect(screen.getByText('ACS880-01-045A-3')).toBeTruthy();
    expect(screen.getByText('659 W')).toBeTruthy();
    expect(screen.getByTestId('bom-result').textContent).toContain('not included');
  });

  it('adds and removes load rows, keeping at least one', async () => {
    await openBomTab(vi.fn());
    fireEvent.click(screen.getByRole('button', { name: 'Add a load' }));
    expect(screen.getByTestId('bom-load-1')).toBeTruthy();
    const removes = screen.getAllByRole('button', { name: 'Remove' });
    fireEvent.click(removes[1] as HTMLElement);
    expect(screen.queryByTestId('bom-load-1')).toBeNull();
    expect(screen.getByRole('button', { name: 'Remove' }).hasAttribute('disabled')).toBe(true);
  });

  it('shows why a schedule was refused', async () => {
    const buildImpl = vi
      .fn()
      .mockResolvedValue({ kind: 'refused', detail: 'M-101: no ACS880-01 400 V type supplies' });
    await openBomTab(buildImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Build the BOM' }));
    const alert = await screen.findByTestId('bom-error');
    expect(alert.textContent).toContain('M-101: no ACS880-01');
  });
});

describe('buildBom', () => {
  it('reads a 422 as a refusal', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ detail: 'empty' }), { status: 422 }));
    const outcome = await buildBom({
      token: 't',
      request: {
        loads: [],
        constraints: {
          width_mm: 1,
          height_mm: 1,
          depth_mm: 1,
          ingress_rating: 'IP54',
          placement: 'single_wall',
          cable_installation_method: 'C',
          supply_voltage_v: '400',
          preferred_vendors: [],
          ambient_temp_c: '35',
          max_internal_temp_c: '50',
        },
      },
      fetchImpl,
    });
    expect(outcome).toEqual({ kind: 'refused', detail: 'empty' });
  });
});
