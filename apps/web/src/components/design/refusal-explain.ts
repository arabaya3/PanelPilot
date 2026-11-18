/**
 * What a configuration refusal means, from the server's structured code and parameters only.
 *
 * A refusal (`configuration_refused` or `configuration_conflict`) carries the composer's code
 * (`params.refusal` or `params.reason`) and the ids it names (`binding`, `cable`, `slot`...).
 * This turns them into the parts the editor says in the user's language: which entry is
 * affected, which of its fields to change (only where the parameters name it; otherwise none,
 * and the entry itself is shown), and the values the translated sentence needs. The server's
 * English sentence is never shown: a refusal the editor has no sentence for gets the generic
 * one, with its code as a technical detail.
 */

/** The binding fields a refusal can point at (their element ids end in these). */
export type RefusalField =
  | { name: 'circuit' }
  | { name: 'cable' }
  | { name: 'slot'; key: string }
  | { name: 'point'; key: string };

/** The refusals the editor has its own sentence for (`configuration.explain.<code>`). */
export const EXPLAINED_REFUSALS = [
  'core_shared_cable_id',
  'core_out_of_range',
  'core_reused',
  'reference_moved',
  'unknown_reference',
  'missing_binding',
  'ambiguous_binding',
  'board_mismatch',
  'conflicting_binding',
  'duplicate_marking',
  'strip_point_conflict',
  'unknown_wire',
  'unknown_pattern',
  'unknown_template',
  'restricted_scope',
  'terminal_not_on_template',
  'contradictory_nets',
  'pattern_invalid',
  'template_conflict',
  'configuration_invalid',
  'design_refused',
] as const;

export type Explained = {
  /** The composer's code ('' when the refusal names none). */
  code: string;
  /** The translation key under `configuration.explain`: the code, or `unknown`. */
  key: string;
  /** The binding the refusal names, if any. */
  binding: string | null;
  /** The field of that binding to go to, only where the parameters name it. */
  field: RefusalField | null;
  /**
   * Whether the field's own choice is what the server refused. False where the field is only
   * where the restriction applies (a correct cable the deployment cannot save yet): then it is
   * pointed at with a neutral note, never marked invalid.
   */
  fieldRefused: boolean;
  /** The values the sentence uses (ids, never server prose). */
  values: Record<string, string>;
};

const text = (value: string | undefined) => (typeof value === 'string' ? value : '');

/** What `unknown_reference` names, from the parameter the server sent. */
function unknownKind(params: Record<string, string>): string {
  for (const kind of ['cable', 'circuit', 'strip', 'component', 'device', 'board'] as const) {
    if (text(params[kind]) !== '') return kind;
  }
  return 'other';
}

/** Read a refusal's parameters. */
export function explainRefusal(params: Record<string, string> | undefined): Explained {
  const p = params ?? {};
  const code = text(p.refusal) || text(p.reason);
  const binding = text(p.binding) || null;
  let field: RefusalField | null = null;
  let key: string = (EXPLAINED_REFUSALS as readonly string[]).includes(code) ? code : 'unknown';
  if (binding !== null) {
    if (code === 'core_shared_cable_id' || code === 'core_out_of_range' || code === 'core_reused') {
      field = { name: 'cable' };
    } else if (code === 'unknown_reference') {
      const kind = unknownKind(p);
      key = `unknown_reference_${kind}`;
      if (kind === 'cable') field = { name: 'cable' };
      else if (kind === 'circuit') field = { name: 'circuit' };
      else if (text(p.slot) !== '') field = { name: 'slot', key: p.slot as string };
    } else if (code === 'reference_moved') {
      const which = text(p.field);
      if (which === 'circuit') field = { name: 'circuit' };
      else if (which === 'cable') field = { name: 'cable' };
      else if (which === 'slot' && text(p.key) !== '')
        field = { name: 'slot', key: p.key as string };
    } else if (
      (code === 'missing_binding' ||
        code === 'ambiguous_binding' ||
        code === 'conflicting_binding') &&
      text(p.slot) !== ''
    ) {
      field = { name: 'slot', key: p.slot as string };
    } else if (
      (code === 'missing_binding' || code === 'strip_point_conflict') &&
      text(p.point) !== ''
    ) {
      field = { name: 'point', key: p.point as string };
    }
  }
  const values: Record<string, string> = {
    binding: binding ?? '',
    cable: text(p.cable) || text(p.ref),
    core: text(p.core) || '—',
    otherBoard: text(p.other_board) || '—',
    ref: text(p.ref) || '—',
    slot: text(p.slot) || text(p.key) || '—',
    point: text(p.point) || '—',
    strip: text(p.strip) || '—',
    pattern: text(p.pattern) || '—',
    template: text(p.template) || '—',
    terminal: text(p.terminal) || '—',
    role: text(p.role) || '—',
    before: text(p.before) || '—',
    after: text(p.after) || '—',
    code: code || '—',
  };
  return { code, key, binding, field, fieldRefused: code !== 'core_shared_cable_id', values };
}

/** The element id of a refused field in a binding row (`rowId` from `entryElementId`). */
export function refusalFieldId(rowId: string, field: RefusalField): string {
  if (field.name === 'slot') return `${rowId}-slot-${field.key}`;
  if (field.name === 'point') return `${rowId}-point-${field.key}`;
  return `${rowId}-${field.name}`;
}

/** Whether a refused field is this one (for marking it). */
export function isRefusedField(
  field: RefusalField | null,
  name: RefusalField['name'],
  key?: string,
): boolean {
  if (field === null || field.name !== name) return false;
  return !('key' in field) || field.key === key;
}

type Translate = (key: string, values?: Record<string, string>) => string;

/** The board a binding (or strip, component) is on in the configuration, or '—'. */
function boardOf(doc: Record<string, unknown>, binding: string | null): string {
  if (binding === null) return '—';
  const list = doc.bindings;
  const found = Array.isArray(list)
    ? (list as { id?: unknown; board?: unknown }[]).find((item) => item.id === binding)
    : undefined;
  return typeof found?.board === 'string' && found.board !== '' ? found.board : '—';
}

/**
 * The one sentence a refusal is said with: what failed, which entry, what to do next.
 *
 * @param t The `configuration` translations.
 * @param mode `save` (nothing was saved) or `preview`.
 */
export function refusalSentence(
  t: Translate,
  explained: Explained,
  doc: Record<string, unknown>,
  mode: 'save' | 'preview',
  /** The entry the refusal names when it is not a binding (a strip, component, pattern...). */
  entry?: string,
): string {
  const key =
    explained.binding === null && entry === undefined && explained.key === 'unknown'
      ? 'unknownNoEntry'
      : explained.key;
  const explanation = t(`explain.${key}`, explained.values);
  if (explained.binding === null) {
    if (entry !== undefined) {
      return t(mode === 'save' ? 'explain.notSavedEntry' : 'explain.previewEntry', {
        entry,
        explanation,
      });
    }
    return t(mode === 'save' ? 'explain.notSavedPlain' : 'explain.previewPlain', { explanation });
  }
  return t(mode === 'save' ? 'explain.notSaved' : 'explain.preview', {
    entry: t('editor.entryKind.binding', { id: explained.binding }),
    board: boardOf(doc, explained.binding),
    explanation,
  });
}
