'use client';

/**
 * Graphite Workbench primitives: the shapes every screen repeats, as
 * components where a component earns its place (it owns structure, ARIA or
 * behaviour) and left as the `.btn` / `.input` / `.card` classes in
 * `globals.css` where it would only wrap a native element.
 *
 * The APIs here are the contract the screens are written against. Styling may
 * change; a prop's meaning may not without updating every caller.
 */

import {
  useEffect,
  useId,
  useRef,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react';

import { AlertTriangleIcon, CheckIcon, CloseIcon, InfoIcon } from '@/components/icons';

/* ---------------------------------------------------------------- Page --- */

/**
 * A screen's title row: title, one line of purpose, and the actions that
 * belong to this screen only. Global actions live in the shell, never here.
 */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** Screen-specific controls, end-aligned; they wrap below on a phone. */
  actions?: ReactNode;
  /** A status or context line under the description, such as a save state. */
  meta?: ReactNode;
}) {
  return (
    <div className="page-header">
      <div className="min-w-0">
        <h1 className="page-title">{title}</h1>
        {description && <p className="page-description">{description}</p>}
        {meta && <div className="mt-2">{meta}</div>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </div>
  );
}

/** A titled region of a screen. Renders an `h2` so the outline stays flat. */
export function Panel({
  title,
  actions,
  children,
  className = '',
  bodyClassName = '',
  testId,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  testId?: string;
}) {
  const headingId = useId();
  return (
    <section
      className={`panel ${className}`}
      aria-labelledby={title ? headingId : undefined}
      data-testid={testId}
    >
      {(title || actions) && (
        <div className="panel-header">
          {title && (
            <h2 id={headingId} className="panel-title">
              {title}
            </h2>
          )}
          {actions && <div className="ms-auto flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={`panel-body ${bodyClassName}`}>{children}</div>
    </section>
  );
}

/* ---------------------------------------------------------------- Tabs --- */

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  /** A count or status shown after the label. */
  badge?: ReactNode;
  disabled?: boolean;
}

/**
 * Contextual tabs (WAI-ARIA tabs pattern, manual activation).
 *
 * Arrow keys move focus between tabs and Enter/Space selects, so moving
 * through the tabs does not trigger a fetch per keypress. Arrow direction
 * follows the document direction: in RTL, ArrowLeft moves to the NEXT tab.
 * Panels are the caller's: give each `role="tabpanel"`, `id={tabPanelId(idBase, key)}`
 * and `aria-labelledby={tabId(idBase, key)}`.
 */
export function Tabs<K extends string>({
  items,
  selected,
  onSelect,
  label,
  idBase,
}: {
  items: ReadonlyArray<TabItem<K>>;
  selected: K;
  onSelect: (key: K) => void;
  /** Accessible name of the tab list. */
  label: string;
  idBase: string;
}) {
  const refs = useRef(new Map<K, HTMLButtonElement>());

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const enabled = items.filter((item) => !item.disabled);
    const focused = enabled.findIndex(
      (item) => refs.current.get(item.key) === document.activeElement,
    );
    if (focused === -1) return;
    const rtl = getComputedStyle(event.currentTarget).direction === 'rtl';
    let next: number | null = null;
    if (event.key === 'ArrowRight') next = focused + (rtl ? -1 : 1);
    else if (event.key === 'ArrowLeft') next = focused + (rtl ? 1 : -1);
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = enabled.length - 1;
    if (next === null) return;
    event.preventDefault();
    const target = enabled[(next + enabled.length) % enabled.length];
    if (target) refs.current.get(target.key)?.focus();
  }

  return (
    <div role="tablist" aria-label={label} className="tabs" onKeyDown={onKeyDown}>
      {items.map((item) => {
        const active = item.key === selected;
        return (
          <button
            key={item.key}
            ref={(node) => {
              if (node) refs.current.set(item.key, node);
              else refs.current.delete(item.key);
            }}
            type="button"
            role="tab"
            id={tabId(idBase, item.key)}
            aria-selected={active}
            // Only the selected tab's panel is guaranteed to exist: screens
            // that render one panel at a time would otherwise point at nothing.
            aria-controls={active ? tabPanelId(idBase, item.key) : undefined}
            tabIndex={active ? 0 : -1}
            disabled={item.disabled}
            onClick={() => {
              onSelect(item.key);
            }}
            className="tab"
          >
            {item.label}
            {item.badge !== undefined && <span className="tab-badge">{item.badge}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function tabId(idBase: string, key: string): string {
  return `${idBase}-tab-${key}`;
}

export function tabPanelId(idBase: string, key: string): string {
  return `${idBase}-panel-${key}`;
}

/* -------------------------------------------------------------- Status --- */

export type Tone = 'neutral' | 'accent' | 'info' | 'warning' | 'critical' | 'success';

/**
 * A status label. Always text: tone adds colour and a shape-bearing dot, but
 * the word carries the meaning, so status never depends on colour alone.
 */
export function StatusBadge({
  tone = 'neutral',
  children,
  testId,
}: {
  tone?: Tone;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <span className={`status status-${tone}`} data-testid={testId}>
      <span className="status-dot" aria-hidden="true" />
      {children}
    </span>
  );
}

/* --------------------------------------------------------- Async states --- */

/** Nothing here yet, and what to do about it. */
export function EmptyState({
  title,
  children,
  action,
  icon,
}: {
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  icon?: ReactElement;
}) {
  return (
    <div className="empty-state">
      {icon && <div className="empty-state-icon">{icon}</div>}
      <p className="font-semibold text-text">{title}</p>
      {children && <div className="max-w-prose text-sm text-text-muted">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** A polite, labelled loading line; motion is suppressed under reduced motion. */
export function LoadingState({ children }: { children: ReactNode }) {
  return (
    <p role="status" className="loading-state">
      <span className="spinner" aria-hidden="true" />
      {children}
    </p>
  );
}

export type NoticeTone = 'info' | 'warning' | 'critical' | 'success';

/**
 * An inline message. `critical` is an alert (announced immediately); the
 * others are polite status. Pass `live={false}` for a static notice that is
 * part of the page rather than the result of something just happening.
 */
export function Notice({
  tone = 'info',
  title,
  children,
  action,
  live = true,
  testId,
}: {
  tone?: NoticeTone;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  live?: boolean;
  testId?: string;
}) {
  const Icon =
    tone === 'critical' || tone === 'warning'
      ? AlertTriangleIcon
      : tone === 'success'
        ? CheckIcon
        : InfoIcon;
  const role = !live ? undefined : tone === 'critical' ? 'alert' : 'status';
  return (
    <div role={role} className={`notice notice-${tone}`} data-testid={testId}>
      <Icon width="18" height="18" className="notice-icon" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className="text-sm">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export type SaveState = 'idle' | 'unsaved' | 'saving' | 'saved' | 'failed';

/** Where the user's work stands. Announced politely when it changes. */
export function SaveIndicator({
  state,
  labels,
}: {
  state: SaveState;
  /** Translated labels; the caller owns the namespace. */
  labels: Record<Exclude<SaveState, 'idle'>, string>;
}) {
  return (
    <span role="status" className="save-indicator" data-state={state}>
      {state !== 'idle' && (
        <StatusBadge
          tone={
            state === 'saved'
              ? 'success'
              : state === 'failed'
                ? 'critical'
                : state === 'saving'
                  ? 'info'
                  : 'warning'
          }
        >
          {labels[state]}
        </StatusBadge>
      )}
    </span>
  );
}

/* --------------------------------------------------------------- Field --- */

/**
 * A labelled control with its unit and its error beside it. The control is
 * the caller's (so tests and screen readers see the real element); give it
 * `id={id}` and spread `fieldDescribedBy(id, { hint, error })` onto it.
 */
export function FormField({
  id,
  label,
  unit,
  hint,
  error,
  children,
}: {
  id: string;
  label: ReactNode;
  unit?: string;
  hint?: ReactNode;
  error?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      <div className="field-control">
        <div className="min-w-0 flex-1">{children}</div>
        {unit && (
          <span className="field-unit" dir="ltr">
            {unit}
          </span>
        )}
      </div>
      {hint && (
        <p id={`${id}-hint`} className="field-hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
    </div>
  );
}

export function fieldDescribedBy(
  id: string,
  { hint, error }: { hint?: unknown; error?: unknown },
): { 'aria-describedby'?: string; 'aria-invalid'?: true } {
  const ids = [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean);
  return {
    ...(ids.length > 0 ? { 'aria-describedby': ids.join(' ') } : {}),
    ...(error ? { 'aria-invalid': true as const } : {}),
  };
}

/* --------------------------------------------------------------- Table --- */

/**
 * A wide table in its own scroll container, labelled so a keyboard user can
 * focus and scroll it. Text never shrinks to fit; the container scrolls.
 */
export function TableFrame({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-frame" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

/* ------------------------------------------------------- Dialog / Drawer --- */

/**
 * A modal dialog or side drawer on the native `<dialog>`, which gives focus
 * trapping, Escape and the inert background for free. Focus returns to the
 * element that was focused when it opened.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  variant = 'dialog',
  closeLabel,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** `drawer` slides from the inline end (start in RTL handled by logical CSS). */
  variant?: 'dialog' | 'drawer' | 'drawer-start';
  /** Accessible name of the close button, translated by the caller. */
  closeLabel: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);
  const headingId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      returnTo.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
      // jsdom lacks showModal; fall back to the attribute so tests can render.
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    } else if (!open && dialog.open) {
      if (typeof dialog.close === 'function') dialog.close();
      else dialog.removeAttribute('open');
      returnTo.current?.focus();
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`dialog dialog-${variant}`}
      aria-labelledby={headingId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        // A click on the backdrop lands on the dialog element itself.
        if (event.target === ref.current) onClose();
      }}
    >
      <div className="dialog-surface">
        <div className="dialog-header">
          <h2 id={headingId} className="panel-title">
            {title}
          </h2>
          <button
            type="button"
            className="btn-icon ms-auto"
            aria-label={closeLabel}
            onClick={onClose}
          >
            <CloseIcon width="18" height="18" />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </div>
    </dialog>
  );
}

/* ----------------------------------------------------- Source reference --- */

/**
 * Where a statement came from. Renders only what it is given: a reference
 * with no document is never padded with a placeholder that looks like one.
 */
export function SourceReference({
  title,
  detail,
  href,
  excerpt,
  testId,
}: {
  title: ReactNode;
  /** Page, clause or section, shown in the technical face. */
  detail?: string;
  href?: string;
  excerpt?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="source-ref" data-testid={testId}>
      <div className="flex min-w-0 items-baseline gap-2">
        {href ? (
          <a
            href={href}
            className="link min-w-0 break-words"
            target="_blank"
            rel="noreferrer noopener"
          >
            {title}
          </a>
        ) : (
          <span className="min-w-0 break-words font-medium">{title}</span>
        )}
        {detail && (
          <bdi className="shrink-0 font-mono text-xs text-text-muted" dir="ltr">
            {detail}
          </bdi>
        )}
      </div>
      {excerpt && <blockquote className="source-ref-excerpt">{excerpt}</blockquote>}
    </div>
  );
}
