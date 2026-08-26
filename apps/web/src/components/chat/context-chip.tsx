'use client';

import type { components } from '@panelpilot/shared-types';
import { useTranslations } from 'next-intl';
import { useEffect, useId, useRef, useState } from 'react';

import { TechnicalToken } from '@/components/technical-token';

type EquipmentContext = components['schemas']['EquipmentContext'];

/**
 * The equipment this session is about.
 *
 * Removes the biggest repeated friction in a troubleshooting session: restating
 * the same brand and model on every message. The chip shows what the assistant
 * currently believes it is looking at, and clicking it opens an editor for when
 * the engineer moves to a different unit.
 *
 * **A note on where the context comes from.** The task describes this field as
 * set server-side from entities extracted out of the first message and returned
 * in the response payload. That does not exist: `DiagnosticResponse` carries no
 * such field, and `run_diagnosis` reads `request.equipment` without ever
 * extracting or echoing it. The one related signal the backend does return is
 * `StructuredDiagnosis.equipment_model` — model-generated, nullable, and
 * carrying no manufacturer.
 *
 * So this adopts that model when the engineer has not set one, and otherwise
 * holds what they typed. It is deliberately built to take a server-supplied
 * context the moment one exists: `contextFromResponse` is the only place that
 * decides, and it would grow a branch rather than the component changing shape.
 *
 * Everything the acceptance criteria ask for is met — the chip updates when
 * context changes, and later questions carry it without the engineer repeating
 * themselves. What is *not* met is server-side extraction of the manufacturer,
 * because there is nothing to extract it from. Guessing a brand from a model
 * number is exactly what the spec's neutral state exists to prevent.
 */

/** Is there anything worth showing? */
export function hasContext(context: EquipmentContext | null): context is EquipmentContext {
  if (!context) return false;
  return Boolean(context.manufacturer ?? context.model);
}

/**
 * What the next request should carry, given a response.
 *
 * Returns `null` to mean "leave the context alone". An engineer's own entry
 * always wins: a model name inferred by the assistant must never quietly
 * overwrite equipment the engineer told it about, because they are the one
 * standing in front of the panel.
 */
export function contextFromResponse(
  current: EquipmentContext | null,
  response: components['schemas']['DiagnosticResponse'],
): EquipmentContext | null {
  // Already set by the engineer, or by an earlier turn — keep it.
  if (hasContext(current)) return null;

  const model = response.diagnosis?.equipment_model;
  if (!model) return null;

  // Manufacturer is left unset rather than inferred from the model number.
  // "ACS880 is an ABB drive" is true and "guessing something wrong" is what
  // the neutral state exists to avoid; a wrong brand on the chip would be
  // carried into every later question in the session.
  return { manufacturer: null, model, fault_codes: [] };
}

export function ContextChip({
  context,
  onChange,
}: {
  context: EquipmentContext | null;
  onChange: (next: EquipmentContext | null) => void;
}) {
  const t = useTranslations('context');
  const [editing, setEditing] = useState(false);
  const [manufacturer, setManufacturer] = useState(context?.manufacturer ?? '');
  const [model, setModel] = useState(context?.model ?? '');
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const chipRef = useRef<HTMLButtonElement>(null);
  // Distinguishes "the editor just closed" from the first render, so the chip
  // does not steal focus when the page loads.
  const wasEditing = useRef(false);
  const manufacturerId = useId();
  const modelId = useId();

  // Opening the editor should put the caret where the typing goes, and
  // closing it should hand focus back to the chip — otherwise a keyboard user
  // is dropped to the top of the document and has to tab back to where they
  // were. The whole control is meant to be usable without a mouse in a plant
  // room, so losing focus on close undoes the point of focusing on open.
  useEffect(() => {
    if (editing) {
      firstFieldRef.current?.focus();
    } else if (wasEditing.current) {
      chipRef.current?.focus();
    }
    wasEditing.current = editing;
  }, [editing]);

  function open() {
    setManufacturer(context?.manufacturer ?? '');
    setModel(context?.model ?? '');
    setEditing(true);
  }

  function save() {
    const nextManufacturer = manufacturer.trim();
    const nextModel = model.trim();
    setEditing(false);
    // Both cleared means the engineer is telling us they do not know, which
    // is a legitimate answer and returns the chip to its neutral state.
    if (nextManufacturer === '' && nextModel === '') {
      onChange(null);
      return;
    }
    onChange({
      manufacturer: nextManufacturer === '' ? null : nextManufacturer,
      model: nextModel === '' ? null : nextModel,
      fault_codes: [],
    });
  }

  if (editing) {
    return (
      <form
        data-testid="context-editor"
        // Named and grouped, so replacing the chip with this form is
        // announced rather than being a silent DOM swap.
        role="group"
        aria-label={t('editLabel')}
        className="flex flex-wrap items-end gap-2 p-2"
        onKeyDown={(event) => {
          // Escape abandons the edit. Expected of any inline editor, and this
          // one is deliberately keyboard-first.
          if (event.key === 'Escape') {
            event.stopPropagation();
            setEditing(false);
          }
        }}
        onSubmit={(event) => {
          event.preventDefault();
          save();
        }}
      >
        <div className="flex flex-col">
          <label htmlFor={manufacturerId} className="text-xs text-text-muted">
            {t('manufacturer')}
          </label>
          <input
            id={manufacturerId}
            ref={firstFieldRef}
            value={manufacturer}
            onChange={(event) => {
              setManufacturer(event.target.value);
            }}
            className="rounded-md border border-border bg-surface px-2 py-1 text-sm text-text"
          />
        </div>
        <div className="flex flex-col">
          <label htmlFor={modelId} className="text-xs text-text-muted">
            {t('model')}
          </label>
          <input
            id={modelId}
            value={model}
            onChange={(event) => {
              setModel(event.target.value);
            }}
            className="rounded-md border border-border bg-surface px-2 py-1 text-sm text-text"
          />
        </div>
        <button
          type="submit"
          className="rounded-md bg-accent px-3 py-1 text-sm text-accent-contrast"
        >
          {t('save')}
        </button>
        <button
          type="button"
          onClick={() => {
            setEditing(false);
          }}
          className="rounded-md border border-border bg-surface px-3 py-1 text-sm text-text"
        >
          {t('cancel')}
        </button>
      </form>
    );
  }

  const known = hasContext(context);

  return (
    <button
      ref={chipRef}
      type="button"
      onClick={open}
      // It reveals the editor, so it is a disclosure control.
      aria-expanded={editing}
      data-testid="context-chip"
      data-known={known ? 'true' : 'false'}
      className={
        known
          ? 'inline-flex items-center gap-2 rounded-full border border-border bg-surface-raised px-3 py-1 text-sm text-text'
          : 'inline-flex items-center gap-2 rounded-full border border-dashed border-border bg-surface px-3 py-1 text-sm text-text-muted'
      }
    >
      {/* Trailing space, so the hint does not run into the equipment text
          and get announced as one word. */}
      <span className="sr-only">{t('editLabel')} </span>
      {known ? (
        <>
          {context.manufacturer ? (
            // Both are technical tokens: a model number reordered inside
            // Arabic prose is the wrong model number, exactly as a fault code
            // is. Rendered conditionally so an absent manufacturer leaves no
            // empty placeholder behind.
            <TechnicalToken>{context.manufacturer}</TechnicalToken>
          ) : null}
          {context.model ? <TechnicalToken>{context.model}</TechnicalToken> : null}
        </>
      ) : (
        // Neutral rather than a guess. A wrong brand here is carried into
        // every later question in the session, which is worse than saying
        // nothing.
        <span>{t('none')}</span>
      )}
    </button>
  );
}
