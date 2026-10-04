import type { components } from '@panelpilot/shared-types';

export type PlanCatalogue = components['schemas']['PlanCatalogue'];
export type PlanOut = components['schemas']['PlanOut'];
export type PlanKey = components['schemas']['PlanKey'];
export type Interval = components['schemas']['Interval'];
export type Entitlements = components['schemas']['Entitlements'];

export type CatalogueOutcome = { kind: 'listed'; catalogue: PlanCatalogue } | { kind: 'failed' };
export type RequestOutcome =
  | { kind: 'requested'; entitlements: Entitlements }
  | {
      kind: 'refused';
      detail: string;
      code?: string | undefined;
      params?: Record<string, string> | undefined;
    }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

type Options = { fetchImpl?: typeof fetch; endpoint?: string };

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** Every plan and its price: `GET /api/v1/billing/plans`, public. */
export async function fetchPlans(options: Options = {}): Promise<CatalogueOutcome> {
  const { fetchImpl = fetch, endpoint = '/api/v1/billing/plans' } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, { method: 'GET' });
  } catch {
    return { kind: 'failed' };
  }
  if (!response.ok) return { kind: 'failed' };
  const payload = await json(response);
  if (typeof payload !== 'object' || payload === null || !('plans' in payload)) {
    return { kind: 'failed' };
  }
  return { kind: 'listed', catalogue: payload as PlanCatalogue };
}

/**
 * Ask to move the account to a plan: `POST /api/v1/billing/request`. Nothing
 * the account may do changes until the plan is activated.
 */
export async function requestPlan(
  options: Options & { token: string; plan: PlanKey; interval: Interval; seats: number },
): Promise<RequestOutcome> {
  const { fetchImpl = fetch, endpoint = '/api/v1/billing/request', token } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        plan: options.plan,
        interval: options.interval,
        seats: options.seats,
      }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  const payload = await json(response);
  if (response.status === 400 || response.status === 422) {
    const body = (payload ?? {}) as { detail?: unknown; code?: unknown; params?: unknown };
    return {
      kind: 'refused',
      detail: typeof body.detail === 'string' ? body.detail : '',
      code: typeof body.code === 'string' ? body.code : undefined,
      params:
        typeof body.params === 'object' && body.params !== null
          ? (body.params as Record<string, string>)
          : undefined,
    };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null || !('plan' in payload)) {
    return { kind: 'failed' };
  }
  return { kind: 'requested', entitlements: payload as Entitlements };
}

/** What one billing period of a plan costs for some seats, or null when agreed. */
export function periodPrice(
  plan: PlanOut,
  interval: Interval,
  seats: number,
  annualMonths: number,
): number | null {
  if (plan.monthly_usd === null) return null;
  const extra = Math.max(0, seats - plan.seats_included) * Number(plan.extra_seat_usd ?? 0);
  const monthly = Number(plan.monthly_usd) + extra;
  return interval === 'annual' ? monthly * annualMonths : monthly;
}
