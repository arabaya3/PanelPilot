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
      saved: {
        settings: { key: 'acme', name: 'Acme' },
        updated_by: 'e',
        updated_at: null,
        permissions: ['edit_company_settings'],
      },
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

describe('company settings for a member who is not the owner', () => {
  it('shows the saved settings but does not offer to save them', async () => {
    const load = vi.fn<typeof getCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: { settings: { key: 'acme', name: 'Acme' }, updated_by: 'e', updated_at: null },
    });
    const save = vi.fn<typeof saveCompanySettings>();
    renderApp(<Harness load={load} save={save} />);
    await waitFor(() => {
      expect(screen.getByLabelText<HTMLInputElement>('Company name').value).toBe('Acme');
    });
    const button = screen.getByRole<HTMLButtonElement>('button', { name: 'Save for the company' });
    expect(button.disabled).toBe(true);
    expect(screen.getByTestId('settings-not-permitted').textContent).toContain(
      "Only the account's owner or an administrator can save the company settings.",
    );
    fireEvent.click(button);
    expect(save).not.toHaveBeenCalled();
  });

  it('says why when the server refuses the save, and stops offering it', async () => {
    const load = vi.fn<typeof getCompanySettings>().mockResolvedValue({
      kind: 'settings',
      saved: {
        settings: { key: 'acme' },
        updated_by: 'e',
        updated_at: null,
        permissions: ['edit_company_settings'],
      },
    });
    const save = vi.fn<typeof saveCompanySettings>().mockResolvedValue({
      kind: 'refused',
      code: 'settings_not_permitted',
      detail: "only the account's owner changes the company settings",
    });
    renderApp(<Harness load={load} save={save} />);
    const button = screen.getByRole<HTMLButtonElement>('button', { name: 'Save for the company' });
    await waitFor(() => {
      expect(button.disabled).toBe(false);
    });
    fireEvent.click(button);
    await waitFor(() => {
      expect(screen.getByTestId('settings-message').textContent).toBe(
        "Only the account's owner or an administrator can change the company settings.",
      );
    });
    expect(button.disabled).toBe(true);
  });
});

describe('a save the server refuses', () => {
  const OWNER = vi.fn<typeof getCompanySettings>().mockResolvedValue({
    kind: 'settings',
    saved: {
      settings: { key: 'acme', name: 'Acme' },
      updated_by: 'e',
      updated_at: null,
      permissions: ['edit_company_settings'],
    },
  });

  async function saveRefused(refusal: Awaited<ReturnType<typeof saveCompanySettings>>) {
    const save = vi.fn<typeof saveCompanySettings>().mockResolvedValue(refusal);
    renderApp(<Harness load={OWNER} save={save} />);
    const button = screen.getByRole<HTMLButtonElement>('button', { name: 'Save for the company' });
    await waitFor(() => {
      expect(button.disabled).toBe(false);
    });
    return { save, button };
  }

  it('says a missing profile key beside the key, and keeps what was typed', async () => {
    const { button } = await saveRefused({
      kind: 'refused',
      code: 'profile_no_key',
      detail: 'a company profile needs a key',
    });
    fireEvent.change(screen.getByLabelText('Profile key'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Company name'), { target: { value: 'Acme Ltd' } });
    fireEvent.click(button);
    const key = screen.getByLabelText<HTMLInputElement>('Profile key');
    await waitFor(() => {
      expect(key.getAttribute('aria-invalid')).toBe('true');
    });
    const describedBy = key.getAttribute('aria-describedby') ?? '';
    expect(document.getElementById(describedBy)?.textContent).toBe(
      'The company settings need a key.',
    );
    expect(screen.getByRole('alert').textContent).toBe('The company settings need a key.');
    expect(screen.getByLabelText<HTMLInputElement>('Company name').value).toBe('Acme Ltd');
    // Typing a key clears the field's error.
    fireEvent.change(key, { target: { value: 'acme' } });
    expect(key.getAttribute('aria-invalid')).toBeNull();
  });

  it('marks the setting it names as not valid', async () => {
    const { button } = await saveRefused({
      kind: 'refused',
      code: 'profile_setting_invalid',
      detail: 'profile setting max_circuits_per_rcd: Input should be a valid integer',
      params: { setting: 'max_circuits_per_rcd' },
      values: { setting: 'max_circuits_per_rcd' },
    });
    fireEvent.click(button);
    const field = screen.getByLabelText<HTMLInputElement>('Circuits per RCD');
    await waitFor(() => {
      expect(field.getAttribute('aria-invalid')).toBe('true');
    });
    expect(screen.getByRole('alert').textContent).toContain('max_circuits_per_rcd');
  });

  it('moves focus to the field a refused save names', async () => {
    const { button } = await saveRefused({
      kind: 'refused',
      code: 'profile_setting_invalid',
      detail: 'profile setting circuit_rules.socket.breaker_a: Input should be a valid decimal',
      params: { setting: 'circuit_rules.socket.breaker_a' },
    });
    button.focus();
    fireEvent.click(button);
    const field = screen.getByLabelText<HTMLInputElement>('Sockets: Breaker A');
    await waitFor(() => {
      expect(document.activeElement).toBe(field);
    });
    expect(field.getAttribute('aria-invalid')).toBe('true');
  });

  it('opens the JSON text and focuses it for settings no field shows', async () => {
    const { button } = await saveRefused({
      kind: 'refused',
      code: 'profile_unknown_settings',
      detail: "unknown profile settings: ['colour']",
      values: { settings: ['colour'] },
    });
    fireEvent.click(button);
    const json = screen.getByLabelText<HTMLTextAreaElement>('Settings as JSON');
    await waitFor(() => {
      expect(document.activeElement).toBe(json);
    });
    expect(json.closest('details')?.open).toBe(true);
  });

  it('leaves focus alone for a refusal that names no setting', async () => {
    const { button } = await saveRefused({ kind: 'refused', code: 'plan_feature', detail: '' });
    button.focus();
    fireEvent.click(button);
    await screen.findByRole('alert');
    expect(document.activeElement).toBe(button);
  });

  it('lists the unknown settings', async () => {
    const { button } = await saveRefused({
      kind: 'refused',
      code: 'profile_unknown_settings',
      detail: "unknown profile settings: ['colour', 'size']",
      params: {},
      values: { settings: ['colour', 'size'] },
    });
    fireEvent.click(button);
    expect((await screen.findByRole('alert')).textContent).toBe(
      'Unknown company settings: colour, size.',
    );
  });

  it.each([
    ['plan_feature', 'Your plan does not include this; see Pricing to change it.'],
    [
      'request_invalid',
      'The server did not accept this request as entered. Check the fields and try again.',
    ],
  ])('says %s as an alert beside the save', async (code, text) => {
    const { button } = await saveRefused({ kind: 'refused', code, detail: '' });
    fireEvent.click(button);
    expect((await screen.findByRole('alert')).textContent).toContain(text);
  });

  it.each(['ar', 'he'] as const)('says a missing key in %s', async (locale) => {
    const save = vi.fn<typeof saveCompanySettings>().mockResolvedValue({
      kind: 'refused',
      code: 'profile_no_key',
      detail: 'a company profile needs a key',
    });
    renderApp(<Harness load={OWNER} save={save} />, { locale });
    const button = await waitFor(() => {
      const found = document.querySelector<HTMLButtonElement>(
        '[data-testid="company-settings"] button.btn-secondary:not([aria-label])',
      );
      if (!found || found.disabled) throw new Error('not ready');
      return found;
    });
    fireEvent.click(button);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).not.toBe('');
    expect(alert.textContent).not.toBe('The company settings need a key.');
  });
});
