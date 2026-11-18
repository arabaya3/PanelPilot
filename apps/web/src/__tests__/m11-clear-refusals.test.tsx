import { act, fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { BoardChoices } from '@/components/design/binding-model';
import { ConfigurationPanel } from '@/components/design/configuration-panel';
import type { ProjectDesignRequest, ConfigurationOutcome } from '@/lib/design';
import en from '@/messages/en.json';

import { type ConfirmedReference } from '@/components/design/draft';
import {
  EXPLAINED_REFUSALS,
  explainRefusal,
  refusalSentence,
} from '@/components/design/refusal-explain';

import { renderApp } from './helpers';

/**
 * M11 (clear refusals), on the M10 fixture: the preview of the current load schedule with the configuration; choices
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

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function refused(params: Record<string, string>): ConfigurationOutcome {
  return {
    kind: 'refused',
    detail: 'the configuration cannot be applied: <b>server prose</b>',
    code: 'configuration_refused',
    params: { ...params, kind: 'cable', ref: params.cable ?? '' },
  };
}

const SHARED = {
  refusal: 'core_shared_cable_id',
  binding: 'bind-1',
  cable: 'c1-cable',
  core: '1',
  other_board: 'SB-1',
};

const E = en.configuration.explain;

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

describe('explainRefusal', () => {
  it('points core refusals at the cable and keeps the code as a detail', () => {
    const explained = explainRefusal(SHARED);
    expect(explained).toMatchObject({
      code: 'core_shared_cable_id',
      key: 'core_shared_cable_id',
      binding: 'bind-1',
      field: { name: 'cable' },
    });
    expect(explained.values).toMatchObject({ cable: 'c1-cable', core: '1', otherBoard: 'SB-1' });
  });

  it('maps a field only where the parameters name it, never guessing one', () => {
    expect(
      explainRefusal({ refusal: 'unknown_reference', binding: 'b', circuit: 'c9' }).field,
    ).toEqual({ name: 'circuit' });
    expect(explainRefusal({ refusal: 'unknown_reference', binding: 'b', cable: 'x' }).key).toBe(
      'unknown_reference_cable',
    );
    expect(explainRefusal({ refusal: 'missing_binding', binding: 'b', slot: 'src' }).field).toEqual(
      { name: 'slot', key: 'src' },
    );
    expect(
      explainRefusal({ refusal: 'reference_moved', binding: 'b', field: 'slot', key: 'q' }).field,
    ).toEqual({ name: 'slot', key: 'q' });
    // Named nothing more specific: the binding itself, no field.
    expect(
      explainRefusal({ refusal: 'board_mismatch', binding: 'b', component: 'k' }).field,
    ).toBeNull();
    // Without a binding, no field at all.
    expect(explainRefusal({ refusal: 'core_reused', cable: 'c1-cable' }).field).toBeNull();
  });

  it('gives an unknown code the generic sentence, with the code kept', () => {
    const explained = explainRefusal({ refusal: 'brand_new_code', binding: 'b' });
    expect(explained.key).toBe('unknown');
    expect(explained.code).toBe('brand_new_code');
  });

  it('has every sentence in English, Arabic and Hebrew', async () => {
    await Promise.resolve();
    const ar = (await import('@/messages/ar.json')).default.configuration.explain;
    const he = (await import('@/messages/he.json')).default.configuration.explain;
    expect(Object.keys(ar).sort()).toEqual(Object.keys(E).sort());
    expect(Object.keys(he).sort()).toEqual(Object.keys(E).sort());
    for (const code of EXPLAINED_REFUSALS) {
      if (code === 'unknown_reference') continue; // said by what it names
      expect(E).toHaveProperty(code);
    }
  });
});

describe('M11 clear refusals', () => {
  it('a refused save says what failed, where and what next, marks the cable and goes to it', () => {
    renderApp(<CablePanel validateImpl={vi.fn()} externalRefusal={refused(SHARED)} />, {
      locale: 'en',
      theme: 'light',
    });
    const error = screen.getByTestId('configuration-error');
    expect(error.textContent).toContain('Not saved: binding bind-1 on board MB-1.');
    expect(error.textContent).toContain(
      'does not yet save the same cable id and core on two boards',
    );
    expect(error.textContent).toContain('Change the cable only if c1-cable was chosen by mistake');
    // The server's prose is never shown; the code is a secondary detail.
    expect(error.textContent).not.toContain('server prose');
    expect(screen.getByTestId('configuration-error-code').textContent).toBe(
      E.code.replace('{code}', 'core_shared_cable_id'),
    );
    // The cable is where the restriction applies, not a wrong choice: a neutral note, not invalid.
    const cable = screen.getByTestId<HTMLSelectElement>('binding-cable');
    expect(cable.getAttribute('aria-invalid')).toBeNull();
    expect(cable.getAttribute('aria-describedby')).toContain('-cable-refused');
    expect(screen.getByTestId('binding-field-refused').textContent).toBe(E.fieldNoteShared);
    // The bindings overview says the same sentence, not the raw code.
    const listed = screen.getByTestId('overview-refused');
    expect(listed.textContent).toContain('does not yet save the same cable id and core');
    expect(listed.textContent).not.toContain('Refused: core_shared_cable_id');
    const go = within(error).getByTestId('conflict-edit');
    expect(go.textContent).toBe(E.goField);
    fireEvent.click(go);
    expect(document.activeElement).toBe(cable);
  });

  it('a refused preview is explained the same way, linked to the field, and cleared by an edit', async () => {
    const validate = vi.fn<Validate>(() => Promise.resolve(refused(SHARED)));
    renderApp(<CablePanel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('binding-preview-run'));
    await act(async () => {
      await tick();
    });
    const said = screen.getByTestId('binding-preview-refused').textContent;
    expect(said).toContain('The preview could not be composed: binding bind-1 on board MB-1.');
    expect(said).not.toContain('Fix what is marked');
    expect(screen.getByTestId('binding-preview-refused-code').textContent).toContain(
      'core_shared_cable_id',
    );
    const cable = screen.getByTestId<HTMLSelectElement>('binding-cable');
    expect(screen.getByTestId('binding-field-refused').textContent).toBe(E.fieldNoteShared);
    fireEvent.click(screen.getByTestId('binding-preview-refused-go'));
    expect(document.activeElement).toBe(cable);
    // An edit makes the refusal outdated: the field is no longer marked as refused.
    fireEvent.change(screen.getAllByTestId('point-marking')[0] as HTMLElement, {
      target: { value: '9' },
    });
    expect(screen.queryByTestId('binding-field-refused')).toBeNull();
    expect(screen.getByTestId('binding-preview-outdated')).toBeTruthy();
  });

  it('an unknown refusal gets the generic sentence with its code', () => {
    renderApp(
      <CablePanel
        validateImpl={vi.fn()}
        externalRefusal={refused({ refusal: 'brand_new_code', binding: 'bind-1' })}
      />,
      { locale: 'en', theme: 'light' },
    );
    const error = screen.getByTestId('configuration-error');
    expect(error.textContent).toContain(E.unknown);
    expect(screen.getByTestId('configuration-error-code').textContent).toContain('brand_new_code');
    // No field named: the binding is shown, not a guessed field.
    expect(within(error).getByTestId('conflict-edit').textContent).not.toBe(E.goField);
    expect(screen.queryByTestId('binding-field-refused')).toBeNull();
  });

  it('says it in Arabic from the code, not from the server sentence', () => {
    renderApp(<CablePanel validateImpl={vi.fn()} externalRefusal={refused(SHARED)} />, {
      locale: 'ar',
      theme: 'light',
    });
    const text = screen.getByTestId('configuration-error').textContent;
    expect(text).toContain('لا يحفظ هذا النشر');
    expect(text).not.toContain('deployment');
  });

  it('a moved reference says old and new targets, the choices, and the wire-details warning', () => {
    const params = {
      refusal: 'reference_moved',
      binding: 'bind-1',
      field: 'cable',
      key: '',
      ref: 'c1-cable',
      before: 'Lighting',
      after: 'Heater',
      before_board: 'MB-1',
      after_board: 'MB-1',
      before_circuit: 'c1',
      after_circuit: 'c1',
      token: 'tttttttttttttttttttttttt',
      moved_count: '1',
      r0_binding: 'bind-1',
      r0_field: 'cable',
      r0_key: '',
      r0_id: 'c1-cable',
      r0_token: 'tttttttttttttttttttttttt',
      r0_before: 'Lighting',
      r0_after: 'Heater',
      r0_before_board: 'MB-1',
      r0_after_board: 'MB-1',
      r0_before_circuit: 'c1',
      r0_after_circuit: 'c1',
    };
    renderApp(
      <CablePanel
        validateImpl={vi.fn()}
        externalRefusal={{ kind: 'refused', detail: 'x', code: 'configuration_refused', params }}
      />,
      { locale: 'en', theme: 'light' },
    );
    const error = screen.getByTestId('configuration-error').textContent;
    expect(error).toContain('it named “Lighting”, now “Heater”');
    expect(error).toContain('not checked for the new load');
    expect(screen.getByTestId('binding-moved-keep').textContent).toContain('Keep c1-cable');
    expect(screen.getByTestId('binding-moved-means').textContent).toContain(
      'instead of “Lighting” (c1 on MB-1)',
    );
  });

  it('a refused choice (core out of range) marks the cable invalid', () => {
    renderApp(
      <CablePanel
        validateImpl={vi.fn()}
        externalRefusal={refused({
          refusal: 'core_out_of_range',
          binding: 'bind-1',
          cable: 'c1-cable',
          core: '7',
        })}
      />,
      { locale: 'en', theme: 'light' },
    );
    const cable = screen.getByTestId<HTMLSelectElement>('binding-cable');
    expect(cable.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByTestId('binding-field-refused').textContent).toBe(E.fieldNote);
    expect(screen.getByTestId('configuration-error').textContent).toContain(
      'Cable c1-cable has no core 7.',
    );
  });

  it('a preview refused for a reason naming no entry does not point at one', async () => {
    const validate = vi.fn<Validate>(() =>
      Promise.resolve({ kind: 'refused', detail: 'x', code: 'rate_limited', params: {} }),
    );
    renderApp(<CablePanel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('binding-preview-run'));
    await act(async () => {
      await tick();
    });
    const said = screen.getByTestId('binding-preview-refused').textContent;
    expect(said).toContain(E.unknownNoEntry);
    expect(said).not.toContain("this entry's choices");
    expect(screen.queryByTestId('binding-preview-refused-go')).toBeNull();
  });
});

describe('announcements', () => {
  it("a save refusal from the page's own Save is not announced again by this panel", () => {
    renderApp(<CablePanel validateImpl={vi.fn()} externalRefusal={refused(SHARED)} />, {
      locale: 'en',
      theme: 'light',
    });
    // The Overview already announced it (role=alert there); here it is shown, not re-announced.
    expect(screen.getByTestId('configuration-error').getAttribute('role')).toBeNull();
  });

  it("the panel's own validation refusal is announced", async () => {
    const validate = vi.fn<Validate>(() => Promise.resolve(refused(SHARED)));
    renderApp(<CablePanel validateImpl={validate} />, { locale: 'en', theme: 'light' });
    fireEvent.click(screen.getByTestId('configuration-validate'));
    await act(async () => {
      await tick();
    });
    expect(screen.getByTestId('configuration-error').getAttribute('role')).toBe('alert');
  });
});

describe('refusalSentence', () => {
  it('names a non-binding entry and explains its code', () => {
    const t = (key: string, values: Record<string, string> = {}) => {
      let node: unknown = en.configuration;
      for (const part of key.split('.')) node = (node as Record<string, unknown>)[part];
      return Object.entries(values).reduce(
        (text, [name, value]) => text.replaceAll(`{${name}}`, value),
        node as string,
      );
    };
    const sentence = refusalSentence(
      t,
      explainRefusal({ refusal: 'duplicate_marking', strip: 'XP', point: '7' }),
      {},
      'save',
      'terminal strip XP',
    );
    expect(sentence).toBe(
      'Not saved: terminal strip XP. Two points on strip XP have the same marking (7). Give each point its own marking.',
    );
  });
});
