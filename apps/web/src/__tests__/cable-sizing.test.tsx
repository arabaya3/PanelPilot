import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CableSizingScreen } from '@/components/cable-sizing-screen';
import { sizeCable, type CableSizingResponse } from '@/lib/calculations';

import { renderApp } from './helpers';

/** `/calc`: the cable-sizing endpoint had no page. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const ABB = {
  document_id: 'abb-1SDC010001D0204',
  document_title: 'Electrical installation handbook, Vol. 2',
  manufacturer: 'ABB',
};

const SIZED: CableSizingResponse = {
  result: {
    cross_section_mm2: '95',
    derated_ampacity_a: '111.8124',
    applied_factors: [
      { name: 'k1 ambient 40 °C', value: '0.87', source: { ...ABB, page: 34, section: 'Table 4' } },
      {
        name: 'k2 grouping 7 circuits',
        value: '0.54',
        source: { ...ABB, page: 37, section: 'Table 5' },
      },
    ],
  },
  voltage_drop_v: '2.1',
  voltage_drop_percent: '0.525',
  sources: [{ ...ABB, page: 43, section: 'Table 8' }],
};

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label, { exact: false }), { target: { value } });
}

async function renderReady(sizeImpl: typeof sizeCable) {
  renderApp(
    <CableSizingScreen sizeImpl={sizeImpl} acquireImpl={vi.fn().mockResolvedValue(READY)} />,
  );
  fill('Design current', '100');
  fill('Run length', '50');
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Size the cable' }).hasAttribute('disabled')).toBe(
      false,
    );
  });
}

describe('cable sizing', () => {
  it('sends the inputs and shows the size, drop and every source', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({ kind: 'sized', response: SIZED });
    await renderReady(sizeImpl);
    fill('Installation method', 'E');
    fill('Ambient temperature', '40');
    fill('Circuits grouped together', '7');
    fill('Insulation', '70');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    await screen.findByTestId('calc-result');
    expect(sizeImpl).toHaveBeenCalledWith({
      token: 'tok',
      request: {
        design_current_a: '100',
        length_m: '50',
        supply_voltage_v: '400',
        installation_method: 'E',
        ambient_temp_c: '40',
        grouped_circuits: 7,
        conductor_material: 'copper',
        insulation_rating_c: 70,
        power_factor: '0.8',
        three_phase: true,
      },
    });
    expect(screen.getByText('95 mm²')).toBeTruthy();
    expect(screen.getByText('111.81 A')).toBeTruthy();
    expect(screen.getByText('2.1 V (0.53%)')).toBeTruthy();
    expect(screen.getByTestId('calc-sources').textContent).toContain('Table 8, p. 43');
  });

  it('shows why an input was refused rather than a size', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({
      kind: 'refused',
      detail: 'installation method D1 is not supported',
    });
    await renderReady(sizeImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    const alert = await screen.findByTestId('calc-error');
    expect(alert.textContent).toContain('do not cover this input');
    expect(alert.textContent).toContain('D1 is not supported');
    expect(screen.queryByTestId('calc-result')).toBeNull();
  });

  it('will not calculate without a current and a length', async () => {
    renderApp(
      <CableSizingScreen sizeImpl={vi.fn()} acquireImpl={vi.fn().mockResolvedValue(READY)} />,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Size the cable' }).hasAttribute('disabled')).toBe(
        true,
      );
    });
  });
});

describe('sizeCable', () => {
  function respond(status: number, body: unknown) {
    return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  }
  const request = {
    design_current_a: '32',
    length_m: '40',
    supply_voltage_v: '400',
    installation_method: 'B1' as const,
    ambient_temp_c: '30',
    grouped_circuits: 1,
    conductor_material: 'copper' as const,
    insulation_rating_c: 90,
    power_factor: '0.8',
    three_phase: true,
  };

  it('reads a 422 as a refusal with its reason', async () => {
    const fetchImpl = respond(422, { error: 'ValidationError', detail: 'outside Table 4' });
    expect(await sizeCable({ token: 't', request, fetchImpl })).toEqual({
      kind: 'refused',
      detail: 'outside Table 4',
    });
  });

  it('reads a 401 as an expired session and a malformed body as a failure', async () => {
    expect(await sizeCable({ token: 't', request, fetchImpl: respond(401, {}) })).toEqual({
      kind: 'unauthorized',
    });
    expect(await sizeCable({ token: 't', request, fetchImpl: respond(200, {}) })).toEqual({
      kind: 'failed',
    });
  });
});
