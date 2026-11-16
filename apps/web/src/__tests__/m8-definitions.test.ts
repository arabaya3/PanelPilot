import { describe, expect, it } from 'vitest';

import {
  addDefinition,
  addDefinitionTerminal,
  componentLinkUses,
  definitionIdProblem,
  definitionProblems,
  definitionsOf,
  definitionUsers,
  removeDefinition,
  removeDefinitionTerminal,
  renameDefinition,
  terminalKindChoices,
  terminalLinkUses,
  terminalPatternUses,
  updateDefinition,
  updateDefinitionTerminal,
  type LinkEndUse,
} from '@/components/design/definition-model';
import { emptyConfiguration, type Configuration } from '@/components/design/configuration-model';
import {
  configurationTerminalOptions,
  linkTerminalOptions,
  linkUsesOf,
} from '@/components/design/feeder-link-model';
import type { V2ModelSummary } from '@/lib/design';
import { deriveId } from '@/lib/sha256';

/** M8: definitions entered in the guided editor. SYNTHETIC data. */

const REF = 'tenant/synth-isolator/v1';

function defined(): Configuration {
  let doc = addDefinition(emptyConfiguration(), { id: 'synth-isolator', function: 'unspecified' });
  doc = addDefinitionTerminal(doc, REF, { marking: 'L1' });
  doc = addDefinitionTerminal(doc, REF, { marking: 'L2' });
  return {
    ...doc,
    components: [
      {
        id: 'component-a1b2c3',
        board: 'MB-1',
        template: REF,
        designation: { function: null, location: null, product: 'Q9' },
      },
    ],
  };
}

describe('a definition entered by the user', () => {
  it('infers nothing and says it is unreviewed', () => {
    const doc = defined();
    const [template] = doc.templates as Record<string, unknown>[];
    expect(template).toMatchObject({
      ref: REF,
      function: 'unspecified',
      contact_groups: [],
      parameters: [],
      restricted_scope: [],
      provenance: { source: null, author: 'user-entered' },
      review: { status: 'unreviewed', reviewer: null, reviewed_at: null, standards: [] },
    });
    expect(template).not.toHaveProperty('symbol');
    const terminals = template?.terminals as Record<string, unknown>[];
    expect(terminals.map((terminal) => terminal.marking)).toEqual(['L1', 'L2']);
    for (const terminal of terminals) {
      expect(terminal).toMatchObject({ kind: 'unspecified', required: null, group: null });
    }
    // Exactly the composer's template keys: an unknown key would be refused.
    expect(Object.keys(template ?? {}).sort()).toEqual(
      [
        'contact_groups',
        'function',
        'parameters',
        'provenance',
        'ref',
        'restricted_scope',
        'review',
        'terminals',
      ].sort(),
    );
  });

  it('keeps terminal ids in entry order and never reissues a removed one', () => {
    let doc = defined();
    const ids = () => definitionsOf(doc)[0]?.terminals.map((terminal) => terminal.id) ?? [];
    const [first, second] = ids();
    expect([...ids()].sort()).toEqual(ids());
    doc = removeDefinitionTerminal(doc, REF, second as string);
    doc = addDefinitionTerminal(doc, REF, { marking: 'L3' });
    const [, third] = ids();
    expect(third).not.toBe(second);
    expect(third).not.toBe(first);
    expect([...ids()].sort()).toEqual(ids());
  });

  it('offers kinds and functions only from data already present, else unspecified', () => {
    const doc = defined();
    expect(terminalKindChoices(doc)).toEqual(['unspecified']);
    const withKind = updateDefinitionTerminal(
      doc,
      REF,
      definitionsOf(doc)[0]?.terminals[0]?.id as string,
      { kind: 'power_line' },
    );
    expect(terminalKindChoices(withKind)).toEqual(['unspecified', 'power_line']);
  });

  it('records a source only when one is typed, and a blank source is none', () => {
    const doc = updateDefinition(defined(), REF, { source: 'Synthetic datasheet S-1' });
    expect(definitionsOf(doc)[0]?.provenance.source).toBe('Synthetic datasheet S-1');
    expect(definitionsOf(updateDefinition(doc, REF, { source: '  ' }))[0]?.provenance.source).toBe(
      null,
    );
  });

  it('says why an id cannot be used, beside the field', () => {
    const doc = defined();
    expect(definitionIdProblem(doc, '')).toBe('empty');
    expect(definitionIdProblem(doc, 'Synth Isolator')).toBe('invalid');
    expect(definitionIdProblem(doc, 'مكوّن')).toBe('invalid');
    expect(definitionIdProblem(doc, 'synth-isolator')).toBe('taken');
    expect(definitionIdProblem(doc, 'synth-isolator', REF)).toBe(null);
    expect(definitionIdProblem(doc, 'synth-2')).toBe(null);
    // A refused id adds nothing.
    expect(addDefinition(doc, { id: 'synth-isolator', function: 'unspecified' })).toBe(doc);
  });

  it('flags an empty or repeated marking per terminal (the server would refuse them)', () => {
    const doc = defined();
    const [first, second] = definitionsOf(doc)[0]?.terminals ?? [];
    const repeated = updateDefinitionTerminal(doc, REF, second?.id as string, { marking: 'L1' });
    const blank = updateDefinitionTerminal(doc, REF, first?.id as string, { marking: ' ' });
    expect(definitionProblems(definitionsOf(repeated)[0] as never).terminals).toEqual({
      [first?.id as string]: 'duplicate',
      [second?.id as string]: 'duplicate',
    });
    expect(definitionProblems(definitionsOf(blank)[0] as never).terminals).toEqual({
      [first?.id as string]: 'empty',
    });
  });
});

describe('references to a definition', () => {
  it('refuses removal while an entry uses it, and names the users', () => {
    const doc = defined();
    expect(definitionUsers(doc, REF)).toEqual([{ kind: 'component', id: 'component-a1b2c3' }]);
    expect(removeDefinition(doc, REF)).toBe(doc);
    const unused = { ...doc, components: [] };
    expect(definitionsOf(removeDefinition(unused, REF))).toEqual([]);
  });

  it('a rename points every user at the new ref in the same step, keeping the version', () => {
    const doc = renameDefinition(defined(), REF, 'synth-isolator-b');
    expect(definitionsOf(doc).map((definition) => definition.ref)).toEqual([
      'tenant/synth-isolator-b/v1',
    ]);
    expect((doc.components as Record<string, unknown>[])[0]?.template).toBe(
      'tenant/synth-isolator-b/v1',
    );
    // An unusable id renames nothing.
    const original = defined();
    expect(renameDefinition(original, REF, 'Bad Id')).toBe(original);
  });

  it('counts a pattern slot on the definition as a user, repoints it on rename, and guards its terminals', () => {
    const doc = defined();
    const terminal = definitionsOf(doc)[0]?.terminals[0]?.id as string;
    const withPattern = {
      ...doc,
      components: [],
      patterns: [
        {
          ref: 'tenant/feed/v1',
          slots: [{ id: 'iso', template: REF, device_kind: null }],
          nets: [{ role: 'L1', members: [{ slot: 'iso', terminal }] }],
        },
      ],
    };
    expect(definitionUsers(withPattern, REF)).toEqual([{ kind: 'pattern', id: 'tenant/feed/v1' }]);
    expect(removeDefinition(withPattern, REF)).toBe(withPattern);
    expect(terminalPatternUses(withPattern, REF, terminal)).toEqual(['tenant/feed/v1']);
    const renamed = renameDefinition(withPattern, REF, 'synth-b');
    const slots = (renamed.patterns as Array<{ slots: Array<{ template: string }> }>)[0]?.slots;
    expect(slots?.[0]?.template).toBe('tenant/synth-b/v1');
  });

  it('finds the feeder-link ends that use a terminal or a component', () => {
    const doc = defined();
    const terminal = definitionsOf(doc)[0]?.terminals[0]?.id as string;
    const uses: LinkEndUse[] = [
      {
        fedBoard: 'SB-1',
        role: 'from',
        terminal: { component: 'k-ac9decc4a2bb0df9fccdd809', terminal },
      },
    ];
    expect(terminalLinkUses(doc, REF, terminal, uses)).toEqual(uses);
    expect(componentLinkUses('component-a1b2c3', uses)).toEqual(uses);
    expect(componentLinkUses('component-other', uses)).toEqual([]);
  });

  it('reads the ends the boards link forms hold', () => {
    const terminal = JSON.stringify({ component: 'k-1', terminal: 't-001-aa' });
    expect(
      linkUsesOf([
        { name: 'MB-1' },
        {
          name: 'SB-1',
          link: {
            from: { terminal, label: '' },
            to: { terminal: '', label: 'x' },
            attributes: {} as never,
          },
        },
      ]),
    ).toEqual([
      { fedBoard: 'SB-1', role: 'from', terminal: { component: 'k-1', terminal: 't-001-aa' } },
    ]);
  });
});

describe('feeder-link choices from the configuration being edited', () => {
  it('offers a defined terminal on its own board by the id the server derives, before saving', () => {
    const options = configurationTerminalOptions(defined(), 'MB-1');
    expect(options.map((option) => option.text)).toEqual(['Q9:L1 — MB-1', 'Q9:L2 — MB-1']);
    const first = JSON.parse(options[0]?.value ?? '{}') as Record<string, string>;
    // The value computed with the API for component id "component-a1b2c3".
    expect(first.component).toBe('k-ac9decc4a2bb0df9fccdd809');
    expect(configurationTerminalOptions(defined(), 'SB-1')).toEqual([]);
  });

  it('skips a terminal without a marking', () => {
    const doc = addDefinitionTerminal(defined(), REF, { marking: '' });
    expect(configurationTerminalOptions(doc, 'MB-1')).toHaveLength(2);
  });

  it('drops a saved configuration component the user removed, keeps the rest of the model', () => {
    const model = {
      terminals: [
        {
          board_id: 'MB-1',
          owner_kind: 'component',
          owner_id: 'k-gone0000000000000000000',
          designation: 'Q7',
          terminal_id: 't-001',
          marking: 'A',
          side: null,
        },
        {
          board_id: 'MB-1',
          owner_kind: 'component',
          owner_id: 'c-legacy0000000000000000',
          designation: 'Q1',
          terminal_id: 'x',
          marking: 'X',
          side: null,
        },
      ],
      definitions: [],
    } as unknown as V2ModelSummary;
    const texts = linkTerminalOptions(model, defined(), 'MB-1').map((option) => option.text);
    expect(texts).toEqual(['Q9:L1 — MB-1', 'Q9:L2 — MB-1', 'Q1:X — MB-1']);
    // With no configuration entries, a saved configuration component is gone too;
    // a legacy part outside the configuration stays.
    expect(
      linkTerminalOptions(model, emptyConfiguration(), 'MB-1').map((option) => option.text),
    ).toEqual(['Q1:X — MB-1']);
    // Without a configuration at all (no project opened), the saved model as before.
    expect(linkTerminalOptions(model, null, 'MB-1').map((option) => option.text)).toEqual([
      'Q7:A — MB-1',
      'Q1:X — MB-1',
    ]);
  });

  it('does not offer a saved terminal or direct point the user has since removed', () => {
    const doc = defined();
    const kept = definitionsOf(doc)[0]?.terminals[1]?.id as string;
    const removed = definitionsOf(doc)[0]?.terminals[0]?.id as string;
    const edited = removeDefinitionTerminal(
      {
        ...doc,
        strips: [
          {
            id: 'strip-x',
            board: 'MB-1',
            template: null,
            designation: { function: null, location: null, product: 'X1' },
            points: [{ id: 'p-keep', marking: '1' }],
          },
        ],
        patterns: [
          {
            ref: 'tenant/feed/v1',
            strips: ['out'],
            points: [{ id: 'pt', strip: 'out', offset: 1, level: 1, kind: 'power_line' }],
          },
        ],
        bindings: [
          { id: 'b1', pattern: 'tenant/feed/v1', board: 'MB-1', strips: { out: 'strip-x' } },
        ],
      },
      REF,
      removed,
    );
    const owner = 'k-ac9decc4a2bb0df9fccdd809';
    const strip = 's-543c1421a9e637a626173c00';
    const row = (extra: Record<string, unknown>) => ({
      board_id: 'MB-1',
      designation: 'Q9',
      side: null,
      pattern_refs: [],
      ...extra,
    });
    const model = {
      definitions: [],
      terminals: [
        row({ owner_kind: 'component', owner_id: owner, terminal_id: removed, marking: 'L1' }),
        row({ owner_kind: 'component', owner_id: owner, terminal_id: kept, marking: 'L2' }),
        // A point removed from strip-x since the save (entered directly).
        row({
          owner_kind: 'strip',
          owner_id: deriveStrip(),
          terminal_id: 'p-removed',
          marking: '9',
          side: 'inner',
          designation: 'X1',
        }),
        // A point binding b1 placed on strip-x: stays while the strip and b1 do.
        row({
          owner_kind: 'strip',
          owner_id: deriveStrip(),
          terminal_id: deriveId('p', 'strip-x', 'b1', 'pt'),
          marking: '7',
          side: 'inner',
          designation: 'X1',
          pattern_refs: ['tenant/feed/v1'],
        }),
        // A pattern point no binding in the configuration places any more.
        row({
          owner_kind: 'strip',
          owner_id: deriveStrip(),
          terminal_id: 'p-pattern-orphan',
          marking: '6',
          side: 'inner',
          designation: 'X1',
          pattern_refs: ['tenant/feed/v1'],
        }),
      ],
    } as unknown as V2ModelSummary;
    function deriveStrip() {
      return strip;
    }
    const texts = linkTerminalOptions(model, edited, 'MB-1').map((option) => option.text);
    expect(texts).toEqual([
      'Q9:L2 — MB-1',
      'X1:1 (inner) — MB-1',
      'X1:1 (outer) — MB-1',
      'X1:7 (inner) — MB-1',
    ]);
  });
});
