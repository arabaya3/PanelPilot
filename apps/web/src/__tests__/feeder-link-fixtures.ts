import type { V2ModelSummary } from '@/lib/design';

/** A synthetic v2/v3 model with terminals on three boards (M7a tests). */
export const LINK_MODEL = {
  definitions: [],
  components: [],
  strips: [],
  nets: [],
  connections: [],
  wires: [],
  findings: [],
  terminals: [
    row('MDB', 'component', 'cmp-q1', 'Q1', 't-2', '2', null),
    row('MDB', 'strip', 'str-x0', 'X0', 'pt-1', '1', 'outer'),
    row('DB1', 'strip', 'str-x1', 'X1', 'pt-9', 'L1', 'inner'),
    row('DB2', 'component', 'cmp-k', 'K9', 't-1', 'A1', null),
  ],
} as unknown as V2ModelSummary;

export function row(
  board: string,
  kind: string,
  owner: string,
  designation: string,
  terminal: string,
  marking: string,
  side: string | null,
) {
  return {
    board_id: board,
    owner_kind: kind,
    owner_id: owner,
    designation,
    terminal_id: terminal,
    marking,
    side,
    position: null,
    level: null,
    kind: 'feed',
    required: { value: null, source: 'unresolved' },
    net_id: null,
    signal: null,
    connected_to: [],
    template_ref: null,
    pattern_refs: [],
    net_origin: null,
    flags: [],
  };
}
