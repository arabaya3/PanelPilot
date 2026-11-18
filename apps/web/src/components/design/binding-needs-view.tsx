'use client';

/**
 * "Needed on this board": what an incomplete binding still needs, with the way to each.
 *
 * Links only move focus (and, for a create link, remember the binding field to come back
 * to); they never create, choose, design, preview or save. A count change is announced once,
 * after typing settles, in a small status of its own; the list itself is not live.
 */

import { useEffect, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { needFieldSuffix, type BindingNeed } from '@/components/design/binding-needs';
import { goToTarget } from '@/components/design/configuration-contents';

/** Where the needs lead: the existing add forms and the choice refresh. */
export type NeedTargets = { component: string; strip: string; refresh: string };

/** The binding field to come back to after visiting a create form. */
export type NeedReturn = { binding: string; field: string };

const ANNOUNCE_DELAY_MS = 800;

export function BindingNeeds({
  needs,
  bindingId,
  board,
  rowId,
  targets,
  onFollowCreate,
}: {
  needs: BindingNeed[];
  bindingId: string;
  board: string;
  rowId: string;
  targets: NeedTargets;
  /** Remember where to come back to before a create form is visited (and its board). */
  onFollowCreate: (back: NeedReturn, board: string) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  const [announced, setAnnounced] = useState('');
  // Said only when the count changes after it was first shown (never on opening the editor,
  // so many bindings do not all speak at once), once typing has settled.
  const shown = useRef<number | null>(null);
  const count = needs.length;
  useEffect(() => {
    if (shown.current === null) {
      shown.current = count;
      return;
    }
    if (shown.current === count) return;
    const timer = window.setTimeout(() => {
      shown.current = count;
      setAnnounced(t('needs.status', { count: String(count), binding: bindingId }));
    }, ANNOUNCE_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [count, bindingId, t]);
  if (needs.length === 0) {
    return (
      <p className="sr-only" role="status" data-testid="binding-needs-status">
        {announced}
      </p>
    );
  }

  function link(label: string, target: string, testId: string, before?: () => void) {
    return (
      <a
        href={`#${target}`}
        className="link max-w-full whitespace-normal break-words underline"
        onClick={(event) => {
          event.preventDefault();
          before?.();
          goToTarget(target);
        }}
        data-testid={testId}
      >
        {label}
      </a>
    );
  }

  function item(need: BindingNeed, index: number) {
    const suffix = needFieldSuffix(need);
    const field = suffix === null ? rowId : `${rowId}-${suffix}`;
    if (need.kind === 'create') {
      const back = { binding: bindingId, field: suffix ?? '' };
      return need.what === 'component' ? (
        <>
          <span>
            {need.template === null
              ? t('needs.createComponentAny', { slot: need.slot, board })
              : t('needs.createComponent', { slot: need.slot, template: need.template, board })}
          </span>{' '}
          {link(
            t('needs.goCreateComponent'),
            targets.component,
            `binding-need-create-${String(index)}`,
            () => {
              onFollowCreate(back, board);
            },
          )}
        </>
      ) : (
        <>
          <span>{t('needs.createStrip', { role: need.role, board })}</span>{' '}
          {link(
            t('needs.goCreateStrip'),
            targets.strip,
            `binding-need-create-${String(index)}`,
            () => {
              onFollowCreate(back, board);
            },
          )}
        </>
      );
    }
    if (need.kind === 'choose') {
      const text =
        need.field === 'slot'
          ? t('needs.chooseComponent', { slot: need.key, count: String(need.count) })
          : need.field === 'role'
            ? t('needs.chooseStrip', { role: need.key, count: String(need.count) })
            : need.count === 0
              ? t('needs.noCable', { board })
              : t('needs.chooseCable', { count: String(need.count) });
      return (
        <>
          <span>{text}</span>{' '}
          {link(t('needs.goChoose'), field, `binding-need-choose-${String(index)}`)}
        </>
      );
    }
    return need.refreshable ? (
      <>
        <span>{t('needs.cableOutdated')}</span>{' '}
        {link(t('needs.goRefresh'), targets.refresh, `binding-need-refresh-${String(index)}`)}
      </>
    ) : (
      <span>{t('needs.cableDesign')}</span>
    );
  }

  return (
    <div
      className="flex min-w-0 flex-col gap-1 rounded-md border border-border-subtle p-2 text-xs"
      data-testid="binding-needs"
    >
      <p className="font-semibold" id={`${rowId}-needs-title`}>
        {t('needs.title', { board })}
      </p>
      <ul className="flex min-w-0 flex-col gap-1" aria-labelledby={`${rowId}-needs-title`}>
        {needs.map((need, index) => (
          <li key={index} className="min-w-0 break-words" dir="auto" data-testid="binding-need">
            {item(need, index)}
          </li>
        ))}
      </ul>
      <p className="sr-only" role="status" data-testid="binding-needs-status">
        {announced}
      </p>
    </div>
  );
}

/** In a create form, after following a need: back to the binding field it came from. */
export function BackToBinding({
  back,
  rowIdOf,
  sectionId,
  onDone,
}: {
  back: NeedReturn;
  rowIdOf: (id: string) => string;
  sectionId: string;
  onDone: () => void;
}) {
  const t = useIsolatingTranslations('configuration');
  return (
    <p className="text-sm">
      <a
        href={`#${rowIdOf(back.binding)}`}
        className="link max-w-full whitespace-normal break-words underline"
        onClick={(event) => {
          event.preventDefault();
          // The field, else the binding (the field went), else the bindings section (it went).
          const row = rowIdOf(back.binding);
          const reached =
            (back.field !== '' && goToTarget(`${row}-${back.field}`)) ||
            goToTarget(row) ||
            goToTarget(sectionId);
          if (reached) onDone();
        }}
        data-testid="back-to-binding"
      >
        {t('needs.back', { binding: back.binding })}
      </a>
    </p>
  );
}
