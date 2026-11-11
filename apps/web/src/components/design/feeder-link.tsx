'use client';

/**
 * M7a: the guided "Board-to-board link" form of a feeder, and a saved
 * revision's links with their findings (docs/architecture/m7a-feeder-link.md).
 *
 * The form never prefills from the designer's feeder cable: the calculation's
 * values stand beside each field, read-only, and where a typed value differs
 * both are shown with a "differs" flag that says nothing about which is right.
 * Nothing here says a link is complete.
 */
import { useId } from 'react';

import { Field } from '@/components/cable-sizing-panel';
import { useIsolatingTranslations } from '@/components/design/bidi';
import {
  CALCULATED_ATTRIBUTES,
  LINK_ATTRIBUTES,
  LINK_MATERIALS,
  blankLink,
  isBlankLink,
  linkValueProblem,
  terminalOptions,
  terminalText,
  valuesDiffer,
  type CalculatedValues,
  type LinkAttribute,
  type LinkEndForm,
  type LinkFinding,
  type LinkForm,
  type StoredLink,
  type StoredLinkEnd,
} from '@/components/design/feeder-link-model';
import type { V2ModelSummary } from '@/lib/design';

const NUMERIC: ReadonlySet<LinkAttribute> = new Set<LinkAttribute>([
  'cores',
  'cross_section_mm2',
  'length_m',
]);

export function FeederLinkForm({
  supplyBoard,
  fedBoard,
  value,
  onChange,
  model,
  calculated,
  refusal,
}: {
  supplyBoard: string;
  fedBoard: string;
  value: LinkForm | undefined;
  /** Undefined once nothing is entered: the board then keeps no link. */
  onChange: (next: LinkForm | undefined) => void;
  /** The opened revision's v2/v3 model, whose terminals an end can name; null for none. */
  model: V2ModelSummary | null;
  /** What the feeder calculation used; null while the project is not designed. */
  calculated: CalculatedValues | null;
  /** A save refusal about this link, said in the reader's language. */
  refusal?: string | null;
}) {
  const t = useIsolatingTranslations('feederLink');
  const id = useId();
  const link = value ?? blankLink();

  function update(next: LinkForm) {
    onChange(isBlankLink(next) ? undefined : next);
  }

  function end(role: 'from' | 'to') {
    const board = role === 'from' ? supplyBoard : fedBoard;
    const current: LinkEndForm = link[role];
    const options = terminalOptions(model, board, t('unnamed'));
    const chosen = options.find((option) => option.value === current.terminal);
    const set = (patch: Partial<LinkEndForm>) => {
      update({ ...link, [role]: { ...current, ...patch } });
    };
    const base = `${id}-${role}`;
    const notSet = current.terminal === '' && current.label.trim() === '';
    return (
      <fieldset
        className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-3"
        data-testid={`link-end-${role}`}
      >
        <legend className="px-1 text-sm font-semibold">
          {t(role === 'from' ? 'fromEnd' : 'toEnd', { board })}
        </legend>
        <Field id={`${base}-terminal`} label={t('terminal', { board })}>
          <select
            id={`${base}-terminal`}
            className="input w-full min-w-0 max-w-full"
            dir="auto"
            value={current.terminal}
            data-testid={`link-${role}-terminal`}
            onChange={(event) => {
              set({ terminal: event.target.value });
            }}
          >
            <option value="">{t('terminalNone')}</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.text}
              </option>
            ))}
          </select>
        </Field>
        {chosen && (
          <p
            className="min-w-0 break-words text-xs"
            dir="auto"
            data-testid={`link-${role}-terminal-chosen`}
          >
            {t('terminalChosen', { terminal: chosen.text })}
          </p>
        )}
        {options.length === 0 && (
          <p className="text-xs text-text-muted" data-testid={`link-${role}-no-terminals`}>
            {t('noTerminals', { board })}
          </p>
        )}
        <Field id={`${base}-label`} label={t('label')}>
          <input
            id={`${base}-label`}
            className="input w-full"
            dir="auto"
            maxLength={200}
            value={current.label}
            aria-describedby={`${base}-label-note`}
            data-testid={`link-${role}-label`}
            onChange={(event) => {
              set({ label: event.target.value });
            }}
          />
        </Field>
        <p
          id={`${base}-label-note`}
          className="text-xs text-text-muted"
          data-testid={`link-${role}-label-note`}
        >
          {t('labelNote')}
        </p>
        {notSet && (
          <p className="text-xs font-semibold" data-testid={`link-${role}-not-set`}>
            {t('endNotSet')}
          </p>
        )}
      </fieldset>
    );
  }

  function attribute(key: LinkAttribute) {
    const fieldId = `${id}-attr-${key}`;
    const typed = link.attributes[key];
    const calc = calculated?.[key];
    const hasCalc = CALCULATED_ATTRIBUTES.has(key);
    const differs = hasCalc && valuesDiffer(key, typed, calc);
    const problem = linkValueProblem(key, typed);
    const set = (text: string) => {
      update({ ...link, attributes: { ...link.attributes, [key]: text } });
    };
    const described = [hasCalc ? `${fieldId}-calc` : null, differs ? `${fieldId}-differs` : null]
      .filter(Boolean)
      .join(' ');
    return (
      <div key={key} className="flex min-w-0 flex-col gap-1" data-testid={`link-attr-${key}`}>
        <Field
          id={fieldId}
          label={t(`attr.${key}`)}
          error={problem ? t(`problem.${problem}`) : undefined}
        >
          {key === 'material' ? (
            <select
              id={fieldId}
              className="input w-full"
              value={typed}
              aria-describedby={described || undefined}
              data-testid={`link-input-${key}`}
              onChange={(event) => {
                set(event.target.value);
              }}
            >
              <option value="">{t('notGiven')}</option>
              {LINK_MATERIALS.map((material) => (
                <option key={material} value={material}>
                  {t(`material.${material}`)}
                </option>
              ))}
            </select>
          ) : (
            <input
              id={fieldId}
              className="input w-full"
              dir={NUMERIC.has(key) ? 'ltr' : 'auto'}
              inputMode={NUMERIC.has(key) ? 'decimal' : undefined}
              maxLength={100}
              placeholder={t('notGiven')}
              value={typed}
              aria-invalid={problem ? true : undefined}
              aria-describedby={described || undefined}
              data-testid={`link-input-${key}`}
              onChange={(event) => {
                set(event.target.value);
              }}
            />
          )}
        </Field>
        {hasCalc && calculated !== null && (
          <output
            id={`${fieldId}-calc`}
            className="text-xs text-text-muted"
            data-testid={`link-calc-${key}`}
          >
            {calc === null || calc === undefined
              ? t('calculationNone')
              : t('calculation', { value: calc })}
          </output>
        )}
        {differs && calc != null && (
          <p id={`${fieldId}-differs`} className="text-xs" data-testid={`link-differs-${key}`}>
            <span className="status status-warning me-1">{t('differs')}</span>
            {t('differsDetail', { user: typed.trim(), calc })}
          </p>
        )}
      </div>
    );
  }

  return (
    <fieldset
      className="panel flex min-w-0 flex-col gap-4 p-4 md:p-5"
      data-testid="feeder-link-form"
    >
      <legend className="px-1 text-sm font-semibold">
        {t('title', { from: supplyBoard, to: fedBoard })}
      </legend>
      <p className="text-sm text-text-muted">{t('intro', { from: supplyBoard, to: fedBoard })}</p>
      {refusal && (
        <p role="alert" className="field-error" data-testid="feeder-link-refusal">
          {t('refusedHere', { reason: refusal })}
        </p>
      )}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {end('from')}
        {end('to')}
      </div>
      <fieldset className="flex min-w-0 flex-col gap-3" data-testid="link-attributes">
        <legend className="px-1 text-sm font-semibold">{t('attributes')}</legend>
        {calculated === null && (
          <p className="text-xs text-text-muted" data-testid="link-calc-not-run">
            {t('calculationNotRun')}
          </p>
        )}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {LINK_ATTRIBUTES.map(attribute)}
        </div>
      </fieldset>
      {value !== undefined && (
        <button
          type="button"
          className="btn btn-sm btn-secondary self-start"
          data-testid="feeder-link-clear"
          onClick={() => {
            onChange(undefined);
          }}
        >
          {t('clear')}
        </button>
      )}
    </fieldset>
  );
}

function text(value: unknown): string {
  if (Array.isArray(value)) return value.map(text).join(', ');
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

/** A saved revision's board-to-board links, each end and attribute as stored, and its findings. */
export function StoredLinks({
  links,
  findings,
  model,
  revision,
}: {
  links: readonly StoredLink[];
  findings: readonly LinkFinding[];
  model: V2ModelSummary | null;
  revision: number;
}) {
  const t = useIsolatingTranslations('feederLink');
  const attrName = (key: string) =>
    (LINK_ATTRIBUTES as readonly string[]).includes(key) ? t(`attr.${key as LinkAttribute}`) : key;

  function endText(end: StoredLinkEnd): string {
    if (end.terminal) return t('endTerminal', { terminal: terminalText(end.terminal, model) });
    // A label is the user's text: the end stays not set, and says so first.
    if (end.label) return `${t('endNotSetStored')}; ${t('endLabelStored', { label: end.label })}`;
    return t('endNotSetStored');
  }

  // A stored material named in the reader's language where it is one the form offers.
  function storedValue(key: LinkAttribute, value: unknown): string {
    const plain = String(value);
    if (key !== 'material') return plain;
    const metal = { copper: 'copper', cu: 'copper', aluminium: 'aluminium', al: 'aluminium' }[
      plain.trim().toLowerCase()
    ] as 'copper' | 'aluminium' | undefined;
    return metal ? t(`material.${metal}`) : plain;
  }

  function findingText(finding: LinkFinding): string {
    // The API sends a finding's values as `values` (strings); older shapes as `params`.
    const params = finding.values ?? finding.params ?? {};
    if (finding.code === 'link_endpoint_unresolved') {
      const raw = text(params.end ?? params.role);
      const board = text(params.end_board);
      // Said in the reader's language: "From MB-1", not the API's "from".
      const end =
        raw === 'from'
          ? t('endFrom', { board }).trim()
          : raw === 'to'
            ? t('endTo', { board }).trim()
            : raw;
      return end
        ? t('finding.link_endpoint_unresolved', { end })
        : t('finding.link_endpoint_unresolved_plain');
    }
    if (finding.code === 'link_attribute_unset') {
      const raw = params.attributes;
      // The API sends a comma-separated string (finding values are strings).
      const items: unknown[] = Array.isArray(raw)
        ? raw
        : typeof raw === 'string'
          ? raw.split(',').map((item) => item.trim())
          : [];
      const list = items
        .filter((item) => text(item) !== '')
        .map((item) => attrName(text(item)))
        .join(', ');
      return list
        ? t('finding.link_attribute_unset', { attributes: list })
        : t('finding.link_attribute_unset_plain');
    }
    const attribute = text(params.attribute);
    const user = text(params.user_value ?? params.user);
    const calc = text(params.calculation_value ?? params.calculation);
    return attribute && user && calc
      ? t('finding.link_value_differs_from_calculation', {
          attribute: attrName(attribute),
          user,
          calc,
        })
      : t('finding.link_value_differs_from_calculation_plain');
  }

  const belongs = (finding: LinkFinding, link: StoredLink) => {
    const params = finding.values ?? finding.params ?? {};
    if (typeof params.link_id === 'string') return params.link_id === link.id;
    return finding.board_id === link.feeder.fed_board;
  };
  const unplaced = findings.filter((finding) => !links.some((link) => belongs(finding, link)));

  return (
    <section className="flex flex-col gap-4" data-testid="stored-links">
      <h3 className="text-base font-semibold">{t('storedTitle', { revision })}</h3>
      {links.length === 0 && (
        <p className="text-sm text-text-muted" data-testid="stored-links-none">
          {t('storedNone')}
        </p>
      )}
      {links.map((link) => {
        const own = findings.filter((finding) => belongs(finding, link));
        return (
          <article
            key={link.id}
            className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-3"
            data-testid={`stored-link-${link.feeder.fed_board}`}
          >
            <h4 className="text-sm font-semibold break-words">
              {t('storedLink', {
                id: link.id,
                from: link.feeder.supply_board,
                to: link.feeder.fed_board,
              })}
            </h4>
            <ul className="flex flex-col gap-1 text-sm">
              {link.ends.map((end) => (
                <li key={end.role} data-testid={`stored-end-${end.role}`} className="break-words">
                  <span className="font-semibold">
                    {t(end.role === 'from' ? 'endFrom' : 'endTo', { board: end.board })}
                  </span>
                  {': '}
                  {endText(end)}
                </li>
              ))}
            </ul>
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-sm sm:grid-cols-2">
              {LINK_ATTRIBUTES.map((key) => {
                const attr = link.attributes[key];
                const given = attr?.source === 'user' && attr.value !== null;
                return (
                  <div key={key} className="flex gap-2" data-testid={`stored-attr-${key}`}>
                    <dt className="font-semibold">{t(`attr.${key}`)}</dt>
                    <dd>
                      {given ? (
                        <>
                          <bdi>{storedValue(key, attr.value)}</bdi>{' '}
                          <span className="text-text-muted">({t('sourceUser')})</span>
                        </>
                      ) : (
                        <span className="text-text-muted">{t('notGiven')}</span>
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
            {link.provenance.entered_at && (
              <p className="text-xs text-text-muted">
                {t('enteredBy', { at: link.provenance.entered_at })}
              </p>
            )}
            <FindingItems findings={own} say={findingText} />
          </article>
        );
      })}
      {unplaced.length > 0 && <FindingItems findings={unplaced} say={findingText} />}
    </section>
  );
}

function FindingItems({
  findings,
  say,
}: {
  findings: readonly LinkFinding[];
  say: (finding: LinkFinding) => string;
}) {
  const t = useIsolatingTranslations('feederLink');
  return (
    <div className="flex flex-col gap-1">
      <h5 className="text-sm font-semibold">{t('findings')}</h5>
      {findings.length === 0 ? (
        <p className="text-xs text-text-muted">{t('findingsNone')}</p>
      ) : (
        <ul className="flex list-disc flex-col gap-1 ps-5 text-sm" data-testid="link-findings">
          {findings.map((finding, index) => (
            <li key={`${finding.code}-${String(index)}`} data-code={finding.code}>
              {say(finding)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
