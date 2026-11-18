'use client';

/**
 * The Configuration editor's Contents: links to its sections, its issue lists, and its main
 * actions (the add-binding form, a binding's preview, Validate), with counts from the editor's
 * current state.
 *
 * Navigation only: a link scrolls to its target and moves focus there; it never adds, submits,
 * validates or previews. A disabled action is never focused (it cannot be): its link reaches
 * what explains the prerequisite, or the section that holds it. Headings it targets take
 * focus with `tabIndex={-1}`, so the page's Tab order is unchanged.
 */

import { useIsolatingTranslations } from '@/components/design/bidi';
import { bindingProblems, type BoardChoices } from '@/components/design/binding-model';
import type { OverviewValidation } from '@/components/design/binding-overview';
import {
  bindingsOf,
  componentsOf,
  stripsOf,
  type Configuration,
} from '@/components/design/configuration-model';
import { definitionsOf } from '@/components/design/definition-model';
import { patternsOf } from '@/components/design/pattern-model';
import { wireProblems } from '@/components/design/wire-model';

/** The element ids the Contents links reach (all derived from the editor's id base). */
export function contentsIds(idBase: string) {
  return {
    contents: `${idBase}-contents`,
    definitions: `${idBase}-definitions`,
    patterns: `${idBase}-patterns`,
    components: `${idBase}-components`,
    strips: `${idBase}-strips`,
    bindings: `${idBase}-bindings`,
    issues: `${idBase}-issues`,
    addDefinition: `${idBase}-add-definition`,
    addPattern: `${idBase}-add-pattern`,
    addComponent: `${idBase}-add-component`,
    addStrip: `${idBase}-add-strip`,
    addBinding: `${idBase}-add-binding`,
    validate: `${idBase}-validate`,
    validateHelp: `${idBase}-validate-help`,
  };
}

const FOCUSABLE =
  'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), button:not([disabled]), a[href]';

/**
 * Move to an element: scroll it into view and focus it, without activating anything.
 *
 * A form container focuses its first enabled control (or itself); an element inside a closed
 * `<details>` opens it first (opening shows it; it validates nothing). A disabled control
 * cannot be focused: `fallback` (what explains it, or its section) is reached instead.
 *
 * @returns Whether focus moved.
 */
export function goToTarget(id: string, fallback?: string): boolean {
  let element = document.getElementById(id);
  const disabled =
    (element instanceof HTMLButtonElement ||
      element instanceof HTMLInputElement ||
      element instanceof HTMLSelectElement ||
      element instanceof HTMLTextAreaElement) &&
    element.disabled;
  if (disabled) element = null;
  if (element === null && fallback !== undefined) element = document.getElementById(fallback);
  if (element === null) return false;
  const details = element.closest('details');
  if (details !== null && !details.open) details.open = true;
  let target: HTMLElement = element;
  if (element.tagName === 'FIELDSET' || element.dataset.contentsForm === 'true') {
    target = element.querySelector<HTMLElement>(FOCUSABLE) ?? element;
  }
  // Centred, so a sticky header never covers it (not every test DOM can scroll).
  if ('scrollIntoView' in target) target.scrollIntoView({ block: 'center' });
  target.focus();
  return document.activeElement === target;
}

/** The counts the Contents shows, from the configuration as edited. */
export function contentsCounts(
  value: Configuration,
  boardChoices: BoardChoices[] | null,
  validation: OverviewValidation | null,
) {
  const bindings = bindingsOf(value);
  // Counted as the issue overview lists them: wire details that name no wire of the pattern
  // (stray, extra, unreadable) are one item per binding there, so one here.
  const toFix = bindings.reduce((sum, binding) => {
    const wires = wireProblems(value, binding);
    const others = wires.stray.length + wires.extra.length + wires.unreadable.length;
    return (
      sum +
      bindingProblems(value, binding, boardChoices).length +
      Object.keys(wires.values).length +
      (others > 0 ? 1 : 0)
    );
  }, 0);
  return {
    definitions: definitionsOf(value).length,
    patterns: patternsOf(value).length,
    components: componentsOf(value).length,
    strips: stripsOf(value).length,
    bindings: bindings.length,
    toFix,
    findings: validation === null ? null : validation.findings.length,
    findingsCurrent: validation?.current ?? false,
  };
}

/** A link that moves to its target on activation (a plain in-page link without script). */
function GoLink({
  target,
  fallback,
  children,
  testId,
}: {
  target: string;
  fallback?: string | undefined;
  children: React.ReactNode;
  testId: string;
}) {
  return (
    <a
      href={`#${target}`}
      className="link max-w-full whitespace-normal break-words text-sm underline"
      onClick={(event) => {
        event.preventDefault();
        goToTarget(target, fallback);
      }}
      data-testid={testId}
    >
      {children}
    </a>
  );
}

export function ConfigurationContents({
  idBase,
  value,
  boardChoices,
  validation,
  previewTarget,
}: {
  idBase: string;
  value: Configuration;
  boardChoices: BoardChoices[] | null;
  validation: OverviewValidation | null;
  /** The first binding's preview button, when a preview can be asked for; else null. */
  previewTarget: string | null;
}) {
  const t = useIsolatingTranslations('configuration');
  const ids = contentsIds(idBase);
  const counts = contentsCounts(value, boardChoices, validation);
  const issues =
    counts.findings === null
      ? t('contents.issues', { count: String(counts.toFix) })
      : t(counts.findingsCurrent ? 'contents.issuesFindings' : 'contents.issuesFindingsOutdated', {
          count: String(counts.toFix),
          findings: String(counts.findings),
        });
  const section = (key: 'definitions' | 'patterns' | 'components' | 'strips' | 'bindings') => (
    <li key={key}>
      <GoLink target={ids[key]} testId={`contents-${key}`}>
        {t(`contents.${key}`, { count: String(counts[key]) })}
      </GoLink>
    </li>
  );
  return (
    <nav
      className="flex min-w-0 flex-col gap-2 rounded-md border border-border-subtle p-3"
      aria-labelledby={ids.contents}
      data-testid="configuration-contents"
    >
      <h3 id={ids.contents} tabIndex={-1} className="text-sm font-semibold">
        {t('contents.title')}
      </h3>
      <div className="flex min-w-0 flex-wrap gap-x-6 gap-y-2">
        <div className="flex min-w-0 flex-col gap-1">
          <h4 className="text-xs font-semibold text-text-muted">{t('contents.sections')}</h4>
          <ul className="flex min-w-0 flex-col gap-1">
            {section('definitions')}
            {section('patterns')}
            {section('components')}
            {section('strips')}
            {section('bindings')}
            <li>
              <GoLink target={ids.issues} fallback={ids.bindings} testId="contents-issues">
                {issues}
              </GoLink>
            </li>
          </ul>
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <h4 className="text-xs font-semibold text-text-muted">{t('contents.actions')}</h4>
          <ul className="flex min-w-0 flex-col gap-1">
            <li>
              <GoLink
                target={ids.addBinding}
                fallback={`${ids.addBinding}-needs`}
                testId="contents-add-binding"
              >
                {t('contents.addBinding')}
              </GoLink>
            </li>
            <li>
              <GoLink
                target={previewTarget ?? ids.bindings}
                fallback={ids.bindings}
                testId="contents-preview"
              >
                {previewTarget === null ? t('contents.previewNeedsBinding') : t('contents.preview')}
              </GoLink>
            </li>
            <li>
              <GoLink target={ids.validate} fallback={ids.validateHelp} testId="contents-validate">
                {t('contents.validate')}
              </GoLink>
            </li>
          </ul>
        </div>
      </div>
      {/* Issue-count changes only, in their own small region (the Contents itself is not live). */}
      <p className="sr-only" role="status" data-testid="contents-status">
        {t('contents.issuesStatus', { count: String(counts.toFix) })}
      </p>
    </nav>
  );
}

/** After a section's heading: the way to its add form. */
export function GoToAddForm({
  target,
  testId,
  fallback,
}: {
  target: string;
  testId: string;
  /** What explains why there is no form yet (e.g. bindings need a pattern). */
  fallback?: string | undefined;
}) {
  const t = useIsolatingTranslations('configuration');
  return (
    <p className="text-sm">
      <GoLink target={target} fallback={fallback} testId={testId}>
        {t('contents.goAdd')}
      </GoLink>
    </p>
  );
}

/** At a section's end: back to the Contents. */
export function BackToContents({ idBase, testId }: { idBase: string; testId: string }) {
  const t = useIsolatingTranslations('configuration');
  return (
    <p className="text-sm">
      <GoLink target={contentsIds(idBase).contents} testId={testId}>
        {t('contents.back')}
      </GoLink>
    </p>
  );
}
