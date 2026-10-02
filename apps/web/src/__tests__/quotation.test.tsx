import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { QuotationPanel } from '@/components/quotation-panel';
import type { exportDesign } from '@/lib/design';
import { importPriceList, priceDesign, type DesignProject, type Quotation } from '@/lib/design';

import { renderApp } from './helpers';

/** The quotation under a designed board: prices in, lines and totals out. */

const PROJECT = { info: { name: 'Pocket' }, boards: [], parts: [] } as unknown as DesignProject;

const QUOTED: Quotation = {
  currency: 'JOD',
  lines: [
    {
      description: 'Circuit-breaker 1P C16',
      designations: ['-Q3'],
      quantity: '1',
      unit: 'pcs',
      key: 'circuit_breaker:1P:C16',
      unit_price: '4.5',
      total: '4.50',
    },
    {
      description: 'Residual current circuit-breaker 4P 40A 30mA',
      designations: ['-F1'],
      quantity: '1',
      unit: 'pcs',
      key: 'residual_current_device:4P:40A:30mA',
      unit_price: null,
      total: null,
    },
  ],
  materials: '4.50',
  labour: '0.00',
  markup: '0.00',
  vat: '0.72',
  total: '5.22',
  unpriced: ['residual_current_device:4P:40A:30mA (no price)'],
  complete: false,
};

function panel(overrides: Partial<Parameters<typeof QuotationPanel>[0]> = {}) {
  renderApp(
    <QuotationPanel
      token="tok"
      project={PROJECT}
      profile={null}
      saveImpl={vi.fn()}
      {...overrides}
    />,
  );
}

describe('quotation', () => {
  it('prices with the rates entered and shows unpriced lines as such', async () => {
    const priceImpl = vi
      .fn<typeof priceDesign>()
      .mockResolvedValue({ kind: 'priced', quotation: QUOTED });
    panel({ priceImpl });
    fireEvent.change(screen.getByLabelText('Labour per circuit'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Price the board' }));

    expect((await screen.findByTestId('quote-total')).textContent).toBe('5.22');
    expect(screen.getByText('not priced')).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toContain('1 line is not priced');
    const pricing = priceImpl.mock.calls[0]?.[0].pricing;
    expect(pricing?.labour_per_circuit).toBe('5');
    expect(pricing?.vat_percent).toBe('16');
    expect(pricing?.cable_length_m).toBeNull();
  });

  it('loads a price list and sends it with the pricing', async () => {
    const importImpl = vi.fn<typeof importPriceList>().mockResolvedValue({
      kind: 'imported',
      entries: [{ key: 'X', description: '', unit_price: '2' }],
    });
    const priceImpl = vi
      .fn<typeof priceDesign>()
      .mockResolvedValue({ kind: 'priced', quotation: QUOTED });
    panel({ importImpl, priceImpl });
    fireEvent.change(screen.getByLabelText('Price list'), {
      target: { files: [new File(['x'], 'prices.csv')] },
    });
    expect((await screen.findByTestId('quote-message')).textContent).toBe('1 price loaded.');
    fireEvent.click(screen.getByRole('button', { name: 'Price the board' }));
    await screen.findByTestId('quote-result');
    expect(priceImpl.mock.calls[0]?.[0].pricing.price_list).toEqual([
      { key: 'X', description: '', unit_price: '2' },
    ]);
  });

  it('downloads the quotation PDF with the pricing', async () => {
    const blob = new Blob(['%PDF']);
    const exportImpl = vi
      .fn<typeof exportDesign>()
      .mockResolvedValue({ kind: 'exported', blob, filename: 'Pocket.quotation.pdf' });
    const saveImpl = vi.fn();
    panel({ exportImpl, saveImpl });
    fireEvent.click(screen.getByTestId('quote-export-pdf'));
    await waitFor(() => {
      expect(saveImpl).toHaveBeenCalledWith(blob, 'Pocket.quotation.pdf');
    });
    expect(exportImpl.mock.calls[0]?.[0]).toMatchObject({ format: 'quotation_pdf', token: 'tok' });
    expect(exportImpl.mock.calls[0]?.[0].pricing?.currency).toBe('JOD');
  });

  it('shows a refused price list in the server’s words', async () => {
    panel({
      importImpl: vi
        .fn<typeof importPriceList>()
        .mockResolvedValue({ kind: 'refused', detail: 'no header row names a key' }),
    });
    fireEvent.change(screen.getByLabelText('Price list'), {
      target: { files: [new File(['x'], 'bad.csv')] },
    });
    expect((await screen.findByTestId('quote-message')).textContent).toBe(
      'no header row names a key',
    );
  });

  it('price client posts the pricing and maps a refusal', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ detail: 'bad' }), { status: 422 }));
    expect(
      await priceDesign({
        token: 't',
        project: PROJECT,
        pricing: { currency: 'JOD', price_list: [] } as never,
        fetchImpl,
      }),
    ).toEqual({ kind: 'refused', detail: 'bad' });
    const ok = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify([]), { status: 200 }));
    expect(
      await importPriceList({
        token: 't',
        file: new Blob(['a']),
        filename: 'p.csv',
        fetchImpl: ok,
      }),
    ).toEqual({ kind: 'imported', entries: [] });
  });
});
