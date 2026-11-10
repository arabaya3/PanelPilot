import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import { FeederLinkForm, StoredLinks } from '@/components/design/feeder-link';
import {
  decodeTerminal,
  encodeTerminal,
  type CalculatedValues,
  type LinkForm,
  type StoredLink,
} from '@/components/design/feeder-link-model';
import type * as DesignModule from '@/lib/design';
import type {
  designProject,
  listProjects,
  openProject,
  reviseProject,
  SavedProject,
  SavedRequest,
} from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { LINK_MODEL } from './feeder-link-fixtures';
import { renderApp } from './helpers';

/**
 * M7a: the guided board-to-board link form of a feeder, the stored link of a
 * saved revision with its findings, and the workspace showing them only where
 * the deployment writes pp-design/3. All synthetic data; the API is mocked.
 */

const f = en.feederLink;

const CALC: CalculatedValues = {
  cores: '5',
  cross_section_mm2: '16',
  material: 'copper',
  insulation: 'XLPE',
  length_m: '40',
};

function Harness({
  initial,
  calculated = CALC,
  onValue,
  refusal = null,
}: {
  initial?: LinkForm;
  calculated?: CalculatedValues | null;
  onValue?: (value: LinkForm | undefined) => void;
  refusal?: string | null;
}) {
  const [value, setValue] = useState<LinkForm | undefined>(initial);
  return (
    <FeederLinkForm
      supplyBoard="MDB"
      fedBoard="DB1"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue?.(next);
      }}
      model={LINK_MODEL}
      calculated={calculated}
      refusal={refusal}
    />
  );
}

const form = () => within(screen.getByTestId('feeder-link-form'));
const end = (role: 'from' | 'to') => within(screen.getByTestId(`link-end-${role}`));
const optionTexts = (select: HTMLElement) =>
  Array.from((select as HTMLSelectElement).options).map((option) => option.textContent);

describe('the board-to-board link form', () => {
  it('offers each end only the terminals and strip points of its own board', () => {
    renderApp(<Harness />, { locale: 'en' });
    expect(optionTexts(end('from').getByTestId('link-from-terminal'))).toEqual([
      f.terminalNone,
      'Q1:2',
      'X0:1 (outer)',
    ]);
    expect(optionTexts(end('to').getByTestId('link-to-terminal'))).toEqual([
      f.terminalNone,
      'X1:L1 (inner)',
    ]);
    expect(screen.getByTestId('feeder-link-form').textContent).not.toContain('K9');
  });

  it('says an end with nothing entered is "terminal not set", and a label is the user’s, not a modelled terminal', () => {
    let last: LinkForm | undefined;
    renderApp(
      <Harness
        onValue={(value) => {
          last = value;
        }}
      />,
      { locale: 'en' },
    );
    expect(end('from').getByTestId('link-from-not-set').textContent).toBe(f.endNotSet);
    expect(end('to').getByTestId('link-to-not-set').textContent).toBe(f.endNotSet);
    const label = end('to').getByLabelText(f.label);
    expect(label.getAttribute('aria-describedby')).toBe(
      end('to').getByTestId('link-to-label-note').id,
    );
    expect(end('to').getByTestId('link-to-label-note').textContent).toBe(f.labelNote);
    fireEvent.change(label, { target: { value: 'Incomer lugs' } });
    expect(end('to').queryByTestId('link-to-not-set')).toBeNull();
    expect(last?.to).toEqual({ terminal: '', label: 'Incomer lugs' });
    fireEvent.change(end('from').getByTestId('link-from-terminal'), {
      target: { value: encodeTerminal({ point: 'pt-1', side: 'outer' }) },
    });
    expect(decodeTerminal(last?.from.terminal ?? '')).toEqual({ point: 'pt-1', side: 'outer' });
  });

  it('starts every property blank ("not given"), never prefilled from the feeder calculation', () => {
    renderApp(<Harness />, { locale: 'en' });
    for (const key of [
      'cores',
      'cross_section_mm2',
      'insulation',
      'length_m',
      'number',
      'colour',
    ]) {
      const input = form().getByTestId<HTMLInputElement>(`link-input-${key}`);
      expect(input.value).toBe('');
      expect(input.placeholder).toBe(f.notGiven);
    }
    expect(form().getByTestId<HTMLInputElement>('link-input-material').value).toBe('');
  });

  it('shows the calculation’s value read-only beside each field, labelled as used by the feeder calculation', () => {
    renderApp(<Harness />, { locale: 'en' });
    const calc = form().getByTestId('link-calc-cross_section_mm2');
    expect(calc.tagName).toBe('OUTPUT');
    expect(calc.textContent).toBe('Used by the feeder calculation: 16');
    expect(
      form().getByTestId('link-input-cross_section_mm2').getAttribute('aria-describedby'),
    ).toBe(calc.id);
    // Number and colour are not values the calculation has.
    expect(form().queryByTestId('link-calc-number')).toBeNull();
    expect(form().queryByTestId('link-calc-colour')).toBeNull();
  });

  it('flags a differing value with both values, in wording that does not say which is right', () => {
    renderApp(<Harness />, { locale: 'en' });
    const input = form().getByTestId('link-input-cross_section_mm2');
    fireEvent.change(input, { target: { value: '16.0' } });
    expect(form().queryByTestId('link-differs-cross_section_mm2')).toBeNull();
    fireEvent.change(input, { target: { value: '25' } });
    const differs = form().getByTestId('link-differs-cross_section_mm2');
    expect(differs.textContent).toBe('DiffersYour value 25; feeder calculation 16.');
    expect(input.getAttribute('aria-describedby')).toContain(differs.id);
    const wording = `${f.differs} ${f.differsDetail} ${f.calculation}`.toLowerCase();
    for (const word of ['wrong', 'correct', 'error', 'should', 'incorrect', 'right']) {
      expect(wording).not.toContain(word);
    }
    // The calculation's value is still shown, unchanged.
    expect(form().getByTestId('link-calc-cross_section_mm2').textContent).toContain('16');
  });

  it('says when the project is not designed yet instead of inventing calculation values', () => {
    renderApp(<Harness calculated={null} />, { locale: 'en' });
    expect(form().getByTestId('link-calc-not-run').textContent).toBe(f.calculationNotRun);
    expect(form().queryByTestId('link-calc-cores')).toBeNull();
  });

  it('says a value out of range at the field', () => {
    renderApp(<Harness />, { locale: 'en' });
    fireEvent.change(form().getByTestId('link-input-cores'), { target: { value: '0' } });
    expect(screen.getByText(f.problem.cores)).toBeTruthy();
  });

  it('shows a save refusal about the link', () => {
    renderApp(<Harness refusal={en.design.errors.link_end_off_board} />, { locale: 'en' });
    expect(form().getByRole('alert').textContent).toContain(en.design.errors.link_end_off_board);
  });

  it('uses only native, labelled controls in reading order, so the keyboard reaches each one', () => {
    let last: LinkForm | undefined = undefined;
    renderApp(
      <Harness
        onValue={(value) => {
          last = value;
        }}
      />,
      { locale: 'en' },
    );
    const controls = Array.from(
      screen.getByTestId('feeder-link-form').querySelectorAll('input, select, button, [tabindex]'),
    );
    for (const control of controls) {
      expect(['INPUT', 'SELECT', 'BUTTON']).toContain(control.tagName);
      expect(control.getAttribute('tabindex')).toBeNull();
      if (control.tagName !== 'BUTTON') {
        expect(document.querySelector(`label[for="${control.id}"]`)).not.toBeNull();
      }
    }
    expect(controls.slice(0, 4)).toEqual([
      end('from').getByTestId('link-from-terminal'),
      end('from').getByTestId('link-from-label'),
      end('to').getByTestId('link-to-terminal'),
      end('to').getByTestId('link-to-label'),
    ]);
    const label = end('from').getByTestId('link-from-label');
    label.focus();
    expect(document.activeElement).toBe(label);
    fireEvent.change(label, { target: { value: 'Q1 out' } });
    expect(last).toBeDefined();
    fireEvent.click(form().getByTestId('feeder-link-clear'));
    expect(last).toBeUndefined();
    expect(form().queryByTestId('feeder-link-clear')).toBeNull();
  });

  it.each([
    ['ar', ar],
    ['he', he],
  ] as const)('reads in %s, its labels translated', (locale, bundle) => {
    renderApp(<Harness />, { locale });
    expect(screen.getByTestId('feeder-link-form').textContent).toContain(
      bundle.feederLink.labelNote,
    );
    expect(screen.getByTestId('feeder-link-form').textContent).toContain(
      bundle.feederLink.attr.cross_section_mm2,
    );
  });
});

/* ------------------------------------------------------- stored links --- */

const STORED: StoredLink = {
  id: 'l-3f2a',
  origin: 'derived',
  feeder: { supply_board: 'MDB', fed_board: 'DB1', circuit_id: 'c-4' },
  ends: [
    {
      role: 'from',
      board: 'MDB',
      terminal: { component: 'cmp-q1', terminal: 't-2' },
      label: null,
      resolution: 'resolved',
    },
    { role: 'to', board: 'DB1', terminal: null, label: null, resolution: 'unresolved' },
  ],
  carrier: { kind: 'cable', cable_id: 'w2' },
  attributes: {
    cores: { value: 4, source: 'user' },
    cross_section_mm2: { value: '25', source: 'user' },
    material: { value: null, source: 'unset' },
  },
  provenance: { requirement_ref: 'DB1.feeder', entered_by_user_id: 'u1', entered_at: null },
};

const LABELLED: StoredLink = {
  ...STORED,
  id: 'l-77',
  feeder: { supply_board: 'MDB', fed_board: 'DB2', circuit_id: null },
  ends: [
    { role: 'from', board: 'MDB', terminal: null, label: 'Q2 out', resolution: 'unresolved' },
    { role: 'to', board: 'DB2', terminal: null, label: null, resolution: 'unresolved' },
  ],
  attributes: {},
};

const FINDINGS = [
  {
    code: 'link_endpoint_unresolved',
    board_id: 'MDB',
    values: { link_id: 'l-3f2a', end: 'to', end_board: 'DB1' },
  },
  {
    code: 'link_attribute_unset',
    board_id: 'MDB',
    // As the API sends it: finding values are strings.
    values: { link_id: 'l-3f2a', attributes: 'material,insulation' },
  },
  {
    code: 'link_value_differs_from_calculation',
    board_id: 'MDB',
    values: {
      link_id: 'l-3f2a',
      attribute: 'cross_section_mm2',
      user_value: '25',
      calculation_value: '16',
    },
  },
  { code: 'link_attribute_unset', board_id: 'DB2' },
];

describe('a saved revision’s links', () => {
  it('shows each end as resolved terminal, "terminal NOT SET" or the user’s label', () => {
    renderApp(
      <StoredLinks links={[STORED, LABELLED]} findings={[]} model={LINK_MODEL} revision={3} />,
      { locale: 'en' },
    );
    const first = within(screen.getByTestId('stored-link-DB1'));
    expect(first.getByText(/l-3f2a/)).toBeTruthy();
    expect(first.getByTestId('stored-end-from').textContent).toBe('From MDB: Terminal Q1:2');
    expect(first.getByTestId('stored-end-to').textContent).toBe('To DB1: terminal NOT SET');
    const second = within(screen.getByTestId('stored-link-DB2'));
    expect(second.getByTestId('stored-end-from').textContent).toBe(
      'From MDB: terminal NOT SET; “Q2 out” — label given by user, not a modelled terminal',
    );
  });

  it('shows each attribute with its source, or "not given"', () => {
    renderApp(<StoredLinks links={[STORED]} findings={[]} model={null} revision={3} />, {
      locale: 'en',
    });
    const link = within(screen.getByTestId('stored-link-DB1'));
    expect(link.getByTestId('stored-attr-cores').textContent).toBe('Cores4 (given by user)');
    expect(link.getByTestId('stored-attr-material').textContent).toBe('Materialnot given');
    expect(link.getByTestId('stored-attr-colour').textContent).toBe('Colournot given');
    // Without the model, the terminal's own ids.
    expect(link.getByTestId('stored-end-from').textContent).toBe('From MDB: Terminal cmp-q1:t-2');
  });

  it('lists the link’s findings, the differing values with both sources', () => {
    renderApp(
      <StoredLinks
        links={[STORED, LABELLED]}
        findings={FINDINGS}
        model={LINK_MODEL}
        revision={3}
      />,
      { locale: 'en' },
    );
    const items = within(screen.getByTestId('stored-link-DB1'))
      .getByTestId('link-findings')
      .querySelectorAll('li');
    expect(Array.from(items).map((item) => item.textContent)).toEqual([
      'End not resolved to a modelled terminal: To DB1.',
      'Not given: Material, Insulation.',
      'Cross-section (mm²): user value 25 (given by user); feeder calculation value 16 (used by the feeder calculation).',
    ]);
    expect(
      within(screen.getByTestId('stored-link-DB2')).getByTestId('link-findings').textContent,
    ).toBe(f.finding.link_attribute_unset_plain);
  });

  it('never calls a link complete', () => {
    renderApp(
      <StoredLinks
        links={[STORED, LABELLED]}
        findings={FINDINGS}
        model={LINK_MODEL}
        revision={3}
      />,
      { locale: 'en' },
    );
    expect(screen.getByTestId('stored-links').textContent.toLowerCase()).not.toContain('complete');
    for (const bundle of [en, ar, he]) {
      expect(JSON.stringify(bundle.feederLink).toLowerCase()).not.toMatch(/complete|مكتمل|הושלם/);
    }
  });
});

/* ------------------------------------------------------ the workspace --- */

const api = vi.hoisted(() => ({
  list: vi.fn<typeof listProjects>(),
  open: vi.fn<typeof openProject>(),
  revise: vi.fn<typeof reviseProject>(),
}));

vi.mock('@/lib/design', async (importOriginal) => {
  const actual = await importOriginal<typeof DesignModule>();
  return { ...actual, listProjects: api.list, openProject: api.open, reviseProject: api.revise };
});

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

function load(description: string) {
  return {
    description,
    load: 'lighting',
    power_kw: '1',
    phases: 1,
    power_factor: null,
    controlled: false,
    starter: null,
    length_m: null,
  };
}

function request(links?: unknown[]): SavedRequest {
  return {
    info: { name: 'Link tower', number: '', customer: '', consultant: '', contractor: '' },
    boards: [
      {
        name: 'MDB',
        location: null,
        fed_from: null,
        feeder_length_m: null,
        supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
        loads: [load('Lights')],
      },
      {
        name: 'DB1',
        location: null,
        fed_from: 'MDB',
        feeder_length_m: '40',
        supply: { voltage_v: '400', phases: 3, fault_level_ka: null },
        loads: [load('Sockets')],
      },
    ],
    profile: null,
    ...(links ? { feeder_links: links } : {}),
  } as unknown as SavedRequest;
}

function project(schema: { written?: string; snapshot: string }, extra = {}): SavedProject {
  return {
    id: 'p1',
    name: 'Link tower',
    revision: 1,
    validated: true,
    export_status: 'draft',
    permissions: ['approve_revision', 'delete_project'],
    revisions: [
      {
        number: 1,
        note: '',
        author: 'e',
        approver_account_recorded: false,
        created_at: '2026-11-10T10:00:00Z',
        validated: true,
        export_status: 'draft',
      },
    ],
    request: request(),
    findings: [],
    model_v2: LINK_MODEL,
    snapshot_schema: schema.snapshot,
    ...(schema.written ? { written_schema: schema.written } : {}),
    ...extra,
  } as unknown as SavedProject;
}

function renderScreen(designImpl?: typeof designProject) {
  renderApp(
    <DesignScreen
      acquireImpl={vi.fn().mockResolvedValue(READY)}
      marketsImpl={vi.fn().mockResolvedValue(null)}
      saveImpl={vi.fn()}
      {...(designImpl ? { designImpl } : {})}
    />,
    { locale: 'en' },
  );
}

async function openStored() {
  const row = await screen.findByTestId('project-p1');
  fireEvent.click(within(row).getByText('Open'));
  await waitFor(() => {
    expect(screen.getByLabelText<HTMLInputElement>('Project name').value).toBe('Link tower');
  });
}

function showBoard(name: string) {
  fireEvent.click(
    within(screen.getByTestId('board-tabs')).getByRole('button', { name, hidden: true }),
  );
}

describe('the workspace', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    api.revise.mockReset();
    api.list.mockResolvedValue({
      kind: 'listed',
      page: {
        permissions: ['approve_revision', 'delete_project'],
        projects: [{ id: 'p1', name: 'Link tower', revisions: 1, updated_at: 'now' }],
      },
    });
  });

  it('shows nothing new where the deployment does not write pp-design/3', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: project({ written: 'pp-design/2', snapshot: 'pp-design/2' }),
    });
    renderScreen();
    await openStored();
    showBoard('DB1');
    expect(screen.queryByTestId('feeder-link-form')).toBeNull();
    expect(screen.queryByTestId('opened-links')).toBeNull();
  });

  it('shows the form only for a fed board, where the deployment writes pp-design/3', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: project({ written: 'pp-design/3', snapshot: 'pp-design/2' }),
    });
    renderScreen();
    await openStored();
    showBoard('MDB');
    expect(screen.queryByTestId('feeder-link-form')).toBeNull();
    showBoard('DB1');
    expect(screen.getByTestId('feeder-link-form').textContent).toContain('MDB → DB1');
  });

  it('saves request.feeder_links exactly as entered, and shows a link refusal at the form', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: project({ written: 'pp-design/3', snapshot: 'pp-design/3' }),
    });
    api.revise.mockResolvedValue({
      kind: 'refused',
      detail: 'off board',
      code: 'link_end_off_board',
      params: { fed_board: 'DB1' },
    });
    renderScreen();
    await openStored();
    showBoard('DB1');
    const link = within(screen.getByTestId('feeder-link-form'));
    fireEvent.change(link.getByTestId('link-from-terminal'), {
      target: { value: encodeTerminal({ component: 'cmp-q1', terminal: 't-2' }) },
    });
    fireEvent.change(link.getByTestId('link-input-cores'), { target: { value: '5' } });
    fireEvent.change(link.getByTestId('link-input-colour'), { target: { value: 'black' } });
    fireEvent.click(screen.getByText('Save as new revision', { selector: 'button' }));
    await waitFor(() => {
      expect(api.revise).toHaveBeenCalledTimes(1);
    });
    const sent = api.revise.mock.lastCall?.[0].request as unknown as { feeder_links: unknown };
    expect(sent.feeder_links).toEqual([
      {
        fed_board: 'DB1',
        from_end: { terminal: { component: 'cmp-q1', terminal: 't-2' } },
        attributes: { cores: 5, colour: 'black' },
      },
    ]);
    const alert = await within(screen.getByTestId('feeder-link-form')).findByTestId(
      'feeder-link-refusal',
    );
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toContain(en.design.errors.link_end_off_board);
    // The form keeps what was typed.
    expect(
      within(
        screen.getByTestId<HTMLInputElement>('feeder-link-form'),
      ).getByTestId<HTMLInputElement>('link-input-colour').value,
    ).toBe('black');
  });

  it('reopens the stored link into the form and shows it with its findings', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: project(
        { snapshot: 'pp-design/3' },
        {
          request: request([
            { fed_board: 'DB1', to_end: { label: 'Lugs' }, attributes: { cores: 4 } },
          ]),
          links: [STORED],
          findings: FINDINGS.slice(0, 3).map((finding) => ({
            severity: 'warning',
            message: finding.code,
            ...finding,
          })),
        },
      ),
    });
    renderScreen();
    await openStored();
    showBoard('DB1');
    const link = within(screen.getByTestId<HTMLInputElement>('feeder-link-form'));
    expect(link.getByTestId<HTMLInputElement>('link-to-label').value).toBe('Lugs');
    expect(link.getByTestId<HTMLInputElement>('link-input-cores').value).toBe('4');
    expect(link.getByTestId<HTMLInputElement>('link-input-insulation').value).toBe('');
    const stored = within(screen.getByTestId('opened-links'));
    expect(stored.getByTestId('stored-end-to').textContent).toContain('terminal NOT SET');
    const items = Array.from(screen.getByTestId('opened-links').querySelectorAll('li'));
    expect(items.map((item) => item.textContent)).toContain('Not given: Material, Insulation.');
  });

  it('shows the calculation’s feeder cable values once designed, without copying them', async () => {
    api.open.mockResolvedValue({
      kind: 'saved',
      project: project({ written: 'pp-design/3', snapshot: 'pp-design/2' }),
    });
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'designed',
      response: {
        project: {
          info: { name: 'Link tower', number: '', customer: '', consultant: '', contractor: '' },
          boards: [
            {
              name: 'MDB',
              circuits: [{ id: 'c-4', feeds: 'DB1', cable_id: 'w2', description: 'DB1' }],
              cables: [
                {
                  id: 'w2',
                  cores: 5,
                  cross_section_mm2: '16',
                  material: 'copper',
                  insulation: 'XLPE',
                  length_m: '40',
                  parallel: 1,
                },
              ],
              devices: [],
            },
            { name: 'DB1', fed_from: 'MDB', circuits: [], cables: [], devices: [] },
          ],
        },
        profile: {},
      } as unknown as DesignModule.BoardDesignResponse,
    });
    renderScreen(designImpl);
    await openStored();
    fireEvent.click(screen.getByText('Design the board', { selector: 'button' }));
    await waitFor(() => {
      expect(designImpl).toHaveBeenCalled();
    });
    showBoard('DB1');
    const link = within(await screen.findByTestId('feeder-link-form'));
    expect((await link.findByTestId('link-calc-cores')).textContent).toBe(
      'Used by the feeder calculation: 5',
    );
    expect(link.getByTestId<HTMLInputElement>('link-input-cores').value).toBe('');
  });
});

describe('stored values in the reader’s language', () => {
  it('names a stored material in Arabic, and the unresolved end by its own words', () => {
    const link = {
      ...STORED,
      attributes: { ...STORED.attributes, material: { value: 'copper', source: 'user' as const } },
    };
    renderApp(<StoredLinks links={[link]} findings={FINDINGS} model={null} revision={3} />, {
      locale: 'ar',
    });
    const card = within(screen.getByTestId('stored-link-DB1'));
    expect(card.getByTestId('stored-attr-material').textContent).toContain('نحاس');
    expect(card.getByTestId('stored-attr-material').textContent).not.toContain('copper');
    expect(card.getByTestId('link-findings').textContent.replace(/[\u2066-\u2069]/g, '')).toContain(
      'إلى DB1',
    );
    expect(card.getByTestId('link-findings').textContent).not.toMatch(/: to\b/);
  });
});
