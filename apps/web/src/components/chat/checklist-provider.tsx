'use client';

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';

/**
 * Which solution steps an engineer has ticked off.
 *
 * Held here rather than inside the card for a reason that is easy to miss: the
 * transcript is windowed, so a card scrolled out of view is *unmounted*.
 * Component-local state would be discarded silently and the ticks would be
 * gone when the engineer scrolled back — which is precisely the case the
 * acceptance criterion names, and the one an engineer working down a list on a
 * phone would hit first.
 *
 * Keyed by message and step index, so two diagnostic cards in one session
 * track independently. A new question produces a new message id and therefore
 * starts unchecked, without disturbing the cards already on screen.
 *
 * Session-lifetime only. Nothing here is persisted: no backend store exists
 * for it yet, and writing a half-finished repair checklist to a shared
 * workshop terminal's storage is not a default worth choosing quietly.
 */

interface ChecklistValue {
  isChecked: (messageId: string, stepIndex: number) => boolean;
  toggle: (messageId: string, stepIndex: number) => void;
  /** How many steps are ticked on one card, for the progress summary. */
  countChecked: (messageId: string) => number;
  /**
   * Forget everything ticked on one turn.
   *
   * Needed because a retry re-asks the same question under the *same* message
   * id and can come back with a different set of steps. Ticks are positional,
   * so without this they rebind to whatever arrives next: step 2 of advice the
   * engineer has never read renders pre-ticked and struck through, which reads
   * as "I already did this" and invites skipping it. Skipping a step in an
   * electrical repair is a safety consequence, not a cosmetic one.
   */
  clear: (messageId: string) => void;
}

const ChecklistContext = createContext<ChecklistValue | null>(null);

/**
 * Ticked step indices, per message.
 *
 * Nested rather than one flat `messageId:index` string key. The flat form was
 * only unambiguous because ids are minted locally as `a1`, `a2` and happen to
 * contain no colon — nothing enforced that, and a server-supplied id (a
 * resumed session, a shared transcript) would have made two different steps
 * collide silently. This shape cannot, and it makes the per-message clear that
 * a retry needs a single delete.
 */
type Checked = ReadonlyMap<string, ReadonlySet<number>>;

export function ChecklistProvider({ children }: { children: ReactNode }) {
  const [checked, setChecked] = useState<Checked>(() => new Map());

  const toggle = useCallback((messageId: string, stepIndex: number) => {
    setChecked((current) => {
      const next = new Map(current);
      const steps = new Set(current.get(messageId) ?? []);
      if (steps.has(stepIndex)) steps.delete(stepIndex);
      else steps.add(stepIndex);
      if (steps.size === 0) next.delete(messageId);
      else next.set(messageId, steps);
      return next;
    });
  }, []);

  const clear = useCallback((messageId: string) => {
    setChecked((current) => {
      if (!current.has(messageId)) return current;
      const next = new Map(current);
      next.delete(messageId);
      return next;
    });
  }, []);

  const isChecked = useCallback(
    (messageId: string, stepIndex: number) => checked.get(messageId)?.has(stepIndex) ?? false,
    [checked],
  );

  const countChecked = useCallback(
    (messageId: string) => checked.get(messageId)?.size ?? 0,
    [checked],
  );

  const value = useMemo(
    () => ({ isChecked, toggle, countChecked, clear }),
    [isChecked, toggle, countChecked, clear],
  );

  return <ChecklistContext.Provider value={value}>{children}</ChecklistContext.Provider>;
}

/**
 * Read the checklist.
 *
 * Returns `null` outside a provider rather than throwing, because the card is
 * rendered on its own in other contexts — a tokens gallery, a future printed
 * or shared view — and a diagnostic card that cannot be read without a
 * checklist around it would be the wrong dependency. Without a provider the
 * steps render as plain text, which is the correct degradation.
 */
export function useChecklist(): ChecklistValue | null {
  return useContext(ChecklistContext);
}
