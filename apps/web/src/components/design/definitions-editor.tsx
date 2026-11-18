'use client';

/**
 * M8: the guided editor of the user's own component definitions
 * (docs/architecture/m8-guided-definitions.md), inside the configuration
 * editor. A definition is the composer's template: an id, a function, and the
 * terminals the user types. Nothing is inferred: function and terminal kind
 * stay "unspecified" unless the user picks a value already present in the
 * project's data; requiredness stays "not stated"; a definition entered here
 * is unreviewed and says so. Removing or renaming anything another entry or a
 * feeder link uses names that use first; nothing is dropped silently.
 */
import { useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import { designationText, type Configuration } from '@/components/design/configuration-model';
import {
  addDefinition,
  addDefinitionTerminal,
  definitionIdProblem,
  definitionProblems,
  definitionsOf,
  definitionUsers,
  functionChoices,
  refParts,
  removeDefinition,
  removeDefinitionTerminal,
  renameDefinition,
  terminalKindChoices,
  terminalLinkUses,
  terminalPatternUses,
  UNSPECIFIED,
  updateDefinition,
  updateDefinitionTerminal,
  type Definition,
  type LinkEndUse,
} from '@/components/design/definition-model';
import { TechnicalToken } from '@/components/technical-token';

type Translate = ReturnType<typeof useIsolatingTranslations>;

function requiredValue(value: boolean | null): string {
  return value === null ? 'unstated' : value ? 'yes' : 'no';
}

function requiredOf(value: string): boolean | null {
  return value === 'yes' ? true : value === 'no' ? false : null;
}

function kindLabel(t: Translate, kind: string): string {
  return kind === UNSPECIFIED ? t('definitions.unspecified') : kind.replace(/_/g, ' ');
}

/** Who uses an entry, said by designation (never a raw id unless it has none). */
function userName(
  config: Configuration,
  kind: 'component' | 'strip' | 'pattern',
  id: string,
): string {
  if (kind === 'pattern') return id;
  const section = kind === 'component' ? 'components' : 'strips';
  const list = Array.isArray(config[section]) ? (config[section] as Record<string, unknown>[]) : [];
  const found = list.find((item) => item.id === id);
  const typed = found ? designationText(found.designation) : '';
  return typed !== '' ? typed : id;
}

function DefinitionRow({
  value,
  onChange,
  definition,
  rowId,
  linkUses,
  conflicted,
  onEditUser,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  definition: Definition;
  rowId: string;
  linkUses: LinkEndUse[];
  conflicted: boolean;
  onEditUser: (kind: 'component' | 'strip', id: string) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  const parts = refParts(definition.ref);
  const [renaming, setRenaming] = useState(parts?.id ?? '');
  const [marking, setMarking] = useState('');
  const [kind, setKind] = useState(UNSPECIFIED);
  const [required, setRequired] = useState('unstated');
  // Announced politely after a terminal is added or removed (WCAG 4.1.3).
  const [status, setStatus] = useState('');
  const newMarkingRef = useRef<HTMLInputElement>(null);
  const users = definitionUsers(value, definition.ref);
  const problems = definitionProblems(definition);
  const renameProblem =
    renaming.trim() === parts?.id ? null : definitionIdProblem(value, renaming, definition.ref);
  const markingTaken = definition.terminals.some((terminal) => terminal.marking === marking.trim());
  const newMarkingProblem = marking.trim() === '' ? null : markingTaken ? 'duplicate' : null;
  const kinds = terminalKindChoices(value);
  const functions = functionChoices(value);

  function addTerminal() {
    if (marking.trim() === '' || newMarkingProblem !== null) return;
    onChange(
      addDefinitionTerminal(value, definition.ref, {
        marking: marking.trim(),
        kind,
        required: requiredOf(required),
      }),
    );
    setStatus(t('definitions.terminalAdded', { marking: marking.trim() }));
    setMarking('');
    setKind(UNSPECIFIED);
    setRequired('unstated');
  }

  return (
    <li
      id={rowId}
      tabIndex={-1}
      className={`flex min-w-0 flex-col gap-3 rounded-md border p-3 ${
        conflicted ? 'border-status-critical' : 'border-border-subtle'
      }`}
      data-testid="definition-entry"
      data-conflict={conflicted || undefined}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <span className="break-words font-semibold" dir="ltr" data-testid="definition-id">
          {parts?.id ?? definition.ref}
        </span>
        <TechnicalToken className="break-all text-xs">{definition.ref}</TechnicalToken>
        <span
          className="rounded-sm border border-status-warning px-1 text-xs font-semibold"
          data-testid="definition-unreviewed"
        >
          {definition.review.status === 'unreviewed'
            ? t('definitions.unreviewed')
            : t('definitions.reviewStatus', { status: definition.review.status })}
        </span>
      </div>
      <p className="text-xs text-text-muted">{t('definitions.enteredNote')}</p>

      <div className="flex min-w-0 flex-wrap items-end gap-2">
        <div className="field min-w-0 flex-1">
          <label htmlFor={`${rowId}-rename`} className="field-label">
            {t('definitions.id')}
          </label>
          <input
            id={`${rowId}-rename`}
            className="input w-full font-mono"
            dir="ltr"
            maxLength={100}
            value={renaming}
            aria-invalid={renameProblem !== null || undefined}
            aria-describedby={`${rowId}-rename-help`}
            onChange={(event) => {
              setRenaming(event.target.value);
            }}
            data-testid="definition-rename"
          />
          <p id={`${rowId}-rename-help`} className="text-xs text-text-muted">
            {renameProblem !== null ? (
              <span className="font-semibold" data-testid="definition-rename-problem">
                {t(`definitions.idProblem.${renameProblem}`)}
              </span>
            ) : (
              t('definitions.idHelp')
            )}
          </p>
        </div>
        <button
          type="button"
          className="btn btn-sm btn-secondary"
          disabled={renameProblem !== null || renaming.trim() === parts?.id}
          onClick={() => {
            onChange(renameDefinition(value, definition.ref, renaming));
          }}
          data-testid="definition-rename-apply"
        >
          {users.length > 0
            ? t('definitions.renameWithUsers', { count: String(users.length) })
            : t('definitions.rename')}
        </button>
      </div>
      {users.length > 0 && renaming.trim() !== parts?.id && renameProblem === null && (
        <p className="text-xs" data-testid="definition-rename-users">
          {t('definitions.renameUsers', {
            entries: users.map((user) => userName(value, user.kind, user.id)).join(', '),
          })}
        </p>
      )}

      <div className="field min-w-0">
        <label htmlFor={`${rowId}-function`} className="field-label">
          {t('definitions.function')}
        </label>
        <select
          id={`${rowId}-function`}
          className="input w-full"
          value={definition.function}
          onChange={(event) => {
            onChange(updateDefinition(value, definition.ref, { function: event.target.value }));
          }}
          data-testid="definition-function"
        >
          {functions.map((fn) => (
            <option key={fn} value={fn}>
              {kindLabel(t, fn)}
            </option>
          ))}
        </select>
      </div>

      <div className="field min-w-0">
        <label htmlFor={`${rowId}-source`} className="field-label">
          {t('definitions.source')}
        </label>
        <input
          id={`${rowId}-source`}
          className="input w-full"
          dir="auto"
          maxLength={2000}
          value={definition.provenance.source ?? ''}
          aria-describedby={`${rowId}-source-help`}
          onChange={(event) => {
            onChange(updateDefinition(value, definition.ref, { source: event.target.value }));
          }}
          data-testid="definition-source"
        />
        <p id={`${rowId}-source-help`} className="text-xs text-text-muted">
          {t('definitions.sourceHelp')}
        </p>
      </div>

      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="definition-terminals">
        <legend className="text-sm font-semibold">{t('definitions.terminals')}</legend>
        {definition.terminals.length === 0 && (
          <p className="text-xs text-text-muted" data-testid="definition-no-terminals">
            {t('definitions.noTerminals')}
          </p>
        )}
        {definition.terminals.length > 0 && (
          <ul className="flex flex-col gap-3">
            {definition.terminals.map((terminal, index) => {
              const problem = problems.terminals[terminal.id];
              const linked = terminalLinkUses(value, definition.ref, terminal.id, linkUses);
              const patterned = terminalPatternUses(value, definition.ref, terminal.id);
              const base = `${rowId}-t-${terminal.id}`;
              return (
                <li
                  key={terminal.id}
                  className="flex min-w-0 flex-col gap-2 border-t border-border-subtle pt-2"
                  data-testid="definition-terminal"
                >
                  <div className="flex min-w-0 flex-wrap items-end gap-2">
                    <div className="field min-w-0 flex-1">
                      <label htmlFor={`${base}-marking`} className="field-label">
                        {t('definitions.terminalN', { n: String(index + 1) })}
                      </label>
                      <input
                        id={`${base}-marking`}
                        className="input w-full"
                        dir="auto"
                        maxLength={50}
                        value={terminal.marking}
                        aria-invalid={problem !== undefined || undefined}
                        aria-describedby={problem ? `${base}-problem` : undefined}
                        onChange={(event) => {
                          onChange(
                            updateDefinitionTerminal(value, definition.ref, terminal.id, {
                              marking: event.target.value,
                            }),
                          );
                        }}
                        data-testid="definition-terminal-marking"
                      />
                    </div>
                    <div className="field min-w-0 sm:w-40">
                      <label htmlFor={`${base}-kind`} className="field-label">
                        {t('definitions.kind')}
                      </label>
                      <select
                        id={`${base}-kind`}
                        className="input w-full"
                        value={terminal.kind}
                        onChange={(event) => {
                          onChange(
                            updateDefinitionTerminal(value, definition.ref, terminal.id, {
                              kind: event.target.value,
                            }),
                          );
                        }}
                        data-testid="definition-terminal-kind"
                      >
                        {(kinds.includes(terminal.kind) ? kinds : [...kinds, terminal.kind]).map(
                          (option) => (
                            <option key={option} value={option}>
                              {kindLabel(t, option)}
                            </option>
                          ),
                        )}
                      </select>
                    </div>
                    <div className="field min-w-0 sm:w-40">
                      <label htmlFor={`${base}-required`} className="field-label">
                        {t('definitions.required')}
                      </label>
                      <select
                        id={`${base}-required`}
                        className="input w-full"
                        value={requiredValue(terminal.required)}
                        onChange={(event) => {
                          onChange(
                            updateDefinitionTerminal(value, definition.ref, terminal.id, {
                              required: requiredOf(event.target.value),
                            }),
                          );
                        }}
                        data-testid="definition-terminal-required"
                      >
                        <option value="unstated">{t('definitions.requiredUnstated')}</option>
                        <option value="yes">{t('definitions.requiredYes')}</option>
                        <option value="no">{t('definitions.requiredNo')}</option>
                      </select>
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-danger"
                      disabled={linked.length > 0 || patterned.length > 0}
                      aria-describedby={
                        linked.length > 0 || patterned.length > 0 ? `${base}-linked` : undefined
                      }
                      aria-label={t('definitions.removeTerminal', {
                        marking: terminal.marking || String(index + 1),
                      })}
                      onClick={() => {
                        onChange(removeDefinitionTerminal(value, definition.ref, terminal.id));
                        setStatus(
                          t('definitions.terminalRemoved', {
                            marking: terminal.marking || String(index + 1),
                          }),
                        );
                        // The pressed button unmounts; keep focus in this definition.
                        newMarkingRef.current?.focus();
                      }}
                      data-testid="definition-terminal-remove"
                    >
                      {t('editor.remove')}
                    </button>
                  </div>
                  {problem !== undefined && (
                    <p
                      id={`${base}-problem`}
                      className="text-xs font-semibold"
                      data-testid="definition-terminal-problem"
                    >
                      {t(`definitions.markingProblem.${problem}`)}
                    </p>
                  )}
                  {patterned.length > 0 && (
                    <p
                      id={linked.length > 0 ? undefined : `${base}-linked`}
                      className="text-xs"
                      data-testid="definition-terminal-patterned"
                    >
                      {t('definitions.terminalPatterned', { patterns: patterned.join(', ') })}
                    </p>
                  )}
                  {linked.length > 0 && (
                    <p
                      id={`${base}-linked`}
                      className="text-xs"
                      data-testid="definition-terminal-linked"
                    >
                      {t('definitions.terminalLinked', {
                        links: linked
                          .map((use) =>
                            t(`definitions.linkEnd.${use.role}`, { board: use.fedBoard }),
                          )
                          .join(', '),
                      })}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        <div className="flex min-w-0 flex-wrap items-end gap-2">
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-marking`} className="field-label">
              {t('definitions.newTerminal')}
            </label>
            <input
              ref={newMarkingRef}
              id={`${rowId}-new-marking`}
              className="input w-full"
              dir="auto"
              maxLength={50}
              value={marking}
              aria-invalid={newMarkingProblem !== null || undefined}
              aria-describedby={newMarkingProblem ? `${rowId}-new-marking-problem` : undefined}
              onChange={(event) => {
                setMarking(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  addTerminal();
                }
              }}
              data-testid="definition-new-terminal"
            />
          </div>
          <div className="field min-w-0 sm:w-40">
            <label htmlFor={`${rowId}-new-kind`} className="field-label">
              {t('definitions.kind')}
            </label>
            <select
              id={`${rowId}-new-kind`}
              className="input w-full"
              value={kind}
              onChange={(event) => {
                setKind(event.target.value);
              }}
              data-testid="definition-new-terminal-kind"
            >
              {kinds.map((option) => (
                <option key={option} value={option}>
                  {kindLabel(t, option)}
                </option>
              ))}
            </select>
          </div>
          <div className="field min-w-0 sm:w-40">
            <label htmlFor={`${rowId}-new-required`} className="field-label">
              {t('definitions.required')}
            </label>
            <select
              id={`${rowId}-new-required`}
              className="input w-full"
              value={required}
              onChange={(event) => {
                setRequired(event.target.value);
              }}
              data-testid="definition-new-terminal-required"
            >
              <option value="unstated">{t('definitions.requiredUnstated')}</option>
              <option value="yes">{t('definitions.requiredYes')}</option>
              <option value="no">{t('definitions.requiredNo')}</option>
            </select>
          </div>
          <button
            type="button"
            className="btn btn-sm btn-secondary"
            disabled={marking.trim() === '' || newMarkingProblem !== null}
            onClick={addTerminal}
            data-testid="definition-add-terminal"
          >
            {t('definitions.addTerminal')}
          </button>
        </div>
        <p className="sr-only" role="status" aria-live="polite" data-testid="definition-status">
          {status}
        </p>
        {newMarkingProblem !== null && (
          <p
            id={`${rowId}-new-marking-problem`}
            className="text-xs font-semibold"
            data-testid="definition-new-terminal-problem"
          >
            {t(`definitions.markingProblem.${newMarkingProblem}`)}
          </p>
        )}
        <p className="text-xs text-text-muted">{t('definitions.requiredHelp')}</p>
      </fieldset>

      <div className="flex min-w-0 flex-col gap-1">
        <button
          type="button"
          className="btn btn-sm btn-danger self-start"
          disabled={users.length > 0}
          aria-describedby={users.length > 0 ? `${rowId}-users` : undefined}
          onClick={() => {
            onChange(removeDefinition(value, definition.ref));
          }}
          data-testid="definition-remove"
        >
          {t('definitions.remove')}
        </button>
        {users.length > 0 && (
          <div
            id={`${rowId}-users`}
            className="flex flex-col gap-1 text-xs"
            data-testid="definition-users"
          >
            <span>{t('definitions.usedBy', { count: String(users.length) })}</span>
            <span className="flex flex-wrap gap-2">
              {users.map((user) =>
                user.kind === 'pattern' ? (
                  <span
                    key={`${user.kind}|${user.id}`}
                    className="break-all font-mono"
                    dir="ltr"
                    data-testid="definition-user-pattern"
                  >
                    {t('definitions.patternUser', { ref: user.id })}
                  </span>
                ) : (
                  <button
                    key={`${user.kind}|${user.id}`}
                    type="button"
                    className="btn btn-sm btn-secondary"
                    onClick={() => {
                      if (user.kind !== 'pattern') onEditUser(user.kind, user.id);
                    }}
                    data-testid="definition-user-edit"
                  >
                    {t(`editor.entryKind.${user.kind}`, {
                      id: userName(value, user.kind, user.id),
                    })}
                  </button>
                ),
              )}
            </span>
          </div>
        )}
      </div>
    </li>
  );
}

export function DefinitionsEditor({
  value,
  onChange,
  idBase,
  linkUses,
  conflictRef,
  rowIdOf,
  onEditUser,
  headerTools = null,
}: {
  /** Shown right after the heading (the way to the add form). */
  headerTools?: React.ReactNode;
  value: Configuration;
  onChange: (next: Configuration) => void;
  idBase: string;
  linkUses: LinkEndUse[];
  /** The ref a save's refusal named, whose row is marked. */
  conflictRef: string | null;
  rowIdOf: (ref: string) => string;
  onEditUser: (kind: 'component' | 'strip', id: string) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const [newId, setNewId] = useState('');
  const [newFunction, setNewFunction] = useState(UNSPECIFIED);
  const definitions = definitionsOf(value);
  const problem = newId === '' ? null : definitionIdProblem(value, newId);

  function add() {
    if (newId.trim() === '' || problem !== null) return;
    onChange(addDefinition(value, { id: newId, function: newFunction }));
    setNewId('');
    setNewFunction(UNSPECIFIED);
  }

  return (
    <section className="flex min-w-0 flex-col gap-3" aria-labelledby={`${idBase}-definitions`}>
      <h3 id={`${idBase}-definitions`} tabIndex={-1} className="text-base font-semibold">
        {t('definitions.title')}
      </h3>
      {headerTools}
      <p className="text-sm text-text-muted">{t('definitions.intro')}</p>
      {definitions.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="definitions-none">
          {t('definitions.none')}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {definitions.map((definition) => (
            <DefinitionRow
              key={definition.ref}
              value={value}
              onChange={onChange}
              definition={definition}
              rowId={rowIdOf(definition.ref)}
              linkUses={linkUses}
              conflicted={conflictRef === definition.ref}
              onEditUser={onEditUser}
            />
          ))}
        </ul>
      )}
      <fieldset
        id={`${idBase}-add-definition`}
        className="flex min-w-0 flex-col gap-3 rounded-md border border-border-subtle p-3"
        data-testid="add-definition"
      >
        <legend className="px-1 text-sm font-semibold">{t('definitions.add')}</legend>
        <div className="field min-w-0">
          <label htmlFor={`${id}-id`} className="field-label">
            {t('definitions.id')}
          </label>
          <input
            id={`${id}-id`}
            className="input w-full font-mono"
            dir="ltr"
            maxLength={100}
            value={newId}
            aria-invalid={problem !== null || undefined}
            aria-describedby={`${id}-id-help`}
            onChange={(event) => {
              setNewId(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                add();
              }
            }}
            data-testid="add-definition-id"
          />
          <p id={`${id}-id-help`} className="text-xs text-text-muted">
            {problem !== null ? (
              <span className="font-semibold" data-testid="add-definition-problem">
                {t(`definitions.idProblem.${problem}`)}
              </span>
            ) : (
              t('definitions.idHelp')
            )}
          </p>
        </div>
        <div className="field min-w-0">
          <label htmlFor={`${id}-function`} className="field-label">
            {t('definitions.function')}
          </label>
          <select
            id={`${id}-function`}
            className="input w-full"
            value={newFunction}
            onChange={(event) => {
              setNewFunction(event.target.value);
            }}
            data-testid="add-definition-function"
          >
            {functionChoices(value).map((fn) => (
              <option key={fn} value={fn}>
                {kindLabel(t, fn)}
              </option>
            ))}
          </select>
        </div>
        <button
          type="button"
          className="btn btn-sm btn-secondary self-start"
          disabled={newId.trim() === '' || problem !== null}
          onClick={add}
          data-testid="add-definition-submit"
        >
          {t('definitions.addAction')}
        </button>
      </fieldset>
    </section>
  );
}
