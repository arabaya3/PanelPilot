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
}

const ChecklistContext = createContext<ChecklistValue | null>(null);

/** `messageId` and index composed into one key. */
function keyOf(messageId: string, stepIndex: number): string {
  return `${messageId}:${String(stepIndex)}`;
}

export function ChecklistProvider({ children }: { children: ReactNode }) {
  const [checked, setChecked] = useState<ReadonlySet<string>>(() => new Set());

  const toggle = useCallback((messageId: string, stepIndex: number) => {
    setChecked((current) => {
      const next = new Set(current);
      const key = keyOf(messageId, stepIndex);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const isChecked = useCallback(
    (messageId: string, stepIndex: number) => checked.has(keyOf(messageId, stepIndex)),
    [checked],
  );

  const countChecked = useCallback(
    (messageId: string) => {
      let total = 0;
      for (const key of checked) {
        if (key.startsWith(`${messageId}:`)) total += 1;
      }
      return total;
    },
    [checked],
  );

  const value = useMemo(
    () => ({ isChecked, toggle, countChecked }),
    [isChecked, toggle, countChecked],
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
