import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CalcScreen } from '@/components/calc-screen';
import {
  listDriveRanges,
  selectVfd,
  type DriveRangeSummary,
  type VfdSelectionResponse,
} from '@/lib/calculations';

import { renderApp } from './helpers';

/** The drive tab of `/calc`: `/calculations/vfd-selection` answered 501 and had no page. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const MANUAL = {
  document_id: 'abb-3AUA0000078093',
  document_title: 'ACS880-01 drives hardware manual',
  manufacturer: 'ABB',
};

const SELECTED: VfdSelectionResponse = {
  result: {
    frame_reference: 'ACS880-01-061A-3 (R4)',
    rated_output_current_a: '45',
    applied_factors: [
      { name: 'temperature 40 °C', value: '1', source: { ...MANUAL, page: 243 } },
      { name: 'altitude 0 m', value: '1', source: { ...MANUAL, page: 244 } },
    ],
    drive_range: 'abb-acs880-01',
    manufacturer: 'ABB',
    series: 'ACS880-01',
  },
  motor_current_a: '40.17',
  sources: [{ ...MANUAL, page: 234, section: 'Electrical ratings, IEC, Un = 400 V' }],
};

const RANGES: DriveRangeSummary[] = [
  {
    key: 'abb-acs880-01',
    manufacturer: 'ABB',
    series: 'ACS880-01',
    bands: [{ low_v: '380', high_v: '415' }],
    heavy_duty: true,
    source: { ...MANUAL, page: 234 },
  },
  {
    key: 'yaskawa-ga500',
    manufacturer: 'Yaskawa',
    series: 'GA500',
    bands: [{ low_v: '380', high_v: '480' }],
    heavy_duty: true,
    source: {
      document_id: 'yaskawa-SIEPC71061752',
      document_title: 'GA500',
      manufacturer: 'Yaskawa',
      page: 40,
    },
  },
];

async function openDriveTab(
  selectImpl: typeof selectVfd,
  listImpl: typeof listDriveRanges = vi.fn().mockResolvedValue({ kind: 'listed', ranges: RANGES }),
) {
  renderApp(
    <CalcScreen
      selectImpl={selectImpl}
      listImpl={listImpl}
      acquireImpl={vi.fn().mockResolvedValue(READY)}
    />,
  );
  fireEvent.click(screen.getByTestId('calc-tab-vfd'));
  fireEvent.change(screen.getByLabelText(/^Motor power\s*\(kW\)/), {
    target: { value: '22' },
  });
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Select the drive' }).hasAttribute('disabled')).toBe(
      false,
    );
  });
}

describe('drive selection', () => {
  it('sends the motor and site, and shows the drive with its sources', async () => {
    const selectImpl = vi.fn().mockResolvedValue({ kind: 'selected', response: SELECTED });
    await openDriveTab(selectImpl);
    fireEvent.change(screen.getByLabelText('Duty'), { target: { value: 'heavy' } });
    fireEvent.click(screen.getByRole('button', { name: 'Select the drive' }));

    await screen.findByTestId('vfd-result');
    expect(selectImpl).toHaveBeenCalledWith({
      token: 'tok',
      request: {
        motor_power_kw: '22',
        supply_voltage_v: '400',
        motor_efficiency: '0.93',
        motor_power_factor: '0.85',
        duty_class: 'heavy',
        altitude_m: '0',
        ambient_temp_c: '40',
      },
    });
    expect(screen.getByText('ABB ACS880-01-061A-3 (R4)')).toBeTruthy();
    expect(screen.getByText('40.2 A')).toBeTruthy();
    expect(screen.getByTestId('calc-sources').textContent).toContain('p. 234');
  });

  it('offers the listed series and sends the one picked', async () => {
    const selectImpl = vi
      .fn<typeof selectVfd>()
      .mockResolvedValue({ kind: 'selected', response: SELECTED });
    await openDriveTab(selectImpl);
    const picker = screen.getByLabelText('Drive series');
    await screen.findByRole('option', { name: 'Yaskawa GA500' });
    // The default is offered once, as the empty choice, not again from the list.
    expect(screen.getAllByRole('option', { name: /ACS880-01/ })).toHaveLength(1);
    fireEvent.change(picker, { target: { value: 'yaskawa-ga500' } });
    fireEvent.click(screen.getByRole('button', { name: 'Select the drive' }));

    await screen.findByTestId('vfd-result');
    expect(selectImpl.mock.calls[0]?.[0].request.drive_range).toBe('yaskawa-ga500');
  });

  it('still selects from the default when the series list cannot be read', async () => {
    const selectImpl = vi
      .fn<typeof selectVfd>()
      .mockResolvedValue({ kind: 'selected', response: SELECTED });
    await openDriveTab(selectImpl, vi.fn().mockResolvedValue({ kind: 'failed' }));
    fireEvent.click(screen.getByRole('button', { name: 'Select the drive' }));

    await screen.findByTestId('vfd-result');
    expect(selectImpl.mock.calls[0]?.[0].request).not.toHaveProperty('drive_range');
  });

  it('shows why a motor was refused', async () => {
    const selectImpl = vi.fn().mockResolvedValue({
      kind: 'refused',
      detail: '690 V is outside the ACS880-01-xxxx-3 supply range',
    });
    await openDriveTab(selectImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Select the drive' }));

    const alert = await screen.findByTestId('vfd-error');
    expect(alert.textContent).toContain('690 V is outside');
  });

  it('opens on cable sizing', () => {
    renderApp(<CalcScreen acquireImpl={vi.fn().mockResolvedValue(READY)} />);
    expect(screen.getByTestId('calc-tab-cable').getAttribute('aria-selected')).toBe('true');
  });
});

describe('selectVfd', () => {
  const request = {
    motor_power_kw: '22',
    supply_voltage_v: '400',
    motor_efficiency: '0.93',
    motor_power_factor: '0.85',
    duty_class: 'normal' as const,
    altitude_m: '0',
    ambient_temp_c: '40',
  };

  it('reads a 422 as a refusal and a malformed body as a failure', async () => {
    const refuse = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify({ detail: 'no type' }), { status: 422 }));
    expect(await selectVfd({ token: 't', request, fetchImpl: refuse })).toEqual({
      kind: 'refused',
      detail: 'no type',
    });
    const junk = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    expect(await selectVfd({ token: 't', request, fetchImpl: junk })).toEqual({ kind: 'failed' });
  });
});

describe('listDriveRanges', () => {
  it('reads the list, and says when it cannot', async () => {
    const ok = vi.fn().mockResolvedValue(new Response(JSON.stringify(RANGES), { status: 200 }));
    expect(await listDriveRanges({ token: 't', fetchImpl: ok })).toEqual({
      kind: 'listed',
      ranges: RANGES,
    });
    expect(ok.mock.calls[0]?.[1]).toEqual({ headers: { Authorization: 'Bearer t' } });
    const gone = vi.fn().mockResolvedValue(new Response('', { status: 401 }));
    expect(await listDriveRanges({ token: 't', fetchImpl: gone })).toEqual({
      kind: 'unauthorized',
    });
    const down = vi.fn().mockRejectedValue(new Error('offline'));
    expect(await listDriveRanges({ token: 't', fetchImpl: down })).toEqual({ kind: 'failed' });
    const odd = vi.fn().mockResolvedValue(new Response('{}', { status: 200 }));
    expect(await listDriveRanges({ token: 't', fetchImpl: odd })).toEqual({ kind: 'failed' });
  });
});
