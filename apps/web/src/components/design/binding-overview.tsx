'use client';

/**
 * M9d: the project's bindings at a glance, grouped by board
 * (docs/architecture/m9d-binding-reuse.md). For each binding: its pattern and
 * what is known against it, kept apart by where it comes from:
 *
 * - **Local checks**: the editor's own checks of the configuration as edited
 *   (bindingProblems, wireProblems). They say what a save would be refused for.
 * - **Last save refusal**: the server's refusal of the last save, where it names
 *   this binding.
 * - **Last validation**: findings of the last server validation whose location
 *   names one of this binding's parts (by the ids the composer derives), marked
 *   outdated once the configuration changes.
 *
 * Nothing here is a second rules engine, and nothing says a binding is
 * complete, correct or safe: an empty list only means none of these found
 * anything.
 */
import { useIsolatingTranslations } from '@/components/design/bidi';
import { explainRefusal } from '@/components/design/refusal-explain';
import {
  bindingProblems,
  bindingRowIds,
  bindingState,
  type BindingProblem,
  type BoardChoices,
} from '@/components/design/binding-model';
import {
  bindingsOf,
  type ConfigBinding,
  type Configuration,
  type ConflictTarget,
} from '@/components/design/configuration-model';
import { WIRE_FIELDS, wireProblems, wireRows } from '@/components/design/wire-model';
import type { V2Finding } from '@/lib/design';

export type OverviewValidation = {
  findings: V2Finding[];
  /** The configuration validated is the one being edited. */
  current: boolean;
  revision: number;
};

type Issue = { key: string; text: string; target: string; fallback: string };

function focusId(id: string, fallback: string) {
  // A field the binding no longer renders (a kept key, say): the binding itself.
  const element = document.getElementById(id) ?? document.getElementById(fallback);
  if (element !== null && 'scrollIntoView' in element) element.scrollIntoView({ block: 'center' });
  element?.focus();
}

/** The element a binding problem is said beside (ids as BindingRow and WireDetails give them). */
export function problemTarget(rowId: string, problem: BindingProblem): string {
  switch (problem.field) {
    case 'pattern':
      return `${rowId}-pattern-problem`;
    case 'board':
      return `${rowId}-board`;
    case 'circuit':
      return `${rowId}-circuit`;
    case 'cable':
    case 'core':
      return `${rowId}-cable`;
    case 'slot':
      return `${rowId}-slot-${problem.key}`;
    case 'role':
      return `${rowId}-role-${problem.key}`;
    case 'marking':
      return `${rowId}-point-${problem.key}`;
    case 'extra':
      return rowId;
  }
}

/** Validation findings whose location names one of this binding's parts. */
export function findingsOfBinding(
  config: Configuration,
  binding: ConfigBinding,
  findings: V2Finding[],
): V2Finding[] {
  const ids = bindingRowIds(config, binding);
  const state = bindingState(binding);
  const mine = new Set<string>([
    ...ids.connections,
    ...ids.wires,
    ...ids.points,
    ...[...ids.nets].filter((net) => !ids.sharedNets.has(net)),
    // A component another binding also fills a slot with is shared: not attributed to either.
    ...Object.values(state.slots)
      .flatMap((slot) => ('component' in slot ? [slot.component] : []))
      .filter(
        (component) =>
          !bindingsOf(config).some(
            (other) =>
              other.id !== binding.id &&
              Object.values(bindingState(other).slots).some(
                (slot) => 'component' in slot && slot.component === component,
              ),
          ),
      ),
  ]);
  return findings.filter((finding) =>
    Object.values(finding.location).some((value) => mine.has(value)),
  );
}

export function BindingOverview({
  value,
  boards,
  boardChoices,
  conflict,
  validation,
  rowIdOf,
}: {
  value: Configuration;
  boards: string[];
  boardChoices: BoardChoices[] | null;
  conflict: ConflictTarget | null;
  validation: OverviewValidation | null;
  rowIdOf: (id: string) => string;
}) {
  const t = useIsolatingTranslations('configuration');
  const bindings = bindingsOf(value);
  if (bindings.length === 0) return null;
  // Boards in the form's order, then any board a binding names that the form no longer has.
  const order = [...boards, ...bindings.map((b) => b.board).filter((b) => !boards.includes(b))];
  const groups = [...new Set(order)]
    .map((board) => ({ board, items: bindings.filter((b) => b.board === board) }))
    .filter((group) => group.items.length > 0);

  function localIssues(binding: ConfigBinding): Issue[] {
    const rowId = rowIdOf(binding.id);
    const out: Issue[] = bindingProblems(value, binding, boardChoices).map((problem, at) => ({
      key: `b${String(at)}`,
      text: t('overview.local', {
        field: t(`overview.field.${problem.field}`),
        key: 'key' in problem ? problem.key : '—',
        problem: t(`bindings.problem.${problem.code}`, {
          key: 'key' in problem ? problem.key : '',
          core: 'core' in problem ? String(problem.core) : '',
        }),
      }),
      target: problemTarget(rowId, problem),
      fallback: rowId,
    }));
    const wires = wireProblems(value, binding);
    wireRows(value, binding).forEach((row, index) => {
      for (const field of WIRE_FIELDS) {
        const problem = wires.values[`${row.key}|${field}`];
        if (problem === undefined) continue;
        out.push({
          key: `w${row.key}|${field}`,
          text: t('overview.wire', {
            wire: `${row.from} – ${row.to}`,
            field: t(`wires.field.${field}`),
            problem: t(`wires.problem.${problem}`),
          }),
          target: `${rowId}-wire-${String(index)}-${field}`,
          fallback: rowId,
        });
      }
    });
    const others = wires.stray.length + wires.extra.length + wires.unreadable.length;
    if (others > 0) {
      out.push({
        key: 'w-other',
        text: t('overview.wireOther', { count: String(others) }),
        target: `${rowId}-wires-title`,
        fallback: rowId,
      });
    }
    return out;
  }

  return (
    <section
      className="flex min-w-0 flex-col gap-3 rounded-md border border-border-subtle p-3"
      aria-labelledby="binding-overview-title"
      data-testid="binding-overview"
    >
      <h4 id="binding-overview-title" className="text-sm font-semibold">
        {t('overview.title')}
      </h4>
      <p className="text-xs text-text-muted" data-testid="binding-overview-note">
        {t('overview.note')}
      </p>
      {validation === null ? (
        <p className="text-xs text-text-muted" data-testid="binding-overview-no-validation">
          {t('overview.noValidation')}
        </p>
      ) : (
        <p
          className="text-xs font-semibold"
          data-testid={
            validation.current ? 'binding-overview-validation' : 'binding-overview-outdated'
          }
        >
          {validation.current
            ? t('overview.validation', { revision: String(validation.revision) })
            : t('overview.validationOutdated', { revision: String(validation.revision) })}
        </p>
      )}
      {groups.map((group) => (
        <div key={group.board} className="flex min-w-0 flex-col gap-2" data-testid="overview-board">
          <h5 className="text-xs font-semibold" dir="auto">
            {t('overview.board', { board: group.board })}
          </h5>
          <ul className="flex min-w-0 flex-col gap-2">
            {group.items.map((binding) => {
              const rowId = rowIdOf(binding.id);
              const local = localIssues(binding);
              const refused =
                conflict !== null && conflict.kind === 'binding' && conflict.entry === binding.id
                  ? conflict
                  : null;
              const found =
                validation === null ? [] : findingsOfBinding(value, binding, validation.findings);
              return (
                <li
                  key={binding.id}
                  className="flex min-w-0 flex-col gap-1 rounded-md border border-border-subtle p-2"
                  data-testid="overview-binding"
                >
                  <p className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
                    <button
                      type="button"
                      className="max-w-full whitespace-normal text-start font-semibold underline"
                      dir="ltr"
                      onClick={() => {
                        focusId(rowId, rowId);
                      }}
                      data-testid="overview-binding-link"
                    >
                      {binding.id}
                    </button>
                    <span dir="auto">{t('overview.pattern', { pattern: binding.pattern })}</span>
                  </p>
                  <IssueList
                    title={t('overview.localTitle')}
                    empty={t('overview.localNone')}
                    issues={local}
                    testId="overview-local"
                  />
                  {refused !== null && (
                    <IssueList
                      title={t('overview.refusedTitle')}
                      empty=""
                      issues={[
                        {
                          key: 'refused',
                          // The per-code sentence, as at the top; the code stays its detail there.
                          text: (() => {
                            const explained = explainRefusal({
                              refusal: refused.reason,
                              binding: binding.id,
                              ref: refused.ref,
                              [refused.refKind || 'ref']: refused.ref,
                            });
                            return t(`explain.${explained.key}`, explained.values);
                          })(),
                          target: rowId,
                          fallback: rowId,
                        },
                      ]}
                      testId="overview-refused"
                    />
                  )}
                  {validation !== null && (
                    <IssueList
                      title={
                        validation.current
                          ? t('overview.foundTitle')
                          : t('overview.foundTitleOutdated')
                      }
                      empty={t('overview.foundNone')}
                      issues={found.map((finding, at) => ({
                        key: `f${String(at)}`,
                        text: t('overview.found', {
                          severity: finding.severity,
                          code: finding.code,
                          message: finding.message,
                        }),
                        target: rowId,
                        fallback: rowId,
                      }))}
                      testId={validation.current ? 'overview-found' : 'overview-found-outdated'}
                    />
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </section>
  );
}

function IssueList({
  title,
  empty,
  issues,
  testId,
}: {
  title: string;
  empty: string;
  issues: Issue[];
  testId: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1" data-testid={testId}>
      <p className="text-xs font-semibold">{title}</p>
      {issues.length === 0 ? (
        empty !== '' && <p className="text-xs text-text-muted">{empty}</p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-1">
          {issues.map((issue) => (
            <li key={issue.key}>
              <button
                type="button"
                className="max-w-full whitespace-normal text-start text-xs underline"
                dir="auto"
                onClick={() => {
                  focusId(issue.target, issue.fallback);
                }}
                data-testid={`${testId}-link`}
              >
                {issue.text}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
