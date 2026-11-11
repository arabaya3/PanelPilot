'use client';

/**
 * M7b: the guided editor of a configuration document
 * (docs/architecture/m7b-guided-configuration.md §5).
 *
 * It adds components and terminal strips only on template definitions
 * already present (the configuration's own templates or the registry
 * definitions the opened revision names), shows each one's review status and
 * provenance exactly as stored (unreviewed or unknown stays flagged), and lets
 * the user type designations and point markings. Nothing is prefilled or
 * invented. Bindings and patterns are kept as loaded; a binding can have its
 * point markings edited or be removed.
 */
import { useId, useState } from 'react';

import { useIsolatingTranslations } from '@/components/design/bidi';
import {
  addComponent,
  addStrip,
  bindingPoints,
  bindingsOf,
  componentsOf,
  designationOf,
  designationText,
  removeEntry,
  setMarking,
  stripsOf,
  templateChoices,
  updateEntry,
  type Configuration,
  type ConflictTarget,
  type TemplateChoice,
} from '@/components/design/configuration-model';
import { ReviewStatus } from '@/components/design/configuration-review';
import { TechnicalToken } from '@/components/technical-token';
import type { V2ModelSummary } from '@/lib/design';

/** The element id of an entry's row, which a conflict's Edit action focuses. */
export function entryElementId(base: string, kind: string, id: string): string {
  return `${base}-${kind}-${id.replace(/[^A-Za-z0-9_-]/g, '_')}`;
}

function TemplateFacts({ choice }: { choice: TemplateChoice | undefined }) {
  const t = useIsolatingTranslations('configuration');
  if (choice === undefined) {
    return (
      <span className="text-xs text-text-muted" data-testid="template-not-available">
        {t('editor.templateNotAvailable')}
      </span>
    );
  }
  return (
    <span className="flex min-w-0 flex-wrap items-center gap-2 text-xs">
      <ReviewStatus status={choice.reviewStatus ?? 'unknown'} />
      <span className="break-words text-text-muted">
        {choice.provenance === null
          ? t('provenanceMissing')
          : t('editor.provenance', { source: choice.provenance })}
      </span>
      <span className="text-text-muted">
        {t(choice.source === 'registry' ? 'editor.fromRegistry' : 'editor.fromConfiguration')}
      </span>
    </span>
  );
}

/** A template chosen from a list of radios, so long refs wrap on a narrow screen. */
function TemplatePicker({
  name,
  legend,
  choices,
  value,
  onChange,
  allowNone,
  testId,
}: {
  name: string;
  legend: string;
  choices: TemplateChoice[];
  value: string | null;
  onChange: (ref: string | null) => void;
  allowNone: boolean;
  testId: string;
}) {
  const t = useIsolatingTranslations('configuration');
  return (
    <fieldset className="flex min-w-0 flex-col gap-1" data-testid={testId}>
      <legend className="text-sm font-semibold">{legend}</legend>
      {allowNone && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="radio"
            name={name}
            checked={value === null}
            onChange={() => {
              onChange(null);
            }}
          />
          <span>{t('editor.noTemplate')}</span>
        </label>
      )}
      {choices.length === 0 && (
        <p className="text-xs text-text-muted" data-testid={`${testId}-none`}>
          {t('editor.noTemplates')}
        </p>
      )}
      {choices.map((choice) => (
        <label
          key={choice.ref}
          className="flex min-w-0 items-start gap-2 rounded-md border border-border-subtle p-2 text-sm"
        >
          <input
            type="radio"
            name={name}
            value={choice.ref}
            checked={value === choice.ref}
            onChange={() => {
              onChange(choice.ref);
            }}
            data-testid="template-choice"
          />
          <span className="flex min-w-0 flex-col gap-1">
            <span className="break-all font-mono" dir="ltr">
              {choice.ref}
            </span>
            {choice.function !== null && (
              <span className="text-xs text-text-muted">{choice.function}</span>
            )}
            <TemplateFacts choice={choice} />
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function AddEntry({
  kind,
  boards,
  choices,
  onAdd,
}: {
  kind: 'component' | 'strip';
  boards: string[];
  choices: TemplateChoice[];
  onAdd: (entry: { board: string; template: string | null; designation: string }) => void;
}) {
  const t = useIsolatingTranslations('configuration');
  const id = useId();
  const [board, setBoard] = useState(boards[0] ?? '');
  const [template, setTemplate] = useState<string | null>(null);
  const [designation, setDesignation] = useState('');
  const chosenBoard = boards.includes(board) ? board : (boards[0] ?? '');
  const ready = chosenBoard !== '' && (kind === 'strip' || template !== null);
  return (
    <fieldset
      className="flex min-w-0 flex-col gap-3 rounded-md border border-border-subtle p-3"
      data-testid={`add-${kind}`}
    >
      <legend className="px-1 text-sm font-semibold">
        {t(kind === 'component' ? 'editor.addComponent' : 'editor.addStrip')}
      </legend>
      <div className="field min-w-0">
        <label htmlFor={`${id}-board`} className="field-label">
          {t('editor.board')}
        </label>
        <select
          id={`${id}-board`}
          className="input w-full"
          dir="auto"
          value={chosenBoard}
          onChange={(event) => {
            setBoard(event.target.value);
          }}
          data-testid={`add-${kind}-board`}
        >
          {boards.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <TemplatePicker
        name={`${id}-template`}
        legend={t('editor.template')}
        choices={choices}
        value={template}
        onChange={setTemplate}
        allowNone={kind === 'strip'}
        testId={`add-${kind}-template`}
      />
      <div className="field min-w-0">
        <label htmlFor={`${id}-designation`} className="field-label">
          {t('editor.designation')}
        </label>
        <input
          id={`${id}-designation`}
          className="input w-full"
          dir="auto"
          maxLength={100}
          value={designation}
          onChange={(event) => {
            setDesignation(event.target.value);
          }}
          data-testid={`add-${kind}-designation`}
        />
      </div>
      <button
        type="button"
        className="btn btn-sm btn-secondary self-start"
        disabled={!ready}
        onClick={() => {
          onAdd({ board: chosenBoard, template, designation });
          setTemplate(null);
          setDesignation('');
        }}
        data-testid={`add-${kind}-submit`}
      >
        {t(kind === 'component' ? 'editor.addComponentAction' : 'editor.addStripAction')}
      </button>
    </fieldset>
  );
}

export function ConfigurationEditor({
  value,
  onChange,
  boards,
  model,
  conflict,
  idBase,
}: {
  value: Configuration;
  onChange: (next: Configuration) => void;
  /** The board names of the form as it stands: an entry belongs to one of them. */
  boards: string[];
  /** The opened revision's v2/v3 model, whose registry definitions are offered too; null for none. */
  model: V2ModelSummary | null;
  /** A save's `configuration_conflict`, whose entry is marked. */
  conflict: ConflictTarget | null;
  /** Prefix of entries' element ids (see `entryElementId`). */
  idBase: string;
}) {
  const t = useIsolatingTranslations('configuration');
  const choices = templateChoices(value, model);
  const byRef = new Map(choices.map((choice) => [choice.ref, choice]));
  const marked = (kind: ConflictTarget['kind'], id: string) =>
    conflict !== null && conflict.kind === kind && conflict.entry === id;

  const components = componentsOf(value);
  const strips = stripsOf(value);
  const bindings = bindingsOf(value);

  function entryRow(
    kind: 'component' | 'strip',
    entry: { id: string; board: string; template: string | null; designation: unknown },
  ) {
    const section = kind === 'component' ? 'components' : 'strips';
    const rowId = entryElementId(idBase, kind, entry.id);
    const conflicted = marked(kind, entry.id);
    return (
      <li
        key={entry.id}
        id={rowId}
        tabIndex={-1}
        className={`flex min-w-0 flex-col gap-2 rounded-md border p-3 ${
          conflicted ? 'border-status-critical' : 'border-border-subtle'
        }`}
        data-testid={`${kind}-entry`}
        data-conflict={conflicted || undefined}
      >
        <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
          <span className="font-semibold">{t('editor.board')}:</span>
          <span dir="auto">{entry.board}</span>
          {!boards.includes(entry.board) && (
            <span className="text-xs font-semibold" data-testid="entry-board-missing">
              {t('editor.boardMissing')}
            </span>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-1 text-sm">
          <span className="font-semibold">{t('editor.template')}:</span>
          {entry.template === null ? (
            <span>{t('editor.noTemplate')}</span>
          ) : (
            <>
              <TechnicalToken className="break-all">{entry.template}</TechnicalToken>
              <TemplateFacts choice={byRef.get(entry.template)} />
            </>
          )}
        </div>
        <div className="field min-w-0">
          <label htmlFor={`${rowId}-designation`} className="field-label">
            {t('editor.designation')}
          </label>
          <input
            id={`${rowId}-designation`}
            className="input w-full"
            dir="auto"
            maxLength={100}
            value={designationText(entry.designation)}
            onChange={(event) => {
              onChange(
                updateEntry(value, section, entry.id, {
                  designation: designationOf(event.target.value, entry.designation),
                }),
              );
            }}
            data-testid={`${kind}-designation`}
          />
        </div>
        <button
          type="button"
          className="btn btn-sm btn-danger self-start"
          onClick={() => {
            onChange(removeEntry(value, section, entry.id));
          }}
          data-testid={`${kind}-remove`}
        >
          {t('editor.remove')}
        </button>
      </li>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-6" data-testid="configuration-editor">
      <p className="text-sm text-text-muted">{t('editor.intro')}</p>

      <section className="flex min-w-0 flex-col gap-3" aria-labelledby={`${idBase}-components`}>
        <h3 id={`${idBase}-components`} className="text-base font-semibold">
          {t('editor.components')}
        </h3>
        {components.length === 0 ? (
          <p className="text-sm text-text-muted">{t('emptyTable')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {components.map((entry) => entryRow('component', entry))}
          </ul>
        )}
        <AddEntry
          kind="component"
          boards={boards}
          choices={choices}
          onAdd={(entry) => {
            if (entry.template === null) return;
            onChange(addComponent(value, { ...entry, template: entry.template }));
          }}
        />
      </section>

      <section className="flex min-w-0 flex-col gap-3" aria-labelledby={`${idBase}-strips`}>
        <h3 id={`${idBase}-strips`} className="text-base font-semibold">
          {t('editor.strips')}
        </h3>
        {strips.length === 0 ? (
          <p className="text-sm text-text-muted">{t('emptyTable')}</p>
        ) : (
          <ul className="flex flex-col gap-2">{strips.map((entry) => entryRow('strip', entry))}</ul>
        )}
        <AddEntry
          kind="strip"
          boards={boards}
          choices={choices}
          onAdd={(entry) => {
            onChange(addStrip(value, entry));
          }}
        />
      </section>

      <section className="flex min-w-0 flex-col gap-3" aria-labelledby={`${idBase}-bindings`}>
        <h3 id={`${idBase}-bindings`} className="text-base font-semibold">
          {t('editor.bindings')}
        </h3>
        {bindings.length === 0 ? (
          <p className="text-sm text-text-muted">{t('editor.noBindings')}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {bindings.map((binding) => {
              const rowId = entryElementId(idBase, 'binding', binding.id);
              const conflicted = marked('binding', binding.id);
              const points = bindingPoints(value, binding);
              return (
                <li
                  key={binding.id}
                  id={rowId}
                  tabIndex={-1}
                  className={`flex min-w-0 flex-col gap-2 rounded-md border p-3 ${
                    conflicted ? 'border-status-critical' : 'border-border-subtle'
                  }`}
                  data-testid="binding-entry"
                  data-conflict={conflicted || undefined}
                >
                  <p className="flex min-w-0 flex-wrap gap-2 text-sm">
                    <span className="font-semibold">{t('editor.pattern')}:</span>
                    <TechnicalToken className="break-all">{binding.pattern}</TechnicalToken>
                    <span>
                      {t('editor.bindingOn', {
                        board: binding.board,
                        circuit: binding.circuit ?? t('editor.noCircuit'),
                      })}
                    </span>
                  </p>
                  {points.length === 0 ? (
                    <p className="text-xs text-text-muted">{t('editor.noPoints')}</p>
                  ) : (
                    <fieldset className="flex min-w-0 flex-col gap-2">
                      <legend className="text-sm font-semibold">{t('editor.points')}</legend>
                      {points.map((point) => (
                        <div key={point.id} className="field min-w-0">
                          <label htmlFor={`${rowId}-point-${point.id}`} className="field-label">
                            {t('editor.pointMarking', {
                              point: point.id,
                              strip: point.strip === '' ? '—' : point.strip,
                            })}
                          </label>
                          <input
                            id={`${rowId}-point-${point.id}`}
                            className="input w-full"
                            dir="auto"
                            maxLength={50}
                            value={point.marking}
                            placeholder={t('notGivenPlaceholder')}
                            onChange={(event) => {
                              onChange(setMarking(value, binding.id, point.id, event.target.value));
                            }}
                            data-testid="point-marking"
                          />
                        </div>
                      ))}
                    </fieldset>
                  )}
                  <button
                    type="button"
                    className="btn btn-sm btn-danger self-start"
                    onClick={() => {
                      onChange(removeEntry(value, 'bindings', binding.id));
                    }}
                    data-testid="binding-remove"
                  >
                    {t('editor.removeBinding')}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
