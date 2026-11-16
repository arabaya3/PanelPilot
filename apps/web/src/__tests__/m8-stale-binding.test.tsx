import { fireEvent, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';

import type { Configuration } from '@/components/design/configuration-model';
import {
  addDefinition,
  addDefinitionTerminal,
  definitionsOf,
  sequenceKey,
  terminalSequence,
} from '@/components/design/definition-model';
import { FeederLinkForm } from '@/components/design/feeder-link';
import {
  blankLink,
  encodeTerminal,
  linkTerminalOptions,
  type LinkForm,
} from '@/components/design/feeder-link-model';
import type { V2ModelSummary } from '@/lib/design';
import { deriveId } from '@/lib/sha256';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * M8: a point a pattern binding placed is offered as a feeder-link end only
 * while that binding is in the configuration; a chosen end whose binding was
 * removed is kept and said, never cleared or replaced. Terminal ids keep their
 * entry order past 999. SYNTHETIC data.
 */

const PATTERN = 'tenant/feed/v1';
const STRIP_MODEL_ID = deriveId('s', 'strip-x');
const BOUND = deriveId('p', 'strip-x', 'b1', 'pt-a');
const ORPHAN = deriveId('p', 'strip-x', 'b-gone', 'pt-a');

function config(withBinding: boolean): Configuration {
  return {
    templates: [],
    components: [],
    strips: [
      {
        id: 'strip-x',
        board: 'MB-1',
        template: null,
        designation: { function: null, location: null, product: 'X1' },
      },
    ],
    patterns: [
      {
        ref: PATTERN,
        slots: [],
        strips: ['out'],
        points: [{ id: 'pt-a', strip: 'out', offset: 1, level: 1, kind: 'power_line' }],
        nets: [],
      },
    ],
    bindings: withBinding
      ? [
          {
            id: 'b1',
            pattern: PATTERN,
            board: 'MB-1',
            circuit: 'c1',
            slots: {},
            strips: { out: 'strip-x' },
            markings: { 'pt-a': '7' },
            wires: {},
          },
        ]
      : [],
  };
}

function point(id: string, marking: string) {
  return {
    owner_kind: 'strip',
    owner_id: STRIP_MODEL_ID,
    terminal_id: id,
    marking,
    side: 'inner',
    board_id: 'MB-1',
    designation: 'X1',
    pattern_refs: [PATTERN],
  };
}

// The saved model: b1's point, and a point from a binding removed before this edit.
const MODEL = {
  definitions: [],
  terminals: [point(BOUND, '7'), point(ORPHAN, '8')],
} as unknown as V2ModelSummary;

const texts = (doc: Configuration) =>
  linkTerminalOptions(MODEL, doc, 'MB-1').map((option) => option.text);

describe('pattern points follow the bindings in the configuration', () => {
  it('does not offer a point whose binding is no longer in the configuration', () => {
    expect(texts(config(true))).toEqual(['X1:7 (inner) — MB-1']);
  });

  it('drops every point of a binding as soon as the binding is removed', () => {
    expect(texts(config(false))).toEqual([]);
  });

  it('still offers the saved model unchanged when no configuration is edited', () => {
    expect(linkTerminalOptions(MODEL, null, 'MB-1')).toHaveLength(2);
  });
});

function LinkHarness({ doc, initial }: { doc: Configuration; initial: LinkForm }) {
  const [value, setValue] = useState<LinkForm | undefined>(initial);
  return (
    <>
      <FeederLinkForm
        supplyBoard="MB-1"
        fedBoard="SB-1"
        value={value}
        onChange={setValue}
        model={MODEL}
        configuration={doc}
        calculated={null}
      />
      <output data-testid="stored-from">{value?.from.terminal ?? ''}</output>
    </>
  );
}

describe('a chosen end whose binding was removed', () => {
  const chosen = encodeTerminal({ point: BOUND, side: 'inner' });
  const initial: LinkForm = { ...blankLink(), from: { terminal: chosen, label: '' } };

  it('is kept, marked invalid with its reason, and not replaced by another end', () => {
    renderApp(<LinkHarness doc={config(false)} initial={initial} />, {
      locale: 'en',
      theme: 'light',
    });
    const select = screen.getByTestId<HTMLSelectElement>('link-from-terminal');
    expect(select.value).toBe(chosen);
    expect(screen.getByTestId('stored-from').textContent).toBe(chosen);
    const offered = Array.from(select.options).map((option) => option.textContent);
    expect(offered).toEqual([
      en.feederLink.terminalNone,
      en.feederLink.terminalGone.replace('{terminal}', 'X1:7 (inner)'),
    ]);
    const note = screen.getByTestId('link-from-terminal-gone');
    expect(note.textContent).toBe(en.feederLink.terminalGoneNote.replace('{board}', 'MB-1'));
    expect(select.getAttribute('aria-invalid')).toBe('true');
    expect(select.getAttribute('aria-describedby')).toBe(note.id);
  });

  it('changes only when the user picks another end', () => {
    renderApp(<LinkHarness doc={config(false)} initial={initial} />, {
      locale: 'en',
      theme: 'light',
    });
    fireEvent.change(screen.getByTestId('link-from-terminal'), { target: { value: '' } });
    expect(screen.getByTestId('stored-from').textContent).toBe('');
    expect(screen.queryByTestId('link-from-terminal-gone')).toBeNull();
  });
});

describe('terminal ids past 999', () => {
  it('sort as plain strings in entry order (the composer sorts by id)', () => {
    let doc = addDefinition(
      { templates: [] },
      {
        id: 'synth-many',
        function: 'unspecified',
      },
    );
    const ref = 'tenant/synth-many/v1';
    for (let i = 1; i <= 1005; i++)
      doc = addDefinitionTerminal(doc, ref, { marking: `T${String(i)}` });
    const terminals = definitionsOf(doc)[0]?.terminals ?? [];
    const sorted = [...terminals].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    expect(sorted.map((terminal) => terminal.marking)).toEqual(
      terminals.map((terminal) => terminal.marking),
    );
    expect(terminals[998]?.id.startsWith('t-999-')).toBe(true);
    expect(terminals[999]?.id.startsWith('t-a1000-')).toBe(true);
  });

  it('keeps the existing three-digit form and widens with a sorting letter', () => {
    expect(sequenceKey(1)).toBe('001');
    expect(sequenceKey(999)).toBe('999');
    expect(sequenceKey(1000)).toBe('a1000');
    expect(sequenceKey(99999)).toBe('b99999');
    expect(sequenceKey(Number.MAX_SAFE_INTEGER)).toBe(`m${String(Number.MAX_SAFE_INTEGER)}`);
    expect(terminalSequence('t-042-0a1b2c3d')).toBe(42);
    expect(terminalSequence('t-a1000-0a1b2c3d')).toBe(1000);
    expect(terminalSequence('t-1000-0a1b2c3d')).toBe(1000);
  });
});
