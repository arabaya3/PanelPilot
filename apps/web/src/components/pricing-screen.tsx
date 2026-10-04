'use client';

import { useLocale, useTranslations } from 'next-intl';
import { useEffect, useId, useState } from 'react';

import { AppShell } from '@/components/app-shell';
import {
  fetchPlans,
  periodPrice,
  requestPlan,
  type Interval,
  type PlanCatalogue,
  type PlanKey,
  type PlanOut,
} from '@/lib/billing';
import { acquireTrial } from '@/lib/session';

type Loaded =
  { kind: 'loading' } | { kind: 'failed' } | { kind: 'ready'; catalogue: PlanCatalogue };
type Asked =
  | { kind: 'idle' }
  | { kind: 'sending'; plan: PlanKey }
  | { kind: 'sent'; plan: PlanKey }
  | { kind: 'error'; plan: PlanKey };

/**
 * `/pricing`: the plans, monthly or annual, and a way to ask for one.
 *
 * Asking records the request on this browser's account (a trial carries it
 * into the account its owner signs up for); the plan is activated once paid,
 * so the page says that rather than promising it is live.
 */
export function PricingScreen({
  plansImpl = fetchPlans,
  requestImpl = requestPlan,
  acquireImpl = acquireTrial,
}: {
  plansImpl?: typeof fetchPlans;
  requestImpl?: typeof requestPlan;
  acquireImpl?: typeof acquireTrial;
}) {
  const t = useTranslations('pricing');
  const locale = useLocale();
  const id = useId();
  const [loaded, setLoaded] = useState<Loaded>({ kind: 'loading' });
  const [interval, setInterval] = useState<Interval>('monthly');
  const [seats, setSeats] = useState<Record<string, number>>({});
  const [asked, setAsked] = useState<Asked>({ kind: 'idle' });

  useEffect(() => {
    let live = true;
    void plansImpl().then((outcome) => {
      if (!live) return;
      setLoaded(
        outcome.kind === 'listed'
          ? { kind: 'ready', catalogue: outcome.catalogue }
          : { kind: 'failed' },
      );
    });
    return () => {
      live = false;
    };
  }, [plansImpl]);

  const money = new Intl.NumberFormat(locale === 'en' ? 'en-US' : `${locale}-u-nu-latn`, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  });

  async function ask(plan: PlanOut) {
    setAsked({ kind: 'sending', plan: plan.key });
    const session = await acquireImpl();
    if (session.kind !== 'ready') {
      setAsked({ kind: 'error', plan: plan.key });
      return;
    }
    const outcome = await requestImpl({
      token: session.accessToken,
      plan: plan.key,
      interval,
      seats: seatsOf(plan),
    });
    setAsked(
      outcome.kind === 'requested'
        ? { kind: 'sent', plan: plan.key }
        : { kind: 'error', plan: plan.key },
    );
  }

  function seatsOf(plan: PlanOut): number {
    return seats[plan.key] ?? plan.seats_included;
  }

  function card(plan: PlanOut, annualMonths: number) {
    const count = seatsOf(plan);
    const price = periodPrice(plan, interval, count, annualMonths);
    const extraSeats = plan.extra_seat_usd !== null && plan.max_seats !== null;
    const status = asked.kind !== 'idle' && asked.plan === plan.key ? asked.kind : null;
    return (
      <li
        key={plan.key}
        data-testid={`plan-${plan.key}`}
        className={`flex flex-col gap-3 rounded-lg border bg-surface p-4 ${
          plan.key === 'team' ? 'border-accent' : 'border-border-subtle'
        }`}
      >
        <h2 className="text-lg font-semibold">{t(`plan.${plan.key}.name`)}</h2>
        <p className="text-sm text-text-muted">{t(`plan.${plan.key}.for`)}</p>
        <p className="text-2xl font-bold" dir="ltr">
          {price === null ? t('byAgreement') : money.format(price)}
          {price !== null && price > 0 && (
            <span className="ms-1 text-sm font-normal text-text-muted">
              {t(interval === 'annual' ? 'perYear' : 'perMonth')}
            </span>
          )}
        </p>
        <ul className="flex flex-col gap-1 text-sm">
          <li>{t('seats', { count: plan.seats_included })}</li>
          {plan.extra_seat_usd !== null && (
            <li>{t('extraSeat', { price: money.format(Number(plan.extra_seat_usd)) })}</li>
          )}
          <li>
            {plan.model_calls_per_month === null
              ? t('aiUnlimited')
              : t('ai', { count: plan.model_calls_per_month })}
          </li>
          <li>
            {plan.saved_projects === null
              ? t('projectsUnlimited')
              : t('projects', { count: plan.saved_projects })}
          </li>
          {plan.features.map((feature) => (
            <li key={feature}>{t(`feature.${feature}`)}</li>
          ))}
        </ul>
        {extraSeats && (
          <label className="flex items-center gap-2 text-sm" htmlFor={`${id}-${plan.key}-seats`}>
            {t('seatCount')}
            <input
              id={`${id}-${plan.key}-seats`}
              type="number"
              dir="ltr"
              min={plan.seats_included}
              max={plan.max_seats ?? undefined}
              value={count}
              onChange={(event) => {
                const value = Number(event.target.value);
                if (Number.isFinite(value)) setSeats({ ...seats, [plan.key]: value });
              }}
              className="input w-24"
            />
          </label>
        )}
        {plan.key !== 'free' && (
          <button
            type="button"
            className="btn btn-primary mt-auto"
            disabled={status === 'sending'}
            onClick={() => void ask(plan)}
          >
            {plan.key === 'enterprise' ? t('contact') : t('choose')}
          </button>
        )}
        {status === 'sent' && (
          <p role="status" className="text-sm text-text-muted">
            {t('requested')}
          </p>
        )}
        {status === 'error' && (
          <p role="alert" className="text-sm text-danger">
            {t('requestFailed')}
          </p>
        )}
      </li>
    );
  }

  return (
    <AppShell>
      <h1 className="mb-2 text-2xl font-bold tracking-tight">{t('title')}</h1>
      <p className="mb-5 max-w-3xl text-text-muted">{t('intro')}</p>
      <div
        role="radiogroup"
        aria-label={t('billing')}
        className="mb-5 flex gap-1 self-start rounded-lg border border-border-subtle bg-surface p-1"
      >
        {(['monthly', 'annual'] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={interval === value}
            onClick={() => {
              setInterval(value);
            }}
            className={`rounded-md px-3 py-2 text-sm transition-colors ${
              interval === value
                ? 'bg-accent-subtle font-semibold text-accent-hover'
                : 'font-medium text-text-muted hover:bg-surface-raised hover:text-text'
            }`}
          >
            {t(value)}
          </button>
        ))}
      </div>
      {loaded.kind === 'loading' && <p className="text-text-muted">{t('loading')}</p>}
      {loaded.kind === 'failed' && (
        <p role="alert" className="text-danger">
          {t('loadFailed')}
        </p>
      )}
      {loaded.kind === 'ready' && (
        <>
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {loaded.catalogue.plans.map((plan) =>
              card(plan, loaded.catalogue.annual_months_charged),
            )}
          </ul>
          <p className="mt-5 max-w-3xl text-sm text-text-muted">{t('activation')}</p>
        </>
      )}
    </AppShell>
  );
}
