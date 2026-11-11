import { fireEvent, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { ConfigurationPanel } from '@/components/design/configuration-panel';
import { duplicateEntries } from '@/components/design/configuration-model';
import type { DesignFinding } from '@/lib/design';

import { renderApp } from './helpers';

/**
 * A `designation_duplicate` finding (blocking approval) shown where the
 * configuration is edited, with a way to the entry to change. SYNTHETIC data.
 */

const DOC = {
  templates: [],
  patterns: [],
  components: [
    {
      id: 'component-a',
      board: 'MB-1',
      template: 'global/legacy-switch_disconnector',
      designation: { function: null, location: null, product: 'Q1' },
    },
    {
      id: 'component-b',
      board: 'SB-1',
      template: 'global/legacy-switch_disconnector',
      designation: { function: null, location: null, product: 'Q1' },
    },
  ],
  strips: [],
  bindings: [],
};

const CLASH: DesignFinding = {
  code: 'designation_duplicate',
  severity: 'blocking',
  board_id: 'MB-1',
  values: { board_id: 'MB-1', designation: '-Q1 / =MB-1-Q1', parts: 'c-derived,c-configured' },
  message: '-Q1 / =MB-1-Q1 is printed on 2 parts of board MB-1',
  requires_engineering_confirmation: false,
  source: 'structural',
} as unknown as DesignFinding;

function Harness({ findings }: { findings: DesignFinding[] }) {
  const [value, setValue] = useState<Record<string, unknown>>(DOC);
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={5}
      value={value}
      onChange={setValue}
      status="stored"
      boards={['MB-1', 'SB-1']}
      model={null}
      onUnauthorized={vi.fn()}
      findings={findings}
      findingsRevision={5}
    />
  );
}

describe('duplicate designations on the configuration tab', () => {
  it('names the clash, its board and revision, and leads to the entry on that board only', () => {
    renderApp(<Harness findings={[CLASH]} />);
    const notice = within(screen.getByTestId('configuration-duplicates'));
    const item = within(notice.getByTestId('configuration-duplicate'));
    expect(item.getByText(/-Q1 \/ =MB-1-Q1 is printed on 2 parts of board MB-1/)).toBeTruthy();
    expect(notice.getByText(/saved revision 5/)).toBeTruthy();
    // The derived part is not an entry here: one button, for MB-1's Q1 (not SB-1's).
    const edits = item.getAllByTestId('duplicate-edit');
    expect(edits).toHaveLength(1);
    expect(edits[0]?.textContent).toContain('Q1');
    expect(item.getByText(/comes from the board form/)).toBeTruthy();
    fireEvent.click(edits[0] as HTMLElement);
    expect(document.activeElement?.id).toContain('component-component-a');
  });

  it('shows nothing without such a finding', () => {
    renderApp(<Harness findings={[{ ...CLASH, code: 'wire_number_duplicate' }]} />);
    expect(screen.queryByTestId('configuration-duplicates')).toBeNull();
  });

  it('matches printed forms on their own board, with or without the function aspect', () => {
    expect(duplicateEntries(DOC, 'MB-1', '-Q1 / =MB-1-Q1')).toEqual([
      { kind: 'component', id: 'component-a' },
    ]);
    expect(duplicateEntries(DOC, 'MB-1', '=MB-1-Q1')).toEqual([
      { kind: 'component', id: 'component-a' },
    ]);
    expect(duplicateEntries(DOC, 'MB-1', '-Q2')).toEqual([]);
  });
});
