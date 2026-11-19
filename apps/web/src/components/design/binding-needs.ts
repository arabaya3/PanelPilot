/**
 * What an incomplete binding still needs on its board, from the existing checks.
 *
 * Read from `bindingProblems` (the editor's own checks) and the existing choice lists, never a
 * second set of rules: each "not chosen" slot, strip role or cable becomes one need, said as
 *
 * - `create`: nothing on this board can fill it yet (no component of the slot's definition, no
 *   terminal strip): the way is the existing add form;
 * - `choose`: compatible items exist but none is chosen: the way is the binding's own selector
 *   (never a suggestion to create a duplicate);
 * - `design`: the choice comes from the design (a cable), which is not known for the current
 *   inputs: the way is the existing refresh, or Design when nothing can be refreshed.
 *
 * Nothing here selects, creates or designs anything.
 */

import {
  bindablePatterns,
  bindingProblems,
  componentChoices,
  stripChoices,
  type BoardChoices,
} from '@/components/design/binding-model';
import type { ConfigBinding, Configuration } from '@/components/design/configuration-model';

export type BindingNeed =
  | { kind: 'create'; what: 'component'; slot: string; template: string | null }
  | { kind: 'create'; what: 'strip'; role: string }
  | { kind: 'choose'; field: 'slot'; key: string; count: number }
  | { kind: 'choose'; field: 'role'; key: string; count: number }
  | { kind: 'choose'; field: 'cable'; count: number }
  | { kind: 'design'; field: 'cable'; refreshable: boolean };

/**
 * @param boards The board choices from a design of the current inputs; null when not known.
 * @param refreshable Whether the editor can refresh them now (the inputs changed since).
 */
export function bindingNeeds(
  config: Configuration,
  binding: ConfigBinding,
  boards: BoardChoices[] | null,
  refreshable: boolean,
): BindingNeed[] {
  const pattern = bindablePatterns(config).find((item) => item.ref === binding.pattern);
  if (pattern === undefined) return [];
  const out: BindingNeed[] = [];
  for (const problem of bindingProblems(config, binding, boards)) {
    if (problem.field === 'slot' && problem.code === 'not_chosen') {
      const slot = pattern.slots.find((item) => item.id === problem.key);
      const template = slot?.template ?? null;
      const count = componentChoices(config, binding.board, template).length;
      out.push(
        count === 0
          ? { kind: 'create', what: 'component', slot: problem.key, template }
          : { kind: 'choose', field: 'slot', key: problem.key, count },
      );
    } else if (problem.field === 'role' && problem.code === 'not_chosen') {
      const count = stripChoices(config, binding.board).length;
      out.push(
        count === 0
          ? { kind: 'create', what: 'strip', role: problem.key }
          : { kind: 'choose', field: 'role', key: problem.key, count },
      );
    } else if (problem.field === 'cable' && problem.code === 'cable_needed') {
      const board = boards?.find((item) => item.id === binding.board);
      if (boards === null || board === undefined) {
        out.push({ kind: 'design', field: 'cable', refreshable });
      } else {
        out.push({ kind: 'choose', field: 'cable', count: board.cables.length });
      }
    }
  }
  return out;
}

/** The binding field a need is about (its element id ends in this), if it has one. */
export function needFieldSuffix(need: BindingNeed): string | null {
  if (need.kind === 'create') {
    return need.what === 'component' ? `slot-${need.slot}` : `role-${need.role}`;
  }
  if (need.field === 'slot' || need.field === 'role') return `${need.field}-${need.key}`;
  return 'cable';
}
