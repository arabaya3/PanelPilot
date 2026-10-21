import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { CableSizingPanel } from '@/components/cable-sizing-panel';
import { checkNumber, sizeCable, type CableSizingResponse } from '@/lib/calculations';

import { renderApp } from './helpers';

/** `/calc`: the cable-sizing endpoint had no page. */

const READY = {
  kind: 'ready' as const,
  accessToken: 'tok',
  trial: { sessionId: 's', claimSecret: 'c' },
  questionsRemaining: 5,
  conversationId: null,
};

const ABB = {
  document_id: 'abb-1SDC010001D0204',
  document_title: 'Electrical installation handbook, Vol. 2',
  manufacturer: 'ABB',
};

const SIZED: CableSizingResponse = {
  result: {
    cross_section_mm2: '95',
    derated_ampacity_a: '111.8124',
    applied_factors: [
      { name: 'k1 ambient 40 °C', value: '0.87', source: { ...ABB, page: 34, section: 'Table 4' } },
      {
        name: 'k2 grouping 7 circuits',
        value: '0.54',
        source: { ...ABB, page: 37, section: 'Table 5' },
      },
    ],
  },
  voltage_drop_v: '2.1',
  voltage_drop_percent: '0.525',
  sources: [{ ...ABB, page: 43, section: 'Table 8' }],
};

function fill(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label, { exact: false }), { target: { value } });
}

async function renderReady(sizeImpl: typeof sizeCable) {
  renderApp(
    <CableSizingPanel sizeImpl={sizeImpl} acquireImpl={vi.fn().mockResolvedValue(READY)} />,
  );
  fill('Design current', '100');
  fill('Run length', '50');
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Size the cable' }).hasAttribute('disabled')).toBe(
      false,
    );
  });
}

describe('cable sizing', () => {
  it('sends the inputs and shows the size, drop and every source', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({ kind: 'sized', response: SIZED });
    await renderReady(sizeImpl);
    fill('Installation method', 'E');
    fill('Ambient temperature', '40');
    fill('Circuits grouped together', '7');
    fill('Insulation', '70');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    await screen.findByTestId('calc-result');
    expect(sizeImpl).toHaveBeenCalledWith({
      token: 'tok',
      request: {
        design_current_a: '100',
        length_m: '50',
        supply_voltage_v: '400',
        installation_method: 'E',
        ambient_temp_c: '40',
        grouped_circuits: 7,
        conductor_material: 'copper',
        insulation_rating_c: 70,
        power_factor: '0.8',
        three_phase: true,
      },
    });
    expect(screen.getByText('95 mm²')).toBeTruthy();
    expect(screen.getByText('111.81 A')).toBeTruthy();
    expect(screen.getByText('2.1 V (0.53%)')).toBeTruthy();
    expect(screen.getByTestId('calc-sources').textContent).toContain('Table 8, p. 43');
  });

  it('sends aluminium when it is picked', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({ kind: 'sized', response: SIZED });
    await renderReady(sizeImpl);
    fill('Conductor', 'aluminium');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    await screen.findByTestId('calc-result');
    const sent = sizeImpl.mock.calls[0]?.[0] as { request: { conductor_material: string } };
    expect(sent.request.conductor_material).toBe('aluminium');
  });

  it('says a coded refusal at its field and in the notice, never the server English', async () => {
    // As the API sends it (`POST /calculations/cable-sizing`, ambient 95 °C).
    const sizeImpl = vi.fn().mockResolvedValue({
      kind: 'refused',
      detail: 'ambient 95 °C is outside Table 4 for 90 °C insulation (10-80 °C)',
      code: 'ambient_out_of_table',
      params: { ambient: '95', insulation: '90', low: '10', high: '80' },
    });
    await renderReady(sizeImpl);
    fill('Ambient temperature', '95');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    const alert = await screen.findByTestId('calc-error');
    const sentence =
      'An ambient of 95 °C is outside the cable table for 90 °C insulation (10–80 °C); ask the cable manufacturer.';
    expect(alert.textContent).toContain('do not cover this input');
    expect(alert.textContent).toContain(sentence);
    expect(alert.textContent).not.toContain('Table 4');
    const ambient = screen.getByLabelText('Ambient temperature', { exact: false });
    expect(ambient.getAttribute('aria-invalid')).toBe('true');
    expect(
      document.getElementById(ambient.getAttribute('aria-describedby') ?? '')?.textContent,
    ).toBe(sentence);
    expect((ambient as HTMLInputElement).value).toBe('95');
    expect(screen.queryByTestId('calc-result')).toBeNull();
  });

  it('never shows an uncoded refusal’s English, which can name internal fields', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({
      kind: 'refused',
      detail: 'design_current_a must be positive, got -5',
    });
    await renderReady(sizeImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    const alert = await screen.findByTestId('calc-error');
    expect(alert.textContent).toBe('The tables do not cover this input, so no size is given.');
    expect(document.body.textContent).not.toContain('design_current_a');
  });

  it('marks the field a schema check rejected, without its internal name', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({
      kind: 'refused',
      detail: '',
      fields: ['length_m'],
    });
    await renderReady(sizeImpl);
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    const alert = await screen.findByTestId('calc-error');
    expect(alert.textContent).toContain('Correct the marked fields');
    const length = screen.getByLabelText('Run length', { exact: false });
    expect(length.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByText('Enter a number, such as 12.5.')).toBeTruthy();
    expect(document.body.textContent).not.toContain('length_m');
  });

  it.each([
    ['-5', 'Enter a number above zero.'],
    ['0', 'Enter a number above zero.'],
    ['', 'Enter a value.'],
    ['   ', 'Enter a value.'],
    ['abc', 'Enter a number, such as 12.5.'],
    ['NaN', 'Enter a number, such as 12.5.'],
    ['Infinity', 'Enter a number, such as 12.5.'],
    ['12,5', 'Enter a number, such as 12.5.'],
    ['1e999', 'This number is too large to calculate with.'],
  ])('refuses a design current of %j at the field on Enter, keeping it', async (value, message) => {
    const sizeImpl = vi.fn();
    await renderReady(sizeImpl);
    const current = screen.getByLabelText('Design current', { exact: false });
    fireEvent.change(current, { target: { value } });
    // Enter in a field submits the form, as the browser does.
    fireEvent.submit(current.closest('form') as HTMLFormElement);

    await waitFor(() => {
      expect(current.getAttribute('aria-invalid')).toBe('true');
    });
    const describedBy = current.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(describedBy)?.textContent).toBe(message);
    expect(document.activeElement).toBe(current);
    expect((current as HTMLInputElement).value).toBe(value);
    expect(sizeImpl).not.toHaveBeenCalled();
    expect(screen.queryByTestId('calc-error')).toBeNull();
  });

  it('checks every number field and clears an error once the field is edited', async () => {
    const sizeImpl = vi.fn().mockResolvedValue({ kind: 'sized', response: SIZED });
    await renderReady(sizeImpl);
    fill('Run length', '-1');
    fill('Supply voltage', '0');
    fill('Circuits grouped together', '1.5');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));

    const length = screen.getByLabelText('Run length', { exact: false });
    await waitFor(() => {
      expect(length.getAttribute('aria-invalid')).toBe('true');
    });
    expect(
      screen.getByLabelText('Supply voltage', { exact: false }).getAttribute('aria-invalid'),
    ).toBe('true');
    expect(screen.getByText('Enter a whole number, 1 or more.')).toBeTruthy();
    expect(sizeImpl).not.toHaveBeenCalled();

    fill('Run length', '50');
    expect(length.hasAttribute('aria-invalid')).toBe(false);
    fill('Supply voltage', '400');
    fill('Circuits grouped together', '2');
    fireEvent.click(screen.getByRole('button', { name: 'Size the cable' }));
    await screen.findByTestId('calc-result');
    expect(sizeImpl).toHaveBeenCalledTimes(1);
  });

  it('says a field error in the page language', async () => {
    renderApp(
      <CableSizingPanel sizeImpl={vi.fn()} acquireImpl={vi.fn().mockResolvedValue(READY)} />,
      { locale: 'ar' },
    );
    const submit = await screen.findByRole('button', { name: 'احسب المقطع' });
    await waitFor(() => {
      expect(submit.hasAttribute('disabled')).toBe(false);
    });
    fireEvent.click(submit);
    expect((await screen.findAllByText('أدخل قيمة.')).length).toBe(2);
  });
});

describe('sizeCable', () => {
  function respond(status: number, body: unknown) {
    return vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status }));
  }
  const request = {
    design_current_a: '32',
    length_m: '40',
    supply_voltage_v: '400',
    installation_method: 'B1' as const,
    ambient_temp_c: '30',
    grouped_circuits: 1,
    conductor_material: 'copper' as const,
    insulation_rating_c: 90,
    power_factor: '0.8',
    three_phase: true,
  };

  it('reads a 422 as a refusal with its reason', async () => {
    const fetchImpl = respond(422, { error: 'ValidationError', detail: 'outside Table 4' });
    expect(await sizeCable({ token: 't', request, fetchImpl })).toEqual({
      kind: 'refused',
      detail: 'outside Table 4',
    });
  });

  it('reads a 401 as an expired session and a malformed body as a failure', async () => {
    expect(await sizeCable({ token: 't', request, fetchImpl: respond(401, {}) })).toEqual({
      kind: 'unauthorized',
    });
    expect(await sizeCable({ token: 't', request, fetchImpl: respond(200, {}) })).toEqual({
      kind: 'failed',
    });
  });

  it('keeps a refusal’s code and params', async () => {
    const fetchImpl = respond(422, {
      error: 'ValidationError',
      detail: '0 grouped circuits is outside Table 5 (1-20)',
      code: 'grouped_out_of_table',
      params: { grouped: '0', largest: '20' },
    });
    expect(await sizeCable({ token: 't', request, fetchImpl })).toEqual({
      kind: 'refused',
      detail: '0 grouped circuits is outside Table 5 (1-20)',
      code: 'grouped_out_of_table',
      params: { grouped: '0', largest: '20' },
    });
  });

  it('reads a schema 422 as the fields it rejected, not its objects', async () => {
    // FastAPI's own shape, as the API answers `design_current_a: "abc"`.
    const fetchImpl = respond(422, {
      detail: [
        {
          type: 'decimal_parsing',
          loc: ['body', 'design_current_a'],
          msg: 'Input should be a valid decimal',
          input: 'abc',
        },
      ],
    });
    expect(await sizeCable({ token: 't', request, fetchImpl })).toEqual({
      kind: 'refused',
      detail: '',
      fields: ['design_current_a'],
    });
  });
});

describe('checkNumber', () => {
  it.each([
    ['12.5', 'positive', null],
    ['  7 ', 'positive', null],
    ['.5', 'fraction', null],
    ['1', 'fraction', null],
    ['1.01', 'fraction', 'notFraction'],
    ['0', 'fraction', 'notFraction'],
    ['0', 'positive', 'notPositive'],
    ['-0.1', 'positive', 'notPositive'],
    ['0', 'nonNegative', null],
    ['-1', 'nonNegative', 'negative'],
    ['-10', 'finite', null],
    ['1e3', 'positive', null],
    ['1e999', 'positive', 'tooLarge'],
    ['Infinity', 'positive', 'notANumber'],
    ['NaN', 'finite', 'notANumber'],
    ['0x10', 'positive', 'notANumber'],
    ['1 000', 'positive', 'notANumber'],
    ['', 'positive', 'required'],
    ['1', 'count', null],
    ['0', 'count', 'notCount'],
    ['2.5', 'count', 'notCount'],
    ['800', 'wholePositive', null],
    ['-800', 'wholePositive', 'notWholePositive'],
  ] as const)('%j as %s is %s', (raw, rule, problem) => {
    expect(checkNumber(raw, rule)).toBe(problem);
  });

  it('lets an optional field stay empty', () => {
    expect(checkNumber(' ', 'positive', { optional: true })).toBeNull();
  });
});
