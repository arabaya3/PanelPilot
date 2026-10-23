import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { preferredLocale } from '@/components/locale-provider';
import type { designProject, BoardDesignResponse } from '@/lib/design';
import { detectMarket, withMarket, type MarketInfo } from '@/lib/market';

import { renderApp } from './helpers';

/*
 * Queries are scoped to the view they read: the whole workspace is rendered,
 * and a page-wide label or role query costs tens of milliseconds a call in
 * jsdom.
 */
const overviewView = () => within(screen.getByTestId('view-overview'));
const scheduleView = () => within(screen.getByTestId('view-schedule'));
const firstRow = () => within(screen.getByTestId('design-load-0'));

/** Markets: a visitor's country sets a new board's supply and its code's rules. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const MARKETS: MarketInfo[] = [
  {
    code: 'PS',
    name: 'Palestine',
    voltage_v: 400,
    phase_voltage_v: 230,
    frequency_hz: 50,
    earthing: 'TN-S',
    currency: 'ILS',
    regulation: 'IEC 60364',
    max_voltage_drop_percent: null,
    rcd_every_final_circuit: false,
    languages: ['ar', 'en'],
  },
  {
    code: 'SA',
    name: 'Saudi Arabia',
    voltage_v: 400,
    phase_voltage_v: 230,
    frequency_hz: 60,
    earthing: 'TN-S',
    currency: 'SAR',
    regulation: 'Saudi Building Code SBC 401',
    max_voltage_drop_percent: '4',
    rcd_every_final_circuit: false,
    languages: ['ar', 'en'],
  },
];

describe('markets', () => {
  it('reads the market from the time zone, and Jerusalem from the language', () => {
    expect(detectMarket('Asia/Hebron', ['en'])).toBe('PS');
    expect(detectMarket('Asia/Amman', ['ar-JO'])).toBe('JO');
    expect(detectMarket('Asia/Jerusalem', ['ar'])).toBe('PS');
    expect(detectMarket('Asia/Jerusalem', ['he-IL', 'en'])).toBe('IL');
    expect(detectMarket('Europe/Berlin', ['de'])).toBe('');
  });

  it('adds the market to the settings unless they name one', () => {
    expect(withMarket(null, 'PS', 'iec-default')).toEqual({ key: 'iec-default', market: 'PS' });
    expect(withMarket({ key: 'acme', market: 'IL' }, 'PS', 'x')).toEqual({
      key: 'acme',
      market: 'IL',
    });
    expect(withMarket(null, '', 'x')).toBeNull();
  });

  it('opens in the browser language the app speaks', () => {
    expect(preferredLocale(['fr-FR', 'ar-PS'])).toBe('ar');
    expect(preferredLocale(['de'])).toBeNull();
  });

  it('says the market basis in the reader language, regulation included', async () => {
    const english = {
      ...MARKETS[0]!,
      regulation: 'IEC 60364 (adopted by the Palestine Standards Institution)',
    };
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        marketsImpl={vi.fn().mockResolvedValue([english, MARKETS[1]!])}
        readMarketImpl={() => 'PS'}
      />,
      { locale: 'ar' },
    );
    const basis = (await screen.findByTestId('market-basis')).textContent ?? '';
    expect(basis).not.toContain('Palestine');
    expect(basis).not.toContain('adopted');
  });

  it('starts a board at the detected market and designs under it', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'designed',
      response: {
        project: { info: { name: 'P' }, boards: [], parts: [] },
        profile: { key: 'iec-default' },
      } as unknown as BoardDesignResponse,
    });
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        designImpl={designImpl}
        marketsImpl={vi.fn().mockResolvedValue(MARKETS)}
        readMarketImpl={() => 'SA'}
      />,
    );
    expect((await screen.findByTestId('market-basis')).textContent).toContain('60 Hz');
    fireEvent.change(overviewView().getByLabelText('Project name'), { target: { value: 'P' } });
    fireEvent.change(firstRow().getByLabelText('Circuit'), { target: { value: 'Sockets' } });
    fireEvent.change(firstRow().getByLabelText(/^Power.*kW/), { target: { value: '1' } });
    const button = screen.getByText('Design the board', { selector: 'button' });
    await waitFor(() => {
      expect(button.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.click(button);
    await waitFor(() => {
      expect(designImpl).toHaveBeenCalled();
    });
    const request = designImpl.mock.calls[0]?.[0].request;
    expect(request?.boards[0]?.supply?.frequency_hz).toBe('60');
    expect(request?.profile).toEqual({ key: 'iec-default', market: 'SA' });

    fireEvent.change(scheduleView().getByLabelText('Market'), { target: { value: 'PS' } });
    expect(screen.getByTestId('market-basis').textContent).toContain('50 Hz');
  });
});
