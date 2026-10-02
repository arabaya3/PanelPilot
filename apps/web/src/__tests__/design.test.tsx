import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import {
  designProject,
  exportDesign,
  importSchedule,
  suggestSchedule,
  type BoardDesignResponse,
  type DesignProject,
} from '@/lib/design';

import { renderApp } from './helpers';

/** `/design`: a load schedule in, a designed board and its downloads out. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const PROJECT: DesignProject = {
  info: { name: 'Pocket', number: '', customer: '', consultant: '', contractor: '', revisions: [] },
  profile: 'iec-default',
  parts: [],
  boards: [
    {
      id: 'DB1',
      name: 'DB1',
      function: null,
      location: null,
      supply: { voltage_v: '400', phases: 3, frequency_hz: '50', earthing: 'TN-S' },
      incomer_ids: ['incomer'],
      notes: [
        {
          code: 'default_power_factor',
          params: {},
          text: 'Loads without a power factor were taken at cos phi 0.9.',
        },
      ],
      devices: [
        {
          id: 'incomer',
          kind: 'circuit_breaker',
          poles: 4,
          rated_current_a: '16',
          curve: 'C',
          designation: { product: 'Q1' },
          description: 'Main incomer',
        },
        {
          id: 'g1-rcd',
          kind: 'residual_current_device',
          poles: 4,
          rated_current_a: '25',
          residual_current_ma: '30',
          designation: { product: 'F1' },
          description: '',
        },
        {
          id: 'c1-breaker',
          kind: 'circuit_breaker',
          poles: 1,
          rated_current_a: '16',
          curve: 'C',
          breaking_capacity_ka: '10',
          designation: { product: 'Q3' },
          description: 'Sockets',
        },
      ],
      cables: [
        {
          id: 'c1-cable',
          cores: 3,
          cross_section_mm2: '2.5',
          material: 'Cu',
          insulation: 'PVC',
          designation: { product: 'W1' },
        },
      ],
      circuits: [
        {
          id: 'c1',
          description: 'Sockets hall',
          load: 'socket',
          power_kw: '1.5',
          design_current_a: '7.22',
          phase: 'L1',
          upstream_id: 'g1-rcd',
          device_ids: ['c1-breaker'],
          cable_id: 'c1-cable',
        },
      ],
    },
  ],
} as unknown as DesignProject;

const DESIGNED = {
  project: PROJECT,
  profile: { key: 'iec-default' },
} as unknown as BoardDesignResponse;

function fillSchedule() {
  fireEvent.change(screen.getByLabelText('Project name'), { target: { value: 'Pocket' } });
  fireEvent.change(screen.getByLabelText('Circuit'), { target: { value: 'Sockets hall' } });
  fireEvent.change(screen.getByLabelText(/^Power.*kW/), { target: { value: '1.5' } });
}

async function submit() {
  const button = screen.getByRole('button', { name: 'Design the board' });
  await waitFor(() => {
    expect(button.hasAttribute('disabled')).toBe(false);
  });
  fireEvent.click(button);
}

describe('board design', () => {
  it('sends the schedule and shows each circuit with its protection', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'designed',
      response: DESIGNED,
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
    );
    fillSchedule();
    fireEvent.click(screen.getByLabelText('PLC switched'));
    await submit();

    await screen.findByTestId('design-result');
    const call = designImpl.mock.calls[0]?.[0];
    expect(call?.token).toBe('tok');
    expect(call?.request.info.name).toBe('Pocket');
    expect(call?.request.boards[0]?.loads).toEqual([
      {
        description: 'Sockets hall',
        load: 'socket',
        power_kw: '1.5',
        phases: 1,
        power_factor: null,
        controlled: true,
        starter: null,
        length_m: null,
      },
    ]);
    expect(call?.request.profile).toBeNull();
    expect(screen.getByText('-Q3 C16 10 kA')).toBeTruthy();
    expect(screen.getByText('-F1 30 mA')).toBeTruthy();
    expect(screen.getByText('3G2.5 Cu')).toBeTruthy();
    expect(screen.getByTestId('design-notes').textContent).toContain('cos φ 0.9');
  });

  it('sends the company settings, and refuses settings that are not JSON', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'designed',
      response: DESIGNED,
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
    );
    fillSchedule();
    const settings = screen.getByLabelText('Company settings (optional)');
    fireEvent.change(settings, { target: { value: '{not json' } });
    await submit();
    expect((await screen.findByTestId('design-error')).textContent).toBe(
      'The company settings are not valid JSON.',
    );
    expect(designImpl).not.toHaveBeenCalled();

    fireEvent.change(settings, { target: { value: '{"key": "acme"}' } });
    await submit();
    await screen.findByTestId('design-result');
    expect(designImpl.mock.calls[0]?.[0].request.profile).toEqual({ key: 'acme' });
  });

  it('draws in Arabic by default in the Arabic page', () => {
    renderApp(<DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} />, {
      locale: 'ar',
    });
    expect(screen.getByLabelText<HTMLSelectElement>('لغة المخططات').value).toBe('ar');
  });

  it('asks for the drawings in Arabic', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'designed',
      response: DESIGNED,
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
    );
    fillSchedule();
    fireEvent.change(screen.getByLabelText('Drawing language'), { target: { value: 'ar' } });
    await submit();
    await screen.findByTestId('design-result');
    expect(designImpl.mock.calls[0]?.[0].request.profile).toEqual({ language: 'ar' });
  });

  it('shows a refusal in the server’s words', async () => {
    const designImpl = vi.fn<typeof designProject>().mockResolvedValue({
      kind: 'refused',
      detail: 'Chiller: 144 A exceeds the largest curve C rating held (125 A)',
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
    );
    fillSchedule();
    await submit();
    expect((await screen.findByTestId('design-error')).textContent).toContain('Chiller');
  });

  it('downloads each export as the file the server names', async () => {
    const blob = new Blob(['%PDF']);
    const exportImpl = vi.fn<typeof exportDesign>().mockResolvedValue({
      kind: 'exported',
      blob,
      filename: 'Pocket.pdf',
    });
    const saveImpl = vi.fn();
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        designImpl={vi.fn().mockResolvedValue({ kind: 'designed', response: DESIGNED })}
        exportImpl={exportImpl}
        saveImpl={saveImpl}
      />,
    );
    fillSchedule();
    await submit();
    fireEvent.click(await screen.findByTestId('design-export-pdf'));
    await waitFor(() => {
      expect(saveImpl).toHaveBeenCalledWith(blob, 'Pocket.pdf');
    });
    expect(exportImpl.mock.calls[0]?.[0]).toMatchObject({
      token: 'tok',
      format: 'pdf',
      project: PROJECT,
    });
  });
});

describe('load schedule import', () => {
  it('fills the rows from the file and lists what it assumed', async () => {
    const importImpl = vi.fn<typeof importSchedule>().mockResolvedValue({
      kind: 'imported',
      result: {
        loads: [
          {
            description: 'Sockets east',
            load: 'socket',
            power_kw: '1.5',
            phases: 1,
            power_factor: null,
            controlled: false,
          },
          {
            description: 'AC 1',
            load: 'air_conditioning',
            power_kw: '4',
            phases: 3,
            power_factor: '0.85',
            controlled: false,
          },
        ],
        warnings: [
          {
            code: 'import_kind_inferred',
            params: { row: '3', load: 'AC 1', kind: 'air_conditioning' },
            text: 'Row 3 (AC 1): taken as air_conditioning from its description.',
          },
        ],
        rows_read: 2,
      },
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} importImpl={importImpl} />,
    );
    const input = screen.getByLabelText('Import a load schedule');
    await waitFor(() => {
      expect(input.hasAttribute('disabled')).toBe(false);
    });
    const file = new File(['x'], 'schedule.xlsx');
    fireEvent.change(input, { target: { files: [file] } });

    const result = await screen.findByTestId('import-result');
    expect(result.textContent).toContain('2 circuits read.');
    expect(result.textContent).toContain('from its description');
    expect(importImpl.mock.calls[0]?.[0]).toMatchObject({
      token: 'tok',
      filename: 'schedule.xlsx',
    });
    expect((screen.getAllByLabelText('Circuit')[1] as HTMLInputElement).value).toBe('AC 1');
    expect((screen.getAllByLabelText('Power factor')[1] as HTMLInputElement).value).toBe('0.85');
  });

  it('shows why a file was refused', async () => {
    const importImpl = vi.fn<typeof importSchedule>().mockResolvedValue({
      kind: 'refused',
      detail: 'no header row names both a description and a power (kW or W) column',
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} importImpl={importImpl} />,
    );
    const input = screen.getByLabelText('Import a load schedule');
    await waitFor(() => {
      expect(input.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.change(input, { target: { files: [new File(['x'], 'bad.csv')] } });
    expect((await screen.findByTestId('import-error')).textContent).toContain('no header row');
  });

  it('posts the file as multipart', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ loads: [], warnings: [], rows_read: 0 }), { status: 200 }),
      );
    const outcome = await importSchedule({
      token: 't',
      file: new Blob(['a']),
      filename: 's.csv',
      fetchImpl,
    });
    expect(outcome.kind).toBe('imported');
    const init = fetchImpl.mock.calls[0]?.[1];
    expect(init?.body).toBeInstanceOf(FormData);
  });
});

describe('suggest from a description', () => {
  it('sends the description and supply, fills the rows and lists the assumptions', async () => {
    const suggestImpl = vi.fn<typeof suggestSchedule>().mockResolvedValue({
      kind: 'suggested',
      result: {
        loads: [
          {
            description: 'Hall sockets 1',
            load: 'socket',
            power_kw: '1.05',
            phases: 1,
            power_factor: null,
            controlled: false,
          },
          {
            description: 'Hall sockets 2',
            load: 'socket',
            power_kw: '0.9',
            phases: 1,
            power_factor: null,
            controlled: false,
          },
        ],
        assumptions: [
          {
            code: 'text',
            params: { text: 'Diversity not applied.' },
            text: 'Diversity not applied.',
          },
          {
            code: 'split_points',
            params: {
              load: 'Hall sockets 1',
              points: '7',
              watts: '150',
              power: '1.05',
              source: 'typical',
            },
            text: 'Hall sockets 1: 7 x 150 W = 1.05 kW. typical',
          },
        ],
      },
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} suggestImpl={suggestImpl} />,
    );
    fireEvent.change(screen.getByLabelText('Describe the board'), {
      target: { value: 'A hall with 13 sockets' },
    });
    const button = screen.getByRole('button', { name: 'Suggest a schedule' });
    await waitFor(() => {
      expect(button.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.click(button);
    const result = await screen.findByTestId('suggest-result');
    expect(result.textContent).toContain('7 × 150 W');
    expect(suggestImpl.mock.calls[0]?.[0]).toMatchObject({
      token: 'tok',
      description: 'A hall with 13 sockets',
      supplyPhases: 3,
      profile: null,
    });
    expect((screen.getAllByLabelText('Circuit')[1] as HTMLInputElement).value).toBe(
      'Hall sockets 2',
    );
  });

  it('says when the month’s allowance is spent', async () => {
    renderApp(
      <DesignScreen
        acquireImpl={vi.fn().mockResolvedValue(READY)}
        suggestImpl={vi.fn<typeof suggestSchedule>().mockResolvedValue({ kind: 'budget' })}
      />,
    );
    fireEvent.change(screen.getByLabelText('Describe the board'), { target: { value: 'hall' } });
    const button = screen.getByRole('button', { name: 'Suggest a schedule' });
    await waitFor(() => {
      expect(button.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.click(button);
    expect((await screen.findByTestId('suggest-error')).textContent).toBe(
      "This month's AI allowance is used up.",
    );
  });

  it('maps a 429 to the budget outcome', async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response('', { status: 429 }));
    expect(
      await suggestSchedule({ token: 't', description: 'x', supplyPhases: 3, fetchImpl }),
    ).toEqual({ kind: 'budget' });
  });
});

describe('design client', () => {
  it('reads the filename from the response and maps refusals', async () => {
    const ok = vi.fn<typeof fetch>().mockResolvedValue(
      new Response('x', {
        status: 200,
        headers: { 'Content-Disposition': 'attachment; filename="Hall.qet"' },
      }),
    );
    const exported = await exportDesign({
      token: 't',
      project: PROJECT,
      format: 'qet',
      fetchImpl: ok,
    });
    expect(exported).toMatchObject({ kind: 'exported', filename: 'Hall.qet' });

    const refused = vi
      .fn<typeof fetch>()
      .mockResolvedValue(
        new Response(JSON.stringify({ detail: [{ msg: 'bad format' }] }), { status: 422 }),
      );
    expect(
      await exportDesign({ token: 't', project: PROJECT, format: 'pdf', fetchImpl: refused }),
    ).toEqual({ kind: 'refused', detail: 'bad format' });

    const designed = await designProject({
      token: 't',
      request: DESIGNED as never,
      fetchImpl: vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(JSON.stringify(DESIGNED), { status: 200 })),
    });
    expect(designed.kind).toBe('designed');
    expect(
      await designProject({
        token: 't',
        request: DESIGNED as never,
        fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(new Response('', { status: 401 })),
      }),
    ).toEqual({ kind: 'unauthorized' });
  });
});
