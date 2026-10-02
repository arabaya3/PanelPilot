import { fireEvent, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { CompanySettingsPanel } from '@/components/design/company-settings';
import {
  moved,
  parseSettings,
  readList,
  readSetting,
  settingsText,
  toggled,
  writeList,
  writeSetting,
} from '@/components/design/company-settings-model';
import type { getCompanySettings, saveCompanySettings } from '@/lib/design';

import { renderApp } from './helpers';

/** The company settings form, and the JSON it edits. */

describe('company settings model', () => {
  it('reads, writes and clears a path, dropping emptied mappings', () => {
    const start = { key: 'acme', circuit_rules: { socket: { breaker_a: '20' } } };
    expect(readSetting(start, ['circuit_rules', 'socket', 'breaker_a'])).toBe('20');
    expect(readSetting(start, ['circuit_rules', 'lighting', 'curve'])).toBe('');
    const written = writeSetting(start, ['demand_factors', 'socket'], ' 0.5 ');
    expect(written).toEqual({ ...start, demand_factors: { socket: '0.5' } });
    const cleared = writeSetting(written, ['circuit_rules', 'socket', 'breaker_a'], '');
    expect(cleared).toEqual({ key: 'acme', demand_factors: { socket: '0.5' } });
  });

  it('orders and picks from a list, the default list left unset', () => {
    const defaults = ['a', 'b', 'c'];
    expect(readList({}, 'pages', defaults)).toEqual(defaults);
    expect(readList({ pages: ['c'] }, 'pages', defaults)).toEqual(['c']);
    expect(moved(['a', 'b', 'c'], 'c', -1)).toEqual(['a', 'c', 'b']);
    expect(moved(['a', 'b'], 'a', -1)).toEqual(['a', 'b']);
    expect(toggled(['a', 'b'], 'a')).toEqual(['b']);
    expect(toggled(['b'], 'a')).toEqual(['b', 'a']);
    expect(writeList({ key: 'k' }, 'pages', ['b', 'a', 'c'], defaults)).toEqual({
      key: 'k',
      pages: ['b', 'a', 'c'],
    });
    expect(writeList({ key: 'k', pages: ['b'] }, 'pages', defaults, defaults)).toEqual({
      key: 'k',
    });
  });

  it('parses text, and says when it is not a JSON object', () => {
    expect(parseSettings('')).toEqual({});
    expect(parseSettings('[1]')).toBe('invalid');
    expect(parseSettings('{oops')).toBe('invalid');
    expect(settingsText({})).toBe('');
    expect(parseSettings(settingsText({ key: 'a' }))).toEqual({ key: 'a' });
  });
});

function Harness({
  load,
  save,
  initial = '',
}: {
  load: typeof getCompanySettings;
  save: typeof saveCompanySettings;
  initial?: string;
}) {
  const [text, setText] = useState(initial);
  return (
    <>
      <CompanySettingsPanel
        token="tok"
        text={text}
        onText={setText}
        onLoaded={(settings) => {
          setText((current) => (current === '' ? JSON.stringify(settings) : current));
        }}
        language="ar"
        impls={{ load, save }}
      />
      <output data-testid="text">{text}</output>
    </>
  );
}

describe('the company settings panel', () => {
  it('fills a blank form from the saved settings, edits, and saves for the company', async () => {
    const load = vi.fn<typeof getCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: { settings: { key: 'acme', name: 'Acme' }, updated_by: 'e', updated_at: null },
    });
    const save = vi.fn<typeof saveCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: { settings: { key: 'acme' }, updated_by: 'e', updated_at: '2026-10-02T12:00:00Z' },
    });
    renderApp(<Harness load={load} save={save} />);
    await waitFor(() => {
      expect(screen.getByLabelText<HTMLInputElement>('Company name').value).toBe('Acme');
    });
    fireEvent.change(screen.getByLabelText('Sockets: Demand factor'), {
      target: { value: '0.4' },
    });
    fireEvent.change(screen.getByLabelText('Circuits per RCD'), { target: { value: '8' } });
    expect(JSON.parse(screen.getByTestId('text').textContent)).toEqual({
      key: 'acme',
      name: 'Acme',
      demand_factors: { socket: '0.4' },
      max_circuits_per_rcd: '8',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save for the company' }));
    await waitFor(() => {
      expect(save).toHaveBeenCalled();
    });
    expect(save.mock.calls[0]?.[0].settings).toMatchObject({ language: 'ar', key: 'acme' });
    expect(screen.getByTestId('settings-message').textContent).toBe(
      'Saved. New projects start from these settings.',
    );
  });

  it('sets letters, the title block and the page order', async () => {
    const load = vi.fn<typeof getCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: { settings: null, updated_by: '', updated_at: null },
    });
    renderApp(<Harness load={load} save={vi.fn<typeof saveCompanySettings>()} />);
    await waitFor(() => {
      expect(load).toHaveBeenCalled();
    });
    fireEvent.change(screen.getByLabelText('Designation letters: Contactor'), {
      target: { value: 'k' },
    });
    fireEvent.click(screen.getByLabelText('Customer'));
    fireEvent.click(screen.getByRole('button', { name: 'Move Parts list up' }));
    const settings = JSON.parse(screen.getByTestId('text').textContent) as Record<string, unknown>;
    expect(settings.letters).toEqual({ contactor: 'K' });
    expect((settings.title_fields as string[]).at(-1)).toBe('customer');
    expect((settings.page_order as string[]).slice(-2)).toEqual(['parts', 'cables']);
  });

  it('keeps settings already typed, and holds the form while the JSON is broken', async () => {
    const load = vi.fn<typeof getCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: { settings: { key: 'saved' }, updated_by: '', updated_at: null },
    });
    const save = vi.fn<typeof saveCompanySettings>();
    renderApp(<Harness load={load} save={save} initial="{broken" />);
    await waitFor(() => {
      expect(load).toHaveBeenCalled();
    });
    expect(screen.getByTestId('text').textContent).toBe('{broken');
    expect(screen.getByRole('alert').textContent).toContain('not valid JSON');
    expect(screen.getByLabelText<HTMLInputElement>('Company name').disabled).toBe(true);
  });
});
