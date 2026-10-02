import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DesignScreen } from '@/components/design-screen';
import {
  designBoard,
  exportDesign,
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
      notes: ['Loads without a power factor were taken at cos phi 0.9.'],
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
    const designImpl = vi.fn<typeof designBoard>().mockResolvedValue({
      kind: 'designed',
      response: DESIGNED,
    });
    renderApp(
      <DesignScreen acquireImpl={vi.fn().mockResolvedValue(READY)} designImpl={designImpl} />,
    );
    fillSchedule();
    await submit();

    await screen.findByTestId('design-result');
    const call = designImpl.mock.calls[0]?.[0];
    expect(call?.token).toBe('tok');
    expect(call?.request.info.name).toBe('Pocket');
    expect(call?.request.board.loads).toEqual([
      {
        description: 'Sockets hall',
        load: 'socket',
        power_kw: '1.5',
        phases: 1,
        power_factor: null,
      },
    ]);
    expect(call?.request.profile).toBeNull();
    expect(screen.getByText('-Q3 C16')).toBeTruthy();
    expect(screen.getByText('-F1 30 mA')).toBeTruthy();
    expect(screen.getByText('3G2.5 Cu')).toBeTruthy();
    expect(screen.getByTestId('design-notes').textContent).toContain('cos phi 0.9');
  });

  it('sends the company settings, and refuses settings that are not JSON', async () => {
    const designImpl = vi.fn<typeof designBoard>().mockResolvedValue({
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

  it('shows a refusal in the server’s words', async () => {
    const designImpl = vi.fn<typeof designBoard>().mockResolvedValue({
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

    const designed = await designBoard({
      token: 't',
      request: DESIGNED as never,
      fetchImpl: vi
        .fn<typeof fetch>()
        .mockResolvedValue(new Response(JSON.stringify(DESIGNED), { status: 200 })),
    });
    expect(designed.kind).toBe('designed');
    expect(
      await designBoard({
        token: 't',
        request: DESIGNED as never,
        fetchImpl: vi.fn<typeof fetch>().mockResolvedValue(new Response('', { status: 401 })),
      }),
    ).toEqual({ kind: 'unauthorized' });
  });
});
