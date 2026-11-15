import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfigurationPanel } from '@/components/design/configuration-panel';
import { directPointModelId, type LinkEndUse } from '@/components/design/definition-model';
import type { V2ModelSummary } from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * M8 accessibility (independent a11y review): an added or removed terminal is
 * announced and focus stays in the definition; a strip point's empty marking
 * and its blocked removal give their reasons to assistive technology.
 * SYNTHETIC data.
 */

const MODEL = {
  templates: [],
  components: [],
  strips: [],
  terminals: [],
  nets: [],
  connections: [],
  wires: [],
  findings: [],
} as unknown as V2ModelSummary;

const DEFINITION = {
  ref: 'tenant/synth-a11y/v1',
  function: 'unspecified',
  terminals: [
    { id: 't-001-aaaaaaaa', marking: 'L1', kind: 'unspecified', required: null, group: null },
  ],
  contact_groups: [],
  parameters: [],
  restricted_scope: [],
  provenance: { source: null, author: 'user-entered', created: '2026-11-15' },
  review: {
    status: 'unreviewed',
    reviewer: null,
    reviewed_at: null,
    standards: [],
    scope_note: null,
  },
};

const STRIP = {
  id: 'X1',
  board: 'MDB',
  designation: { function: null, location: null, product: 'X1' },
  template: null,
  points: [
    { id: 'p1', marking: '', level: null },
    { id: 'p2', marking: '2', level: null },
  ],
};

function Harness({
  initial,
  linkUses = [],
}: {
  initial: Record<string, unknown>;
  linkUses?: LinkEndUse[];
}) {
  const [value, setValue] = useState<Record<string, unknown>>(initial);
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={1}
      value={value}
      onChange={setValue}
      status="none"
      boards={['MDB']}
      model={MODEL}
      onUnauthorized={vi.fn()}
      validateImpl={vi.fn()}
      linkUses={linkUses}
    />
  );
}

const defs = en.configuration.definitions;

function describedText(element: HTMLElement): string {
  const ids = (element.getAttribute('aria-describedby') ?? '').split(' ').filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? '').join(' ');
}

describe('M8 definitions: announcements and focus', () => {
  it('announces an added terminal politely', () => {
    renderApp(<Harness initial={{ templates: [DEFINITION] }} />, { locale: 'en', theme: 'light' });
    const row = screen.getByTestId('definition-entry');
    const status = within(row).getByTestId('definition-status');
    expect(status.getAttribute('role')).toBe('status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toBe('');
    const input = within(row).getByTestId('definition-new-terminal');
    fireEvent.change(input, { target: { value: 'L2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(status.textContent).toBe(defs.terminalAdded.replace('{marking}', 'L2'));
  });

  it('announces a removed terminal and keeps focus in the definition', () => {
    renderApp(<Harness initial={{ templates: [DEFINITION] }} />, { locale: 'en', theme: 'light' });
    const row = screen.getByTestId('definition-entry');
    const remove = within(row).getByTestId('definition-terminal-remove');
    remove.focus();
    fireEvent.click(remove);
    expect(within(row).queryAllByTestId('definition-terminal')).toHaveLength(0);
    expect(within(row).getByTestId('definition-status').textContent).toBe(
      defs.terminalRemoved.replace('{marking}', 'L1'),
    );
    expect(document.activeElement).toBe(within(row).getByTestId('definition-new-terminal'));
  });
});

describe('M8 strip points: reasons tied to the controls', () => {
  it('links an empty point marking to its error text', () => {
    renderApp(<Harness initial={{ strips: [STRIP] }} />, { locale: 'en', theme: 'light' });
    const [empty, filled] = screen.getAllByTestId('strip-point-marking');
    expect(empty?.getAttribute('aria-invalid')).toBe('true');
    expect(describedText(empty as HTMLElement)).toBe(defs.pointMarkingEmpty);
    expect(filled?.hasAttribute('aria-describedby')).toBe(false);
  });

  it('links a blocked point removal to the link that uses it', () => {
    const uses: LinkEndUse[] = [
      {
        fedBoard: 'DB1',
        role: 'from',
        terminal: { point: directPointModelId('X1', 'p2'), side: 'a' },
      },
    ];
    renderApp(<Harness initial={{ strips: [STRIP] }} linkUses={uses} />, {
      locale: 'en',
      theme: 'light',
    });
    const [free, used] = screen.getAllByTestId('strip-point-remove');
    expect(free?.hasAttribute('disabled')).toBe(false);
    expect(free?.hasAttribute('aria-describedby')).toBe(false);
    expect(used?.hasAttribute('disabled')).toBe(true);
    expect(describedText(used as HTMLElement)).toBe(
      screen.getByTestId('strip-point-linked').textContent,
    );
    expect(describedText(used as HTMLElement)).toContain('DB1');
  });
});
