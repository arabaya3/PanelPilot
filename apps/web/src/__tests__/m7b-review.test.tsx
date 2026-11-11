import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { ConfigurationPanel, configurationPayload } from '@/components/design/configuration-panel';
import type * as DesignModule from '@/lib/design';
import type {
  listProjects,
  openProject,
  reviseProject,
  SavedProject,
  SavedRequest,
  validateConfiguration,
} from '@/lib/design';
import en from '@/messages/en.json';

import { renderApp } from './helpers';

/**
 * Adversarial review of M7b. Each test failed against 6629eb6 when written.
 * SYNTHETIC data: refs and markings are illustrative labels only.
 */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  validate: vi.fn<typeof validateConfiguration>(),
  revise: vi.fn<typeof reviseProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return {
    ...actual,
    listProjects: api.list,
    openProject: api.open,
    validateConfiguration: api.validate,
    reviseProject: api.revise,
  };
});

beforeEach(() => {
  api.list.mockReset();
  api.open.mockReset();
  api.validate.mockReset();
  api.revise.mockReset();
});

const OLDER = {
  name: 'older synthetic',
  templates: [],
  patterns: [],
  strips: [
    {
      id: 'X1',
      board: 'MDB',
      designation: { function: null, location: null, product: 'X1' },
      template: null,
    },
  ],
  components: [],
  bindings: [],
};

const SAVED_REQUEST = {
  info: { name: 'Stored tower', number: '', customer: '', consultant: '', contractor: '' },
  boards: [
    {
      name: 'MDB',
      location: null,
      fed_from: null,
      feeder_length_m: null,
      supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
      loads: [
        {
          description: 'Lights',
          load: 'lighting',
          power_kw: '1',
          phases: 1,
          power_factor: null,
          controlled: false,
          starter: null,
          length_m: null,
        },
      ],
    },
  ],
  profile: null,
} as unknown as SavedRequest;

function revisionEntry(number: number) {
  return {
    number,
    note: '',
    author: 'e',
    approver_account_recorded: false,
    created_at: '2026-11-10T10:00:00Z',
    validated: true,
    export_status: 'draft',
  };
}

/** Revision 1 of 2 opened: its own stored configuration differs from revision 2's. */
function olderRevision(): SavedProject {
  return {
    id: 'p1',
    name: 'Stored tower',
    revision: 1,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision'],
    revisions: [revisionEntry(1), revisionEntry(2)],
    request: SAVED_REQUEST,
    findings: [],
    model_v2: null,
    snapshot_schema: 'pp-design/2',
    configuration: OLDER,
    configuration_status: 'stored',
  } as unknown as SavedProject;
}

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

describe('saving from an older revision', () => {
  it('carries the opened revision’s own configuration, not the latest one', async () => {
    // The server carries the LATEST revision's configuration when none is
    // sent (contract §3). Saving from revision 1 must therefore send revision
    // 1's configuration explicitly (§3: "saving from it carries that one").
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: ['approve_revision'],
        projects: [{ id: 'p1', name: 'Stored tower', revisions: 2, updated_at: 'now' }],
      },
    } as never);
    api.open.mockResolvedValue({ kind: 'saved', project: olderRevision() });
    api.revise.mockResolvedValue({ kind: 'saved', project: olderRevision() });
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        marketsImpl={vi.fn().mockResolvedValue(null)}
        saveImpl={vi.fn()}
      />,
    );
    const listed = await screen.findByTestId('project-p1');
    fireEvent.click(within(listed).getByText('Open'));
    const config = within(screen.getByTestId('view-configuration'));
    await waitFor(() => {
      expect(config.getByTestId<HTMLInputElement>('strip-designation').value).toBe('X1');
    });
    // A form edit only (the project name): the configuration is not edited.
    fireEvent.change(within(screen.getByTestId('view-overview')).getByLabelText('Project name'), {
      target: { value: 'Stored tower B' },
    });
    fireEvent.click(
      within(screen.getByTestId('view-overview')).getByRole('button', {
        name: en.design.projects.saveRevision,
      }),
    );
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalled();
    });
    const sent = api.revise.mock.calls[0]?.[0];
    expect(sent?.configuration).toEqual(OLDER);
  });
});

function Harness() {
  const [value, setValue] = useState<Record<string, unknown>>({});
  const [drop, setDrop] = useState(false);
  return (
    <ConfigurationPanel
      token="t"
      projectId="p1"
      latestRevision={3}
      value={value}
      onChange={setValue}
      status="not_recoverable"
      dropUnrecoverable={drop}
      onDropUnrecoverable={setDrop}
      boards={['MDB']}
      model={null}
      onUnauthorized={vi.fn()}
      validateImpl={api.validate}
    />
  );
}

describe('a configuration that cannot be carried', () => {
  it('can be removed explicitly instead of every save being refused', () => {
    // The removal is an explicit choice the notice offers, never sent
    // silently; the project's Save then sends it (configurationPayload).
    renderApp(<Harness />);
    expect(configurationPayload({}, 'not_recoverable')).toEqual({});
    fireEvent.click(screen.getByTestId('configuration-drop-unrecoverable'));
    expect(screen.getByTestId<HTMLInputElement>('configuration-drop-unrecoverable').checked).toBe(
      true,
    );
    expect(configurationPayload({}, 'not_recoverable', true)).toEqual({
      configurationRemoved: true,
    });
  });
});
