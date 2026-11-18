import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type { ProjectDesignRequest, ConfigurationOutcome } from '@/lib/design';
import en from '@/messages/en.json';

import { type ConfirmedReference } from '@/components/design/draft';

import { renderApp } from './helpers';

/**
 * M12 (Configuration Contents), on the M10/M11 fixture: the preview of the current load schedule with the configuration; choices
 * from the server's design of the current inputs; moved references said beside
 * their binding and kept only by an explicit action. SYNTHETIC data; mocked API
 * (the real composition and save are checked by the API tests and the browser run).
 */

const SRC = 'tenant/synth-src/v1';
const DST = 'tenant/synth-dst/v1';
const REF = 'tenant/synth-two-slot/v1';

function component(id: string, board: string, template: string, product: string) {
  return { id, board, template, designation: { function: null, location: null, product } };
}

const INITIAL = {
  templates: [],
  patterns: [
    {
      ref: REF,
      slots: [
        { id: 'src', template: SRC, device_kind: null },
        { id: 'dst', template: DST, device_kind: null },
      ],
      strips: ['out'],
      points: [{ id: 'p1', strip: 'out', offset: 1, level: 1, kind: 'unspecified' }],
      nets: [
        {
          role: 'n1',
          signal: 'S1',
          scope: 'binding',
          members: [
            { slot: 'src', terminal: 't-001' },
            { point: 'p1', side: 'inner' },
          ],
          connections: [{ from: 0, to: 1, via: 'wire', core: null, label: null }],
        },
      ],
      provenance: { source: null, author: 'user-entered', created: '2026-11-16' },
      review: {
        status: 'unreviewed',
        reviewer: null,
        reviewed_at: null,
        standards: [],
        scope_note: null,
      },
    },
  ],
  strips: [
    {
      id: 'strip-xp',
      board: 'MB-1',
      template: null,
      designation: { function: null, location: null, product: 'XP' },
    },
  ],
  components: [component('comp-ka', 'MB-1', SRC, 'KA'), component('comp-kb', 'MB-1', DST, 'KB')],
  bindings: [],
};

// The composed rows as the server derives their ids for bind-1 (see m9b1-binding-model.test.ts),
// plus rows of another binding the preview must not show.

const BOUND = {
  ...INITIAL,
  bindings: [
    {
      id: 'bind-1',
      pattern: REF,
      board: 'MB-1',
      circuit: 'c1',
      slots: { src: { component: 'comp-ka' }, dst: { component: 'comp-kb' } },
      strips: { out: 'strip-xp' },
      markings: { p1: '7' },
      cable: null,
      wires: { 'n1/0': { number: { value: 'W7', source: 'user' } } },
    },
  ],
};

const REQUEST = { boards: [{ name: 'MB-1', loads: [] }] } as unknown as ProjectDesignRequest;

type Validate = (options: {
  configuration: Record<string, unknown>;
  request?: ProjectDesignRequest | null;
  referencesConfirmed?: ConfirmedReference[];
}) => Promise<ConfigurationOutcome>;

const CABLED = {
  ...BOUND,
  bindings: [{ ...(BOUND.bindings[0] as Record<string, unknown>), cable: 'c1-cable' }],
};
const CABLE_BOARDS: BoardChoices[] = [
  {
    id: 'MB-1',
    circuits: [{ id: 'c1', label: 'c1 · Lighting' }],
    cables: [{ id: 'c1-cable', label: 'MB-1 W1 (c1-cable)' }],
  },
  { id: 'SB-1', circuits: [], cables: [] },
];

function CablePanel({
  validateImpl,
  externalRefusal = null,
  locale,
}: {
  validateImpl: Validate;
  externalRefusal?: ConfigurationOutcome | null;
  locale?: string;
}) {
  const [value, setValue] = useState<Record<string, unknown>>(CABLED);
  void locale;
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={1}
      value={value}
      onChange={setValue}
      status="none"
      boards={['MB-1', 'SB-1']}
      model={null}
      boardChoices={CABLE_BOARDS}
      currentRequest={REQUEST}
      confirmed={[]}
      onConfirm={vi.fn()}
      onUnauthorized={vi.fn()}
      validateImpl={validateImpl as never}
      externalRefusal={externalRefusal as never}
    />
  );
}

const C = en.configuration.contents;
const fill = (text: string, values: Record<string, string>) =>
  Object.entries(values).reduce((out, [k, v]) => out.replaceAll(`{${k}}`, v), text);

describe('M12 Configuration Contents', () => {
  it('shows counts from the configuration as edited, and follows edits', () => {
    renderApp(<CablePanel validateImpl={vi.fn()} />, { locale: 'en', theme: 'light' });
    const nav = screen.getByTestId('configuration-contents');
    expect(within(nav).getByTestId('contents-components').textContent).toBe(
      fill(C.components, { count: '2' }),
    );
    expect(within(nav).getByTestId('contents-strips').textContent).toBe(
      fill(C.strips, { count: '1' }),
    );
    expect(within(nav).getByTestId('contents-bindings').textContent).toBe(
      fill(C.bindings, { count: '1' }),
    );
    // Removing the strip's component is not possible while bound; remove the binding instead.
    fireEvent.click(screen.getByTestId('binding-remove'));
    const confirm = screen.queryByTestId('binding-remove-confirm-yes');
    if (confirm !== null) fireEvent.click(confirm);
    expect(within(nav).getByTestId('contents-bindings').textContent).toBe(
      fill(C.bindings, { count: '0' }),
    );
  });

  it('a section link focuses its heading; Back to contents returns', () => {
    renderApp(<CablePanel validateImpl={vi.fn()} />, { locale: 'en', theme: 'light' });
    for (const key of ['definitions', 'patterns', 'components', 'strips', 'bindings'] as const) {
      fireEvent.click(screen.getByTestId(`contents-${key}`));
      const active = document.activeElement as HTMLElement;
      expect(active.tagName).toBe('H3');
      expect(active.id.endsWith(`-${key}`)).toBe(true);
    }
    fireEvent.click(screen.getByTestId('back-bindings'));
    expect((document.activeElement as HTMLElement).id.endsWith('-contents')).toBe(true);
  });

  it('a shortcut never adds, previews or validates', () => {
    const validate = vi.fn<Validate>();
    renderApp(<CablePanel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('contents-add-binding'));
    // The first field of the add form, not its (disabled until valid) Add button.
    expect(document.activeElement).toBe(screen.getByTestId('add-binding-id'));
    fireEvent.click(screen.getByTestId('contents-preview'));
    expect(document.activeElement).toBe(screen.getByTestId('binding-preview-run'));
    fireEvent.click(screen.getByTestId('contents-validate'));
    expect(document.activeElement).toBe(screen.getByTestId('configuration-validate'));
    expect(screen.getByTestId<HTMLDetailsElement>('configuration-advanced').open).toBe(true);
    for (const kind of ['definition', 'pattern', 'component', 'strip', 'binding'] as const) {
      fireEvent.click(screen.getByTestId(`go-add-${kind}`));
      expect(
        (document.activeElement as HTMLElement).closest(`[data-testid="add-${kind}"]`),
      ).not.toBeNull();
    }
    expect(validate).not.toHaveBeenCalled();
    expect(screen.getByTestId('contents-bindings').textContent).toBe(
      fill(C.bindings, { count: '1' }),
    );
  });

  it('a disabled Validate is not focused: its explanation is', () => {
    function Empty() {
      const [value, setValue] = useState<Record<string, unknown>>({});
      return (
        <ConfigurationPanel
          token="t"
          projectId="p1"
          latestRevision={1}
          value={value}
          onChange={setValue}
          status="none"
          boards={['MB-1']}
          model={null}
          boardChoices={null}
          onUnauthorized={vi.fn()}
          validateImpl={vi.fn() as never}
        />
      );
    }
    renderApp(<Empty />, { locale: 'en', theme: 'light' });
    expect(screen.getByTestId<HTMLButtonElement>('configuration-validate').disabled).toBe(true);
    fireEvent.click(screen.getByTestId('contents-validate'));
    expect((document.activeElement as HTMLElement).id.endsWith('-validate-help')).toBe(true);
    // No binding: the preview shortcut says so and reaches the bindings section.
    expect(screen.getByTestId('contents-preview').textContent).toBe(C.previewNeedsBinding);
    fireEvent.click(screen.getByTestId('contents-preview'));
    expect((document.activeElement as HTMLElement).id.endsWith('-bindings')).toBe(true);
    // No bindings, so no issue overview: the issues link reaches the bindings section.
    fireEvent.click(screen.getByTestId('contents-issues'));
    expect((document.activeElement as HTMLElement).id.endsWith('-bindings')).toBe(true);
  });

  it('the issues link reaches the issue overview; only a small status region is live', () => {
    renderApp(<CablePanel validateImpl={vi.fn()} />, { locale: 'en', theme: 'light' });
    const nav = screen.getByTestId('configuration-contents');
    expect(nav.getAttribute('aria-live')).toBeNull();
    expect(nav.getAttribute('role')).toBeNull();
    expect(within(nav).getByTestId('contents-status').getAttribute('role')).toBe('status');
    fireEvent.click(screen.getByTestId('contents-issues'));
    expect((document.activeElement as HTMLElement).id.endsWith('-issues')).toBe(true);
    expect(screen.getByTestId('binding-overview').contains(document.activeElement)).toBe(true);
  });

  it('is in Arabic and Hebrew too', async () => {
    const ar = (await import('@/messages/ar.json')).default.configuration.contents;
    const he = (await import('@/messages/he.json')).default.configuration.contents;
    expect(Object.keys(ar).sort()).toEqual(Object.keys(C).sort());
    expect(Object.keys(he).sort()).toEqual(Object.keys(C).sort());
    renderApp(<CablePanel validateImpl={vi.fn()} />, { locale: 'ar', theme: 'light' });
    expect(screen.getByTestId('configuration-contents').textContent).toContain(ar.title);
  });
});
