import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PricingScreen } from '@/components/pricing-screen';
import type { fetchPlans } from '@/lib/billing';
import { periodPrice, requestPlan, type PlanCatalogue, type PlanOut } from '@/lib/billing';

import { renderApp } from './helpers';

/** `/pricing`: the plans and a way to ask for one. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const PLANS: PlanCatalogue['plans'] = [
  {
    key: 'free',
    monthly_usd: '0',
    annual_usd: '0',
    seats_included: 1,
    extra_seat_usd: null,
    max_seats: 1,
    model_calls_per_month: 30,
    saved_projects: 3,
    features: ['quotation'],
  },
  {
    key: 'team',
    monthly_usd: '79',
    annual_usd: '790',
    seats_included: 3,
    extra_seat_usd: '25',
    max_seats: 10,
    model_calls_per_month: 2000,
    saved_projects: null,
    features: ['approval_workflow', 'company_settings', 'ecad_export', 'quotation'],
  },
  {
    key: 'enterprise',
    monthly_usd: null,
    annual_usd: null,
    seats_included: 50,
    extra_seat_usd: null,
    max_seats: null,
    model_calls_per_month: null,
    saved_projects: null,
    features: ['quotation'],
  },
];
const [, TEAM, ENTERPRISE] = PLANS as [PlanOut, PlanOut, PlanOut];

const CATALOGUE: PlanCatalogue = { currency: 'USD', annual_months_charged: 10, plans: PLANS };

const listed = vi
  .fn<typeof fetchPlans>()
  .mockResolvedValue({ kind: 'listed', catalogue: CATALOGUE });

describe('pricing', () => {
  it('prices extra seats and annual billing', () => {
    const team = TEAM;
    expect(periodPrice(team, 'monthly', 3, 10)).toBe(79);
    expect(periodPrice(team, 'monthly', 5, 10)).toBe(129);
    expect(periodPrice(team, 'annual', 3, 10)).toBe(790);
    expect(periodPrice(ENTERPRISE, 'annual', 60, 10)).toBeNull();
  });

  it('shows each plan and switches to annual prices', async () => {
    renderApp(<PricingScreen plansImpl={listed} />);
    const team = await screen.findByTestId('plan-team');
    expect(team.textContent).toContain('$79');
    expect(team.textContent).toContain('2000 AI requests a month');
    expect(screen.getByTestId('plan-enterprise').textContent).toContain('By agreement');
    fireEvent.click(screen.getByRole('radio', { name: 'Annual' }));
    expect(screen.getByTestId('plan-team').textContent).toContain('$790');
  });

  it('asks for a plan with its seats and says it waits on payment', async () => {
    const asked = vi.fn<typeof requestPlan>().mockResolvedValue({
      kind: 'requested',
      entitlements: {} as never,
    });
    renderApp(
      <PricingScreen
        plansImpl={listed}
        requestImpl={asked}
        acquireImpl={vi.fn().mockResolvedValue(READY)}
      />,
    );
    await screen.findByTestId('plan-team');
    fireEvent.change(screen.getByLabelText('Engineers'), { target: { value: '5' } });
    expect(screen.getByTestId('plan-team').textContent).toContain('$129');
    const team = screen.getByTestId('plan-team');
    fireEvent.click(team.querySelector('button') as HTMLButtonElement);
    await waitFor(() => {
      expect(asked).toHaveBeenCalledWith({
        token: 'tok',
        plan: 'team',
        interval: 'monthly',
        seats: 5,
      });
    });
    expect((await screen.findByRole('status')).textContent).toContain('Request received');
  });

  it('says so when the plans cannot be loaded', async () => {
    renderApp(<PricingScreen plansImpl={vi.fn().mockResolvedValue({ kind: 'failed' })} />);
    expect((await screen.findByRole('alert')).textContent).toContain('could not be loaded');
  });

  it('reads a refusal from the request endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'no', code: 'plan_seats', params: { most: '1' } }), {
        status: 400,
      }),
    );
    const outcome = await requestPlan({
      token: 't',
      plan: 'engineer',
      interval: 'monthly',
      seats: 2,
      fetchImpl,
    });
    expect(outcome).toEqual({
      kind: 'refused',
      detail: 'no',
      code: 'plan_seats',
      params: { most: '1' },
    });
  });
});
