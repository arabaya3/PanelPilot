'use client';

/**
 * M9a: the guided editor of the user's own connection patterns
 * (docs/architecture/m9a-guided-patterns.md), inside the configuration
 * editor. A pattern is the composer's: slots, strip roles, points and nets
 * whose members are a slot's terminal or a point's side. Nothing is inferred;
 * a pattern entered here is unreviewed and says so. Removing or renaming
 * anything something else names says what names it first; nothing is dropped
 * silently. Problems the composer would refuse are said beside the field.
 */
import { useEffect, useId, useRef, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import type { Configuration } from '@/components/design/configuration-model';
import { refParts, UNSPECIFIED } from '@/components/design/definition-model';
import {
  addMember,
  addNet,
  addPattern,
  addPoint,
  addRole,
  addSlot,
  isSlotMember,
  memberConnections,
  NET_SCOPES,
  partIdProblem,
  patternIdProblem,
  patternProblems,
  patternsOf,
  patternUsers,
  pointKindChoices,
  pointUses,
  positionProblem,
  problemCount,
  addConnection,
  connectionProblem,
  removeConnection,
  removeMember,
  removeNet,
  removePattern,
  removePoint,
  removeRole,
  removeSlot,
  renamePattern,
  roleUses,
  SIDES,
  slotUses,
  templateTerminals,
  updateConnection,
  updateNet,
  updatePatternSource,
  VIAS,
  wireDetailUses,
  type ConnectionDraft,
  type ConnectionProblem,
  type Via,
  updatePoint,
  updateSlotTemplate,
  type NetScope,
  type Pattern,
  type PatternMember,
  type Side,
} from '@/components/design/pattern-model';
import { TechnicalToken } from '@/components/technical-token';

type Translate = ReturnType<typeof useIsolatingTranslations>;

function kindLabel(t: Translate, kind: string): string {
  return kind === UNSPECIFIED ? t('definitions.unspecified') : kind.replace(/_/g, ' ');
}

function Problem({ id, text, testId }: { id: string; text: string; testId: string }) {
  return (
    <p id={id} className="text-xs font-semibold" data-testid={testId}>
      {text}
    </p>
  );
}

/** Focus an element after React has rendered the change (the pressed button may be gone). */
function focusLater(id: string) {
  window.setTimeout(() => {
    document.getElementById(id)?.focus();
  }, 0);
}

function PatternRow({
  value,
  onChange,
  pattern,
  rowId,
  templates,
  conflicted,
  announce,
  addFieldId,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  pattern: Pattern;
  rowId: string;
  templates: readonly string[];
  conflicted: boolean;
  announce: (message: string) => void;
  /** Where focus goes when this pattern is removed: the add-pattern field. */
  addFieldId: string;
}) {
  const t = useIsolatingTranslations('configuration');
  const ref = pattern.ref;
  const id = refParts(ref)?.id ?? ref;
  const users = patternUsers(value, ref);
  const known = new Set(templates);
  const problems = patternProblems(value, pattern, known);
  const count = problemCount(problems);
  const kinds = pointKindChoices(value);
  const [renaming, setRenaming] = useState(id);
  const renameProblem = renaming.trim() === id ? null : patternIdProblem(value, renaming, ref);
  const [slotId, setSlotId] = useState('');
  const [slotTemplate, setSlotTemplate] = useState('');
  const [role, setRole] = useState('');
  const [point, setPoint] = useState({
    id: '',
    strip: '',
    offset: '1',
    level: '1',
    kind: UNSPECIFIED,
  });
  const [net, setNet] = useState({ role: '', signal: '', scope: 'binding' as NetScope });
  const slotIdProblem =
    slotId === ''
      ? null
      : partIdProblem(
          pattern.slots.map((slot) => slot.id),
          slotId,
        );
  const roleProblem = role === '' ? null : partIdProblem(pattern.strips, role);
  const pointIdProblem =
    point.id === ''
      ? null
      : partIdProblem(
          pattern.points.map((p) => p.id),
          point.id,
        );
  const pointStrip = point.strip !== '' ? point.strip : (pattern.strips[0] ?? '');
  const netRoleProblem =
    net.role === ''
      ? null
      : partIdProblem(
          pattern.nets.map((n) => n.role),
          net.role,
        );
  const name = (key: string, part: string) => t(`patterns.${key}`, { id: part });
  const usesText = (uses: { kind: string; ids: string[] }[]) =>
    uses
      .filter((use) => use.ids.length > 0)
      .map((use) => `${use.kind}: ${use.ids.join(', ')}`)
      .join('; ');

  return (
    <li
      id={rowId}
      tabIndex={-1}
      className={`flex min-w-0 flex-col gap-4 rounded-md border p-3 ${
        conflicted ? 'border-status-critical' : 'border-border-subtle'
      }`}
      data-testid="pattern-entry"
      data-conflict={conflicted || undefined}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <span className="break-words font-semibold" dir="ltr" data-testid="pattern-id">
          {id}
        </span>
        <TechnicalToken className="break-all text-xs">{ref}</TechnicalToken>
        <span
          className="rounded-sm border border-status-warning px-1 text-xs font-semibold"
          data-testid="pattern-unreviewed"
        >
          {pattern.reviewStatus === 'unreviewed'
            ? t('patterns.unreviewed')
            : t('patterns.reviewStatus', { status: pattern.reviewStatus })}
        </span>
      </div>
      {count > 0 && (
        <p className="text-xs font-semibold" data-testid="pattern-problems">
          {t('patterns.problems', { count: String(count) })}
        </p>
      )}
      {users.length > 0 && (
        <p className="text-xs" id={`${rowId}-users`} data-testid="pattern-users">
          {t('patterns.bindingsAffected', {
            count: String(users.length),
            bindings: users.join(', '),
          })}
        </p>
      )}

      <div className="flex min-w-0 flex-wrap items-end gap-2">
        <div className="field min-w-0 flex-1">
          <label htmlFor={`${rowId}-source`} className="field-label">
            {t('patterns.source')}
          </label>
          <input
            id={`${rowId}-source`}
            className="input w-full"
            dir="auto"
            maxLength={200}
            value={pattern.source ?? ''}
            onChange={(event) => {
              onChange(updatePatternSource(value, ref, event.target.value));
            }}
            data-testid="pattern-source"
          />
        </div>
        <div className="field min-w-0 flex-1">
          <label htmlFor={`${rowId}-rename`} className="field-label">
            {t('patterns.id')}
          </label>
          <input
            id={`${rowId}-rename`}
            className="input w-full"
            dir="ltr"
            maxLength={100}
            value={renaming}
            aria-invalid={renameProblem !== null || undefined}
            aria-describedby={renameProblem ? `${rowId}-rename-problem` : undefined}
            onChange={(event) => {
              setRenaming(event.target.value);
            }}
            data-testid="pattern-rename"
          />
        </div>
        <button
          type="button"
          className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
          disabled={renaming.trim() === id || renameProblem !== null}
          onClick={() => {
            onChange(renamePattern(value, ref, renaming));
          }}
          data-testid="pattern-rename-apply"
        >
          {users.length > 0
            ? t('patterns.renameWithUsers', { count: String(users.length) })
            : t('patterns.rename')}
        </button>
      </div>
      {renameProblem !== null && (
        <Problem
          id={`${rowId}-rename-problem`}
          text={t(`patterns.idProblem.${renameProblem}`)}
          testId="pattern-rename-problem"
        />
      )}
      {renameProblem === null && renaming.trim() !== id && users.length > 0 && (
        <p className="text-xs" data-testid="pattern-rename-users">
          {t('patterns.renameUsers', { bindings: users.join(', ') })}
        </p>
      )}

      {/* ------------------------------------------------------------ slots */}
      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="pattern-slots">
        <legend className="text-sm font-semibold">{t('patterns.slots')}</legend>
        <p className="text-xs text-text-muted">{t('patterns.slotsHelp')}</p>
        {pattern.slots.length === 0 && (
          <p className="text-xs text-text-muted">{t('patterns.noSlots')}</p>
        )}
        <ul className="flex min-w-0 flex-col gap-2">
          {pattern.slots.map((slot) => {
            const uses = slotUses(value, ref, slot.id);
            const used = uses.nets.length + uses.bindings.length > 0;
            const base = `${rowId}-slot-${slot.id}`;
            const problem = problems.slots[slot.id];
            return (
              <li key={slot.id} className="flex min-w-0 flex-col gap-1" data-testid="pattern-slot">
                <div className="flex min-w-0 flex-wrap items-end gap-2">
                  <span className="font-semibold" dir="ltr" data-testid="pattern-slot-id">
                    {slot.id}
                  </span>
                  <div className="field min-w-0 flex-1">
                    <label htmlFor={`${base}-template`} className="field-label">
                      {t('patterns.slotTemplate', { slot: slot.id })}
                    </label>
                    <select
                      id={`${base}-template`}
                      className="input w-full"
                      dir="ltr"
                      value={slot.template ?? ''}
                      aria-invalid={problem !== undefined || undefined}
                      aria-describedby={problem ? `${base}-problem` : undefined}
                      onChange={(event) => {
                        onChange(
                          updateSlotTemplate(value, ref, slot.id, event.target.value || null),
                        );
                      }}
                      data-testid="pattern-slot-template"
                    >
                      <option value="">{t('patterns.noTemplate')}</option>
                      {slot.template !== null && !known.has(slot.template) && (
                        <option value={slot.template}>{slot.template}</option>
                      )}
                      {templates.map((choice) => (
                        <option key={choice} value={choice}>
                          {choice}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger max-w-full whitespace-normal text-start"
                    disabled={used}
                    aria-describedby={used ? `${base}-used` : undefined}
                    aria-label={t('patterns.removeSlot', { slot: slot.id })}
                    onClick={() => {
                      onChange(removeSlot(value, ref, slot.id));
                      announce(t('patterns.removed', { what: name('kindSlot', slot.id) }));
                      focusLater(`${rowId}-new-slot`);
                    }}
                    data-testid="pattern-slot-remove"
                  >
                    {t('editor.remove')}
                  </button>
                </div>
                {slot.deviceKind !== null && (
                  <p className="text-xs text-text-muted">
                    {t('patterns.deviceKindKept', { kind: slot.deviceKind })}
                  </p>
                )}
                {problem !== undefined && (
                  <Problem
                    id={`${base}-problem`}
                    text={t('patterns.templateUnknown')}
                    testId="pattern-slot-problem"
                  />
                )}
                {used && (
                  <p id={`${base}-used`} className="text-xs" data-testid="pattern-slot-used">
                    {t('patterns.slotUsed', {
                      uses: usesText([
                        { kind: t('patterns.nets'), ids: uses.nets },
                        { kind: t('editor.bindings'), ids: uses.bindings },
                      ]),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
        <div className="flex min-w-0 flex-wrap items-end gap-2">
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-slot`} className="field-label">
              {t('patterns.slotId')}
            </label>
            <input
              id={`${rowId}-new-slot`}
              className="input w-full"
              dir="ltr"
              maxLength={50}
              value={slotId}
              aria-invalid={slotIdProblem !== null || undefined}
              aria-describedby={slotIdProblem ? `${rowId}-new-slot-problem` : undefined}
              onChange={(event) => {
                setSlotId(event.target.value);
              }}
              data-testid="pattern-new-slot"
            />
          </div>
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-slot-template`} className="field-label">
              {t('patterns.newSlotTemplate')}
            </label>
            <select
              id={`${rowId}-new-slot-template`}
              className="input w-full"
              dir="ltr"
              value={slotTemplate}
              onChange={(event) => {
                setSlotTemplate(event.target.value);
              }}
              data-testid="pattern-new-slot-template"
            >
              <option value="">{t('patterns.noTemplate')}</option>
              {templates.map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
            disabled={slotId.trim() === '' || slotIdProblem !== null}
            onClick={() => {
              onChange(addSlot(value, ref, { id: slotId, template: slotTemplate || null }));
              announce(t('patterns.added', { what: name('kindSlot', slotId.trim()) }));
              setSlotId('');
              focusLater(`${rowId}-new-slot`);
            }}
            data-testid="pattern-add-slot"
          >
            {t('patterns.addSlot')}
          </button>
        </div>
        {slotIdProblem !== null && (
          <Problem
            id={`${rowId}-new-slot-problem`}
            text={t(`patterns.partProblem.${slotIdProblem}`)}
            testId="pattern-new-slot-problem"
          />
        )}
      </fieldset>

      {/* ------------------------------------------------------ strip roles */}
      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="pattern-roles">
        <legend className="text-sm font-semibold">{t('patterns.roles')}</legend>
        <p className="text-xs text-text-muted">{t('patterns.rolesHelp')}</p>
        {pattern.strips.length === 0 && (
          <p className="text-xs text-text-muted">{t('patterns.noRoles')}</p>
        )}
        <ul className="flex min-w-0 flex-col gap-2">
          {pattern.strips.map((item) => {
            const uses = roleUses(value, ref, item);
            const used = uses.points.length + uses.bindings.length > 0;
            const base = `${rowId}-role-${item}`;
            return (
              <li key={item} className="flex min-w-0 flex-col gap-1" data-testid="pattern-role">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <span className="font-semibold" dir="ltr" data-testid="pattern-role-name">
                    {item}
                  </span>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger max-w-full whitespace-normal text-start"
                    disabled={used}
                    aria-describedby={used ? `${base}-used` : undefined}
                    aria-label={t('patterns.removeRole', { role: item })}
                    onClick={() => {
                      onChange(removeRole(value, ref, item));
                      announce(t('patterns.removed', { what: name('kindRole', item) }));
                      focusLater(`${rowId}-new-role`);
                    }}
                    data-testid="pattern-role-remove"
                  >
                    {t('editor.remove')}
                  </button>
                </div>
                {used && (
                  <p id={`${base}-used`} className="text-xs" data-testid="pattern-role-used">
                    {t('patterns.roleUsed', {
                      uses: usesText([
                        { kind: t('patterns.points'), ids: uses.points },
                        { kind: t('editor.bindings'), ids: uses.bindings },
                      ]),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
        <div className="flex min-w-0 flex-wrap items-end gap-2">
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-role`} className="field-label">
              {t('patterns.roleName')}
            </label>
            <input
              id={`${rowId}-new-role`}
              className="input w-full"
              dir="ltr"
              maxLength={50}
              value={role}
              aria-invalid={roleProblem !== null || undefined}
              aria-describedby={roleProblem ? `${rowId}-new-role-problem` : undefined}
              onChange={(event) => {
                setRole(event.target.value);
              }}
              data-testid="pattern-new-role"
            />
          </div>
          <button
            type="button"
            className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
            disabled={role.trim() === '' || roleProblem !== null}
            onClick={() => {
              onChange(addRole(value, ref, role));
              announce(t('patterns.added', { what: name('kindRole', role.trim()) }));
              setRole('');
              focusLater(`${rowId}-new-role`);
            }}
            data-testid="pattern-add-role"
          >
            {t('patterns.addRole')}
          </button>
        </div>
        {roleProblem !== null && (
          <Problem
            id={`${rowId}-new-role-problem`}
            text={t(`patterns.partProblem.${roleProblem}`)}
            testId="pattern-new-role-problem"
          />
        )}
      </fieldset>

      {/* ----------------------------------------------------------- points */}
      <fieldset className="flex min-w-0 flex-col gap-2" data-testid="pattern-points">
        <legend className="text-sm font-semibold">{t('patterns.points')}</legend>
        <p className="text-xs text-text-muted">{t('patterns.pointsHelp')}</p>
        {pattern.points.length === 0 && (
          <p className="text-xs text-text-muted">{t('patterns.noPoints')}</p>
        )}
        <ul className="flex min-w-0 flex-col gap-3">
          {pattern.points.map((item) => {
            const uses = pointUses(value, ref, item.id);
            const used = uses.nets.length + uses.bindings.length > 0;
            const base = `${rowId}-point-${item.id}`;
            const problem = problems.points[item.id];
            return (
              <li key={item.id} className="flex min-w-0 flex-col gap-1" data-testid="pattern-point">
                <div className="flex min-w-0 flex-wrap items-end gap-2">
                  <span className="font-semibold" dir="ltr" data-testid="pattern-point-id">
                    {item.id}
                  </span>
                  <div className="field min-w-0 sm:w-40">
                    <label htmlFor={`${base}-role`} className="field-label">
                      {t('patterns.pointRoleOf', { point: item.id })}
                    </label>
                    <select
                      id={`${base}-role`}
                      className="input w-full"
                      dir="ltr"
                      value={item.strip}
                      aria-invalid={problem !== undefined || undefined}
                      aria-describedby={problem ? `${base}-problem` : undefined}
                      onChange={(event) => {
                        onChange(updatePoint(value, ref, item.id, { strip: event.target.value }));
                      }}
                      data-testid="pattern-point-role"
                    >
                      {!pattern.strips.includes(item.strip) && (
                        <option value={item.strip}>{item.strip || '—'}</option>
                      )}
                      {pattern.strips.map((choice) => (
                        <option key={choice} value={choice}>
                          {choice}
                        </option>
                      ))}
                    </select>
                  </div>
                  <PositionField
                    id={`${base}-offset`}
                    label={t('patterns.offsetOf', { point: item.id })}
                    value={item.offset}
                    problemText={t('patterns.positionProblem')}
                    testId="pattern-point-offset"
                    onValid={(next) => {
                      onChange(updatePoint(value, ref, item.id, { offset: next }));
                    }}
                  />
                  <PositionField
                    id={`${base}-level`}
                    label={t('patterns.levelOf', { point: item.id })}
                    value={item.level}
                    problemText={t('patterns.positionProblem')}
                    testId="pattern-point-level"
                    onValid={(next) => {
                      onChange(updatePoint(value, ref, item.id, { level: next }));
                    }}
                  />
                  <div className="field min-w-0 sm:w-40">
                    <label htmlFor={`${base}-kind`} className="field-label">
                      {t('patterns.kindOf', { point: item.id })}
                    </label>
                    <select
                      id={`${base}-kind`}
                      className="input w-full"
                      value={item.kind}
                      onChange={(event) => {
                        onChange(updatePoint(value, ref, item.id, { kind: event.target.value }));
                      }}
                      data-testid="pattern-point-kind"
                    >
                      {(kinds.includes(item.kind) ? kinds : [...kinds, item.kind]).map((choice) => (
                        <option key={choice} value={choice}>
                          {kindLabel(t, choice)}
                        </option>
                      ))}
                    </select>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm btn-danger max-w-full whitespace-normal text-start"
                    disabled={used}
                    aria-describedby={used ? `${base}-used` : undefined}
                    aria-label={t('patterns.removePoint', { point: item.id })}
                    onClick={() => {
                      onChange(removePoint(value, ref, item.id));
                      announce(t('patterns.removed', { what: name('kindPoint', item.id) }));
                      focusLater(`${rowId}-new-point`);
                    }}
                    data-testid="pattern-point-remove"
                  >
                    {t('editor.remove')}
                  </button>
                </div>
                {problem !== undefined && (
                  <Problem
                    id={`${base}-problem`}
                    text={t('patterns.noRole')}
                    testId="pattern-point-problem"
                  />
                )}
                {used && (
                  <p id={`${base}-used`} className="text-xs" data-testid="pattern-point-used">
                    {t('patterns.pointUsed', {
                      uses: usesText([
                        { kind: t('patterns.nets'), ids: uses.nets },
                        { kind: t('editor.bindings'), ids: uses.bindings },
                      ]),
                    })}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
        {pattern.strips.length === 0 ? (
          <p className="text-xs text-text-muted" data-testid="pattern-point-need-role">
            {t('patterns.needRole')}
          </p>
        ) : (
          <>
            <div className="flex min-w-0 flex-wrap items-end gap-2">
              <div className="field min-w-0 flex-1">
                <label htmlFor={`${rowId}-new-point`} className="field-label">
                  {t('patterns.pointId')}
                </label>
                <input
                  id={`${rowId}-new-point`}
                  className="input w-full"
                  dir="ltr"
                  maxLength={50}
                  value={point.id}
                  aria-invalid={pointIdProblem !== null || undefined}
                  aria-describedby={pointIdProblem ? `${rowId}-new-point-problem` : undefined}
                  onChange={(event) => {
                    setPoint({ ...point, id: event.target.value });
                  }}
                  data-testid="pattern-new-point"
                />
              </div>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${rowId}-new-point-role`} className="field-label">
                  {t('patterns.pointRole')}
                </label>
                <select
                  id={`${rowId}-new-point-role`}
                  className="input w-full"
                  dir="ltr"
                  value={pointStrip}
                  onChange={(event) => {
                    setPoint({ ...point, strip: event.target.value });
                  }}
                  data-testid="pattern-new-point-role"
                >
                  {pattern.strips.map((choice) => (
                    <option key={choice} value={choice}>
                      {choice}
                    </option>
                  ))}
                </select>
              </div>
              {(['offset', 'level'] as const).map((key) => (
                <div key={key} className="field w-24">
                  <label htmlFor={`${rowId}-new-point-${key}`} className="field-label">
                    {t(`patterns.${key}`)}
                  </label>
                  <input
                    id={`${rowId}-new-point-${key}`}
                    className="input w-full"
                    inputMode="numeric"
                    dir="ltr"
                    value={point[key]}
                    aria-invalid={positionProblem(point[key]) !== null || undefined}
                    aria-describedby={
                      positionProblem(point[key]) !== null
                        ? `${rowId}-new-point-${key}-problem`
                        : undefined
                    }
                    onChange={(event) => {
                      setPoint({ ...point, [key]: event.target.value });
                    }}
                    data-testid={`pattern-new-point-${key}`}
                  />
                </div>
              ))}
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${rowId}-new-point-kind`} className="field-label">
                  {t('patterns.kind')}
                </label>
                <select
                  id={`${rowId}-new-point-kind`}
                  className="input w-full"
                  value={point.kind}
                  onChange={(event) => {
                    setPoint({ ...point, kind: event.target.value });
                  }}
                  data-testid="pattern-new-point-kind"
                >
                  {kinds.map((choice) => (
                    <option key={choice} value={choice}>
                      {kindLabel(t, choice)}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
                disabled={
                  point.id.trim() === '' ||
                  pointIdProblem !== null ||
                  positionProblem(point.offset) !== null ||
                  positionProblem(point.level) !== null
                }
                onClick={() => {
                  onChange(
                    addPoint(value, ref, {
                      id: point.id,
                      strip: pointStrip,
                      offset: Number(point.offset),
                      level: Number(point.level),
                      kind: point.kind,
                    }),
                  );
                  announce(t('patterns.added', { what: name('kindPoint', point.id.trim()) }));
                  setPoint({ ...point, id: '', offset: String(Number(point.offset) + 1) });
                  focusLater(`${rowId}-new-point`);
                }}
                data-testid="pattern-add-point"
              >
                {t('patterns.addPoint')}
              </button>
            </div>
            {pointIdProblem !== null && (
              <Problem
                id={`${rowId}-new-point-problem`}
                text={t(`patterns.partProblem.${pointIdProblem}`)}
                testId="pattern-new-point-problem"
              />
            )}
            {(['offset', 'level'] as const).map(
              (key) =>
                positionProblem(point[key]) !== null && (
                  <Problem
                    key={key}
                    id={`${rowId}-new-point-${key}-problem`}
                    text={t('patterns.positionProblem')}
                    testId={`pattern-new-point-${key}-problem`}
                  />
                ),
            )}
          </>
        )}
      </fieldset>

      {/* ------------------------------------------------------------- nets */}
      <fieldset className="flex min-w-0 flex-col gap-3" data-testid="pattern-nets">
        <legend className="text-sm font-semibold">{t('patterns.nets')}</legend>
        <p className="text-xs text-text-muted">{t('patterns.netsHelp')}</p>
        {pattern.nets.length === 0 && (
          <p className="text-xs text-text-muted">{t('patterns.noNets')}</p>
        )}
        <ul className="flex min-w-0 flex-col gap-3">
          {pattern.nets.map((item) => (
            <NetCard
              key={item.role}
              value={value}
              onChange={onChange}
              pattern={pattern}
              net={item}
              base={`${rowId}-net-${item.role}`}
              netProblem={problems.nets[item.role]}
              memberProblems={problems.members}
              connectionProblems={problems.connections}
              announce={announce}
              newNetId={`${rowId}-new-net`}
            />
          ))}
        </ul>
        <div className="flex min-w-0 flex-wrap items-end gap-2">
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-net`} className="field-label">
              {t('patterns.netRole')}
            </label>
            <input
              id={`${rowId}-new-net`}
              className="input w-full"
              dir="ltr"
              maxLength={50}
              value={net.role}
              aria-invalid={netRoleProblem !== null || undefined}
              aria-describedby={netRoleProblem ? `${rowId}-new-net-problem` : undefined}
              onChange={(event) => {
                setNet({ ...net, role: event.target.value });
              }}
              data-testid="pattern-new-net"
            />
          </div>
          <div className="field min-w-0 flex-1">
            <label htmlFor={`${rowId}-new-net-signal`} className="field-label">
              {t('patterns.signal')}
            </label>
            <input
              id={`${rowId}-new-net-signal`}
              className="input w-full"
              dir="auto"
              maxLength={50}
              value={net.signal}
              aria-describedby={`${rowId}-new-net-signal-help`}
              onChange={(event) => {
                setNet({ ...net, signal: event.target.value });
              }}
              data-testid="pattern-new-net-signal"
            />
          </div>
          <div className="field min-w-0 sm:w-40">
            <label htmlFor={`${rowId}-new-net-scope`} className="field-label">
              {t('patterns.scope')}
            </label>
            <select
              id={`${rowId}-new-net-scope`}
              className="input w-full"
              value={net.scope}
              onChange={(event) => {
                setNet({ ...net, scope: event.target.value as NetScope });
              }}
              data-testid="pattern-new-net-scope"
            >
              {NET_SCOPES.map((scope) => (
                <option key={scope} value={scope}>
                  {t(scope === 'board' ? 'patterns.scopeBoard' : 'patterns.scopeBinding')}
                </option>
              ))}
            </select>
          </div>
          <button
            type="button"
            className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
            disabled={net.role.trim() === '' || netRoleProblem !== null || net.signal.trim() === ''}
            onClick={() => {
              onChange(addNet(value, ref, net));
              announce(t('patterns.added', { what: name('kindNet', net.role.trim()) }));
              setNet({ role: '', signal: '', scope: net.scope });
              focusLater(`${rowId}-new-net`);
            }}
            data-testid="pattern-add-net"
          >
            {t('patterns.addNet')}
          </button>
        </div>
        <p id={`${rowId}-new-net-signal-help`} className="text-xs text-text-muted">
          {t('patterns.signalHelp')}
        </p>
        {netRoleProblem !== null && (
          <Problem
            id={`${rowId}-new-net-problem`}
            text={t(`patterns.partProblem.${netRoleProblem}`)}
            testId="pattern-new-net-problem"
          />
        )}
      </fieldset>

      {users.length > 0 && (
        <p className="text-xs" id={`${rowId}-remove-blocked`} data-testid="pattern-remove-blocked">
          {t('patterns.usedBy', { count: String(users.length), bindings: users.join(', ') })}
        </p>
      )}
      <button
        type="button"
        className="btn btn-sm btn-danger self-start max-w-full whitespace-normal text-start"
        disabled={users.length > 0}
        aria-describedby={users.length > 0 ? `${rowId}-remove-blocked` : undefined}
        aria-label={t('patterns.removeNamed', { id })}
        onClick={() => {
          onChange(removePattern(value, ref));
          announce(t('patterns.removed', { what: name('kindPattern', id) }));
          focusLater(addFieldId);
        }}
        data-testid="pattern-remove"
      >
        {t('patterns.remove')}
      </button>
    </li>
  );
}

function PositionField({
  id,
  label,
  value,
  problemText,
  testId,
  onValid,
}: {
  id: string;
  label: string;
  value: number;
  problemText: string;
  testId: string;
  onValid: (next: number) => void;
}) {
  // Kept as typed until it is a whole number ≥ 1; the stored value changes only then.
  const [typed, setTyped] = useState(String(value));
  // A stored value changed elsewhere (an import, a restore) is shown, unless the user is mid-typing an invalid one.
  useEffect(() => {
    setTyped((current) =>
      positionProblem(current) === null && Number(current) !== value ? String(value) : current,
    );
  }, [value]);
  const problem = positionProblem(typed);
  return (
    <div className="field w-24">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      <input
        id={id}
        className="input w-full"
        inputMode="numeric"
        dir="ltr"
        value={typed}
        aria-invalid={problem !== null || undefined}
        aria-describedby={problem ? `${id}-problem` : undefined}
        onChange={(event) => {
          setTyped(event.target.value);
          if (positionProblem(event.target.value) === null) onValid(Number(event.target.value));
        }}
        data-testid={testId}
      />
      {problem !== null && (
        <Problem id={`${id}-problem`} text={problemText} testId={`${testId}-problem`} />
      )}
    </div>
  );
}

function NetCard({
  value,
  onChange,
  pattern,
  net,
  base,
  netProblem,
  memberProblems,
  connectionProblems,
  announce,
  newNetId,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  pattern: Pattern;
  net: Pattern['nets'][number];
  base: string;
  netProblem: 'no_members' | 'no_signal' | undefined;
  memberProblems: Record<string, string>;
  connectionProblems: Record<string, ConnectionProblem>;
  announce: (message: string) => void;
  newNetId: string;
}) {
  const t = useIsolatingTranslations('configuration');
  const ref = pattern.ref;
  const [kind, setKind] = useState<'slot' | 'point'>(pattern.slots.length > 0 ? 'slot' : 'point');
  const [slot, setSlot] = useState('');
  const [terminal, setTerminal] = useState('');
  const [point, setPoint] = useState('');
  const [side, setSide] = useState<Side>('inner');
  const chosenSlot = pattern.slots.find((item) => item.id === (slot || pattern.slots[0]?.id));
  const terminals = chosenSlot ? templateTerminals(value, chosenSlot.template) : null;
  const chosenTerminal = terminals !== null ? terminal || (terminals[0]?.id ?? '') : terminal;
  const chosenPoint = point || (pattern.points[0]?.id ?? '');
  const effectiveKind = kind === 'slot' && pattern.slots.length === 0 ? 'point' : kind;
  const netWireUses = wireDetailUses(value, ref, net.role);

  function memberText(member: PatternMember): string {
    if (isSlotMember(member)) {
      const owner = pattern.slots.find((item) => item.id === member.slot);
      const marking = templateTerminals(value, owner?.template ?? null)?.find(
        (item) => item.id === member.terminal,
      )?.marking;
      return t('patterns.memberSlot', {
        slot: member.slot,
        terminal: marking ? `${marking} (${member.terminal})` : member.terminal,
      });
    }
    const owner = pattern.points.find((item) => item.id === member.point);
    return t('patterns.memberPoint', {
      point: member.point,
      role: owner?.strip ?? '—',
      side: t(member.side === 'outer' ? 'patterns.sideOuter' : 'patterns.sideInner'),
    });
  }

  const memberToAdd: PatternMember | null =
    effectiveKind === 'slot'
      ? chosenSlot && chosenTerminal.trim() !== ''
        ? { slot: chosenSlot.id, terminal: chosenTerminal }
        : null
      : chosenPoint !== ''
        ? { point: chosenPoint, side }
        : null;

  return (
    <li
      className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-3"
      data-testid="pattern-net"
    >
      <h4 className="text-sm font-semibold" dir="auto" data-testid="pattern-net-title">
        {t('patterns.netTitle', { net: net.role })}
      </h4>
      <div className="flex min-w-0 flex-wrap items-end gap-2">
        <div className="field min-w-0 flex-1">
          <label htmlFor={`${base}-signal`} className="field-label">
            {t('patterns.signalOf', { net: net.role })}
          </label>
          <input
            id={`${base}-signal`}
            className="input w-full"
            dir="auto"
            maxLength={50}
            value={net.signal}
            aria-invalid={netProblem === 'no_signal' || undefined}
            aria-describedby={netProblem === 'no_signal' ? `${base}-problem` : undefined}
            onChange={(event) => {
              onChange(updateNet(value, ref, net.role, { signal: event.target.value }));
            }}
            data-testid="pattern-net-signal"
          />
        </div>
        <div className="field min-w-0 sm:w-40">
          <label htmlFor={`${base}-scope`} className="field-label">
            {t('patterns.scopeOf', { net: net.role })}
          </label>
          <select
            id={`${base}-scope`}
            className="input w-full"
            value={net.scope}
            onChange={(event) => {
              onChange(updateNet(value, ref, net.role, { scope: event.target.value as NetScope }));
            }}
            data-testid="pattern-net-scope"
          >
            {NET_SCOPES.map((scope) => (
              <option key={scope} value={scope}>
                {t(scope === 'board' ? 'patterns.scopeBoard' : 'patterns.scopeBinding')}
              </option>
            ))}
          </select>
        </div>
      </div>
      {netProblem !== undefined && (
        <Problem
          id={`${base}-problem`}
          text={t(netProblem === 'no_members' ? 'patterns.noMembers' : 'patterns.noSignal')}
          testId="pattern-net-problem"
        />
      )}
      <ul
        className="flex min-w-0 flex-col gap-1"
        aria-label={t('patterns.members', { net: net.role })}
        data-testid="pattern-net-members"
      >
        {net.members.map((member, index) => {
          const key = `${net.role}#${String(index)}`;
          const problem = memberProblems[key];
          const connected = memberConnections(net, index).length > 0;
          const text = memberText(member);
          const mid = `${base}-member-${String(index)}`;
          const described = [problem ? `${mid}-problem` : '', connected ? `${mid}-connected` : '']
            .filter(Boolean)
            .join(' ');
          return (
            <li key={key} className="flex min-w-0 flex-col gap-1" data-testid="pattern-net-member">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <span
                  className="break-words text-sm"
                  dir="auto"
                  data-testid="pattern-net-member-text"
                >
                  {text}
                </span>
                <button
                  type="button"
                  className="btn btn-sm btn-danger max-w-full whitespace-normal text-start"
                  disabled={connected}
                  aria-describedby={described || undefined}
                  aria-label={t('patterns.removeMember', { member: text })}
                  onClick={() => {
                    onChange(removeMember(value, ref, net.role, index));
                    announce(t('patterns.removed', { what: text }));
                    focusLater(`${base}-member-kind`);
                  }}
                  data-testid="pattern-net-member-remove"
                >
                  {t('editor.remove')}
                </button>
              </div>
              {problem !== undefined && (
                <Problem
                  id={`${mid}-problem`}
                  text={t(`patterns.memberProblem.${problem}`)}
                  testId="pattern-net-member-problem"
                />
              )}
              {connected && (
                <p
                  id={`${mid}-connected`}
                  className="text-xs"
                  data-testid="pattern-net-member-connected"
                >
                  {t('patterns.memberConnected')}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      <ConnectionList
        value={value}
        onChange={onChange}
        pattern={pattern}
        net={net}
        base={base}
        problems={connectionProblems}
        memberText={memberText}
        announce={announce}
      />

      {pattern.slots.length === 0 && pattern.points.length === 0 ? (
        <p className="text-xs text-text-muted" data-testid="pattern-net-need-parts">
          {t('patterns.needSlotOrPoint')}
        </p>
      ) : (
        <fieldset
          className="flex min-w-0 flex-wrap items-end gap-2"
          data-testid="pattern-add-member"
        >
          <legend className="text-xs font-semibold">
            {t('patterns.addMember', { net: net.role })}
          </legend>
          <div className="field min-w-0 sm:w-40">
            <label htmlFor={`${base}-member-kind`} className="field-label">
              {t('patterns.memberKind')}
            </label>
            <select
              id={`${base}-member-kind`}
              className="input w-full"
              value={effectiveKind}
              onChange={(event) => {
                setKind(event.target.value === 'point' ? 'point' : 'slot');
              }}
              data-testid="pattern-member-kind"
            >
              {pattern.slots.length > 0 && (
                <option value="slot">{t('patterns.memberKindSlot')}</option>
              )}
              {pattern.points.length > 0 && (
                <option value="point">{t('patterns.memberKindPoint')}</option>
              )}
            </select>
          </div>
          {effectiveKind === 'slot' ? (
            <>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${base}-member-slot`} className="field-label">
                  {t('patterns.memberSlotPick')}
                </label>
                <select
                  id={`${base}-member-slot`}
                  className="input w-full"
                  dir="ltr"
                  value={chosenSlot?.id ?? ''}
                  onChange={(event) => {
                    setSlot(event.target.value);
                    setTerminal('');
                  }}
                  data-testid="pattern-member-slot"
                >
                  {pattern.slots.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id}
                    </option>
                  ))}
                </select>
              </div>
              {terminals !== null ? (
                <div className="field min-w-0 sm:w-40">
                  <label htmlFor={`${base}-member-terminal`} className="field-label">
                    {t('patterns.memberTerminal')}
                  </label>
                  <select
                    id={`${base}-member-terminal`}
                    className="input w-full"
                    dir="auto"
                    value={chosenTerminal}
                    onChange={(event) => {
                      setTerminal(event.target.value);
                    }}
                    data-testid="pattern-member-terminal"
                  >
                    {terminals.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.marking || item.id}
                      </option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="field min-w-0 sm:w-40">
                  <label htmlFor={`${base}-member-terminal`} className="field-label">
                    {t('patterns.memberTerminalTyped')}
                  </label>
                  <input
                    id={`${base}-member-terminal`}
                    className="input w-full"
                    dir="ltr"
                    maxLength={50}
                    value={terminal}
                    aria-describedby={`${base}-member-terminal-help`}
                    onChange={(event) => {
                      setTerminal(event.target.value);
                    }}
                    data-testid="pattern-member-terminal-typed"
                  />
                  <p id={`${base}-member-terminal-help`} className="text-xs text-text-muted">
                    {t('patterns.memberTerminalHelp')}
                  </p>
                </div>
              )}
            </>
          ) : (
            <>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${base}-member-point`} className="field-label">
                  {t('patterns.memberPointPick')}
                </label>
                <select
                  id={`${base}-member-point`}
                  className="input w-full"
                  dir="ltr"
                  value={chosenPoint}
                  onChange={(event) => {
                    setPoint(event.target.value);
                  }}
                  data-testid="pattern-member-point"
                >
                  {pattern.points.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${base}-member-side`} className="field-label">
                  {t('patterns.memberSide')}
                </label>
                <select
                  id={`${base}-member-side`}
                  className="input w-full"
                  value={side}
                  onChange={(event) => {
                    setSide(event.target.value === 'outer' ? 'outer' : 'inner');
                  }}
                  data-testid="pattern-member-side"
                >
                  {SIDES.map((item) => (
                    <option key={item} value={item}>
                      {t(item === 'outer' ? 'patterns.sideOuter' : 'patterns.sideInner')}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
          <button
            type="button"
            className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
            disabled={memberToAdd === null}
            onClick={() => {
              if (memberToAdd === null) return;
              onChange(addMember(value, ref, net.role, memberToAdd));
              announce(t('patterns.added', { what: memberText(memberToAdd) }));
              if (terminals === null) setTerminal('');
              focusLater(`${base}-member-kind`);
            }}
            data-testid="pattern-add-member-submit"
          >
            {t('patterns.addMemberAction')}
          </button>
        </fieldset>
      )}
      {netWireUses.length > 0 && (
        <p id={`${base}-keep`} className="text-xs" data-testid="pattern-net-keep">
          {t('patterns.conn.netBlocked', {
            uses: netWireUses.map((use) => `${use.binding} (${use.key})`).join(', '),
          })}
        </p>
      )}
      <button
        type="button"
        className="btn btn-sm btn-danger self-start max-w-full whitespace-normal text-start"
        disabled={netWireUses.length > 0}
        aria-describedby={netWireUses.length > 0 ? `${base}-keep` : undefined}
        aria-label={
          net.connections.length > 0
            ? t('patterns.removeNetConnections', {
                net: net.role,
                count: String(net.connections.length),
              })
            : t('patterns.removeNet', { net: net.role })
        }
        onClick={() => {
          onChange(removeNet(value, ref, net.role));
          announce(t('patterns.removed', { what: t('patterns.kindNet', { id: net.role }) }));
          focusLater(newNetId);
        }}
        data-testid="pattern-net-remove"
      >
        {net.connections.length > 0
          ? t('patterns.removeNetConnections', {
              net: net.role,
              count: String(net.connections.length),
            })
          : t('patterns.removeNet', { net: net.role })}
      </button>
    </li>
  );
}

/* M9b2: the connections of a net, edited without JSON. */
function ConnectionList({
  value,
  onChange,
  pattern,
  net,
  base,
  problems,
  memberText,
  announce,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  pattern: Pattern;
  net: Pattern['nets'][number];
  base: string;
  problems: Record<string, ConnectionProblem>;
  memberText: (member: PatternMember) => string;
  announce: (message: string) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  const ref = pattern.ref;
  const uses = wireDetailUses(value, ref, net.role);
  const [draft, setDraft] = useState<{
    from: string;
    to: string;
    via: Via;
    core: string;
    label: string;
  }>({ from: '', to: '', via: 'wire', core: '', label: '' });
  const [coreText, setCoreText] = useState<Record<number, string>>({});
  const n = net.members.length;
  const pick = (typed: string, fallback: number) => (typed === '' ? fallback : Number(typed));
  const from = pick(draft.from, 0);
  const to = pick(draft.to, n > 1 ? 1 : 0);
  const coreOf = (typed: string): number | null => (/^\d+$/.test(typed) ? Number(typed) : null);
  const newDraft: ConnectionDraft = {
    from,
    to,
    via: draft.via,
    core: coreOf(draft.core),
    label: draft.label.trim() === '' ? null : draft.label,
  };
  const newProblem: ConnectionProblem | null =
    n < 2
      ? 'end_missing'
      : draft.via === 'cable_core' && draft.core.trim() !== '' && coreOf(draft.core) === null
        ? 'core_invalid'
        : connectionProblem(net, newDraft);
  const endText = (index: number) => {
    const member = net.members[index];
    return member === undefined
      ? t('patterns.conn.noEnd')
      : `${String(index + 1)}. ${memberText(member)}`;
  };
  const connText = (c: { from: number; to: number; via: string }) =>
    t('patterns.conn.text', {
      from: endText(c.from),
      to: endText(c.to),
      via: t(
        `patterns.conn.via.${(VIAS as readonly string[]).includes(c.via) ? c.via : 'unknown'}`,
      ),
    });
  const usesText = (list: { binding: string; key: string }[]) =>
    list.map((use) => `${use.binding} (${use.key})`).join(', ');

  function endSelect(
    id: string,
    label: string,
    chosen: number,
    onPick: (at: number) => void,
    disabled: boolean,
    testId: string,
  ) {
    return (
      <div className="field min-w-0 sm:w-40">
        <label htmlFor={id} className="field-label">
          {label}
        </label>
        <select
          id={id}
          className="input w-full"
          dir="auto"
          value={String(chosen)}
          disabled={disabled}
          onChange={(event) => {
            onPick(Number(event.target.value));
          }}
          data-testid={testId}
        >
          {chosen >= n && <option value={String(chosen)}>{t('patterns.conn.noEnd')}</option>}
          {net.members.map((member, at) => (
            <option key={at} value={String(at)}>
              {`${String(at + 1)}. ${memberText(member)}`}
            </option>
          ))}
        </select>
      </div>
    );
  }

  function viaSelect(
    id: string,
    chosen: string,
    onPick: (via: Via) => void,
    disabled: boolean,
    testId: string,
  ) {
    return (
      <div className="field min-w-0 sm:w-40">
        <label htmlFor={id} className="field-label">
          {t('patterns.conn.viaLabel')}
        </label>
        <select
          id={id}
          className="input w-full"
          value={chosen}
          disabled={disabled}
          onChange={(event) => {
            onPick(event.target.value as Via);
          }}
          data-testid={testId}
        >
          {!(VIAS as readonly string[]).includes(chosen) && (
            <option value={chosen}>{t('patterns.conn.via.unknown')}</option>
          )}
          {VIAS.map((via) => (
            <option key={via} value={via}>
              {t(`patterns.conn.via.${via}`)}
            </option>
          ))}
        </select>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="pattern-connections">
      <h5 className="text-xs font-semibold">{t('patterns.conn.title', { net: net.role })}</h5>
      {net.connections.length === 0 && (
        <p className="text-xs text-text-muted" data-testid="pattern-connections-none">
          {t('patterns.conn.none')}
        </p>
      )}
      <ol
        className="flex min-w-0 flex-col gap-2"
        aria-label={t('patterns.conn.title', { net: net.role })}
      >
        {net.connections.map((connection, index) => {
          const cid = `${base}-conn-${String(index)}`;
          const problem = problems[`${net.role}#${String(index)}`];
          const own = uses.filter((use) => use.index === index);
          const later = uses.filter((use) => use.index >= index);
          const locked = own.length > 0;
          const text = connText(connection);
          const draftOf = (patch: Partial<ConnectionDraft>): ConnectionDraft => ({
            from: connection.from,
            to: connection.to,
            via: connection.via as Via,
            core: connection.core,
            label: connection.label,
            ...patch,
          });
          const write = (patch: Partial<ConnectionDraft>) => {
            onChange(updateConnection(value, ref, net.role, index, draftOf(patch)));
          };
          const typedCore =
            coreText[index] ?? (connection.core === null ? '' : String(connection.core));
          const coreBad = typedCore.trim() !== '' && coreOf(typedCore) === null;
          const described = [
            problem || coreBad ? `${cid}-problem` : '',
            locked ? `${cid}-locked` : '',
          ]
            .filter(Boolean)
            .join(' ');
          return (
            <li
              key={index}
              className="flex min-w-0 flex-col gap-1 rounded-md border border-border-subtle p-2"
              data-testid="pattern-connection"
              aria-label={text}
            >
              <div className="flex min-w-0 flex-wrap items-end gap-2">
                {endSelect(
                  `${cid}-from`,
                  t('patterns.conn.from'),
                  connection.from,
                  (at) => {
                    write({ from: at });
                  },
                  locked,
                  'pattern-connection-from',
                )}
                {endSelect(
                  `${cid}-to`,
                  t('patterns.conn.to'),
                  connection.to,
                  (at) => {
                    write({ to: at });
                  },
                  locked,
                  'pattern-connection-to',
                )}
                {viaSelect(
                  `${cid}-via`,
                  connection.via,
                  (via) => {
                    setCoreText((prev) => ({ ...prev, [index]: '' }));
                    write({
                      via,
                      core: null,
                      label: via === 'cable_core' ? connection.label : null,
                    });
                  },
                  locked,
                  'pattern-connection-via',
                )}
                {connection.via === 'cable_core' && (
                  <>
                    <div className="field min-w-0 sm:w-40">
                      <label htmlFor={`${cid}-core`} className="field-label">
                        {t('patterns.conn.core')}
                      </label>
                      <input
                        id={`${cid}-core`}
                        className="input w-full"
                        dir="ltr"
                        inputMode="numeric"
                        maxLength={4}
                        value={typedCore}
                        disabled={locked}
                        aria-invalid={Boolean(problem?.startsWith('core')) || coreBad || undefined}
                        aria-describedby={described || undefined}
                        onChange={(event) => {
                          const typed = event.target.value;
                          setCoreText((prev) => ({ ...prev, [index]: typed }));
                          write({ core: coreOf(typed) });
                        }}
                        data-testid="pattern-connection-core"
                      />
                    </div>
                    <div className="field min-w-0 sm:w-40">
                      <label htmlFor={`${cid}-label`} className="field-label">
                        {t('patterns.conn.label')}
                      </label>
                      <input
                        id={`${cid}-label`}
                        className="input w-full"
                        dir="auto"
                        maxLength={50}
                        value={connection.label ?? ''}
                        disabled={locked}
                        onChange={(event) => {
                          write({ label: event.target.value === '' ? null : event.target.value });
                        }}
                        data-testid="pattern-connection-label"
                      />
                    </div>
                  </>
                )}
                <button
                  type="button"
                  className="btn btn-sm btn-danger max-w-full whitespace-normal text-start"
                  disabled={later.length > 0}
                  aria-describedby={later.length > 0 ? `${cid}-keep` : undefined}
                  aria-label={t('patterns.conn.remove', { connection: text })}
                  onClick={() => {
                    onChange(removeConnection(value, ref, net.role, index));
                    setCoreText({});
                    announce(t('patterns.removed', { what: text }));
                    focusLater(`${base}-conn-new-from`);
                  }}
                  data-testid="pattern-connection-remove"
                >
                  {t('editor.remove')}
                </button>
              </div>
              {(problem !== undefined || coreBad) && (
                <Problem
                  id={`${cid}-problem`}
                  text={t(
                    `patterns.conn.problem.${coreBad ? 'core_invalid' : (problem ?? 'core_invalid')}`,
                  )}
                  testId="pattern-connection-problem"
                />
              )}
              {locked && (
                <p id={`${cid}-locked`} className="text-xs" data-testid="pattern-connection-locked">
                  {t('patterns.conn.locked', { uses: usesText(own) })}
                </p>
              )}
              {later.length > 0 && (
                <p id={`${cid}-keep`} className="text-xs" data-testid="pattern-connection-keep">
                  {t('patterns.conn.removeBlocked', { uses: usesText(later) })}
                </p>
              )}
            </li>
          );
        })}
      </ol>
      {n < 2 ? (
        <p className="text-xs text-text-muted" data-testid="pattern-connection-need-members">
          {t('patterns.conn.needMembers')}
        </p>
      ) : (
        <fieldset
          className="flex min-w-0 flex-wrap items-end gap-2"
          data-testid="pattern-add-connection"
        >
          <legend className="text-xs font-semibold">
            {t('patterns.conn.add', { net: net.role })}
          </legend>
          {endSelect(
            `${base}-conn-new-from`,
            t('patterns.conn.from'),
            from,
            (at) => {
              setDraft((d) => ({ ...d, from: String(at) }));
            },
            false,
            'pattern-connection-new-from',
          )}
          {endSelect(
            `${base}-conn-new-to`,
            t('patterns.conn.to'),
            to,
            (at) => {
              setDraft((d) => ({ ...d, to: String(at) }));
            },
            false,
            'pattern-connection-new-to',
          )}
          {viaSelect(
            `${base}-conn-new-via`,
            draft.via,
            (via) => {
              setDraft((d) => ({ ...d, via, core: '', label: '' }));
            },
            false,
            'pattern-connection-new-via',
          )}
          {draft.via === 'cable_core' && (
            <>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${base}-conn-new-core`} className="field-label">
                  {t('patterns.conn.core')}
                </label>
                <input
                  id={`${base}-conn-new-core`}
                  className="input w-full"
                  dir="ltr"
                  inputMode="numeric"
                  maxLength={4}
                  value={draft.core}
                  aria-describedby={`${base}-conn-new-core-help`}
                  onChange={(event) => {
                    const typed = event.target.value;
                    setDraft((d) => ({ ...d, core: typed }));
                  }}
                  data-testid="pattern-connection-new-core"
                />
                <p id={`${base}-conn-new-core-help`} className="text-xs text-text-muted">
                  {t('patterns.conn.coreHelp')}
                </p>
              </div>
              <div className="field min-w-0 sm:w-40">
                <label htmlFor={`${base}-conn-new-label`} className="field-label">
                  {t('patterns.conn.label')}
                </label>
                <input
                  id={`${base}-conn-new-label`}
                  className="input w-full"
                  dir="auto"
                  maxLength={50}
                  value={draft.label}
                  onChange={(event) => {
                    const typed = event.target.value;
                    setDraft((d) => ({ ...d, label: typed }));
                  }}
                  data-testid="pattern-connection-new-label"
                />
              </div>
            </>
          )}
          <button
            type="button"
            className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
            disabled={newProblem !== null}
            aria-describedby={newProblem !== null ? `${base}-conn-new-problem` : undefined}
            onClick={() => {
              if (newProblem !== null) return;
              onChange(addConnection(value, ref, net.role, newDraft));
              announce(t('patterns.added', { what: connText(newDraft) }));
              setDraft((d) => ({ ...d, core: '', label: '' }));
              focusLater(`${base}-conn-new-from`);
            }}
            data-testid="pattern-add-connection-submit"
          >
            {t('patterns.conn.addAction')}
          </button>
          {newProblem !== null && (
            <Problem
              id={`${base}-conn-new-problem`}
              text={t(`patterns.conn.problem.${newProblem}`)}
              testId="pattern-add-connection-problem"
            />
          )}
        </fieldset>
      )}
    </div>
  );
}

export function PatternsEditor({
  value,
  onChange,
  templates,
  conflictRef,
  rowIdOf,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  /** The template refs a slot may name (the configuration's and the opened revision's registry). */
  templates: readonly string[];
  /** The pattern a save refusal names, marked. */
  conflictRef: string | null;
  rowIdOf: (ref: string) => string;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const patterns = patternsOf(value);
  const [newId, setNewId] = useState('');
  const [status, setStatus] = useState('');
  const statusRef = useRef<HTMLParagraphElement>(null);
  const problem = newId === '' ? null : patternIdProblem(value, newId);

  return (
    <section
      className="flex min-w-0 flex-col gap-3"
      aria-labelledby={`${id}-title`}
      data-testid="patterns-editor"
    >
      <h3 id={`${id}-title`} className="text-base font-semibold">
        {t('patterns.title')}
      </h3>
      <p className="text-sm text-text-muted">{t('patterns.intro')}</p>
      <p
        ref={statusRef}
        className="sr-only"
        role="status"
        aria-live="polite"
        data-testid="patterns-status"
      >
        {status}
      </p>
      {patterns.length === 0 ? (
        <p className="text-sm text-text-muted" data-testid="patterns-none">
          {t('patterns.none')}
        </p>
      ) : (
        <ul className="flex min-w-0 flex-col gap-4">
          {patterns.map((pattern) => (
            <PatternRow
              key={pattern.ref}
              value={value}
              onChange={onChange}
              pattern={pattern}
              rowId={rowIdOf(pattern.ref)}
              templates={templates}
              conflicted={conflictRef === pattern.ref}
              announce={setStatus}
              addFieldId={`${id}-add-id`}
            />
          ))}
        </ul>
      )}
      <div className="flex min-w-0 flex-wrap items-end gap-2" data-testid="add-pattern">
        <div className="field min-w-0 flex-1">
          <label htmlFor={`${id}-add-id`} className="field-label">
            {t('patterns.id')}
          </label>
          <input
            id={`${id}-add-id`}
            className="input w-full"
            dir="ltr"
            maxLength={100}
            value={newId}
            aria-invalid={problem !== null || undefined}
            aria-describedby={problem ? `${id}-add-problem` : `${id}-add-help`}
            onChange={(event) => {
              setNewId(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && newId.trim() !== '' && problem === null) {
                event.preventDefault();
                onChange(addPattern(value, newId));
                setStatus(
                  t('patterns.added', { what: t('patterns.kindPattern', { id: newId.trim() }) }),
                );
                setNewId('');
              }
            }}
            data-testid="add-pattern-id"
          />
        </div>
        <button
          type="button"
          className="btn btn-sm btn-secondary max-w-full whitespace-normal text-start"
          disabled={newId.trim() === '' || problem !== null}
          onClick={() => {
            onChange(addPattern(value, newId));
            setStatus(
              t('patterns.added', { what: t('patterns.kindPattern', { id: newId.trim() }) }),
            );
            setNewId('');
          }}
          data-testid="add-pattern-submit"
        >
          {t('patterns.addAction')}
        </button>
      </div>
      <p id={`${id}-add-help`} className="text-xs text-text-muted">
        {t('patterns.idHelp')}
      </p>
      {problem !== null && (
        <Problem
          id={`${id}-add-problem`}
          text={t(`patterns.idProblem.${problem}`)}
          testId="add-pattern-problem"
        />
      )}
    </section>
  );
}
