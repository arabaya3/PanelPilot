import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AppShell } from '@/components/app-shell';

import { renderApp } from './helpers';

vi.mock('next/navigation', () => ({ usePathname: () => '/calc' }));

describe('AppShell', () => {
  it('lists every tool as a link, in order, and marks the current one', () => {
    renderApp(<AppShell>body</AppShell>, { locale: 'en' });
    const nav = screen.getByRole('navigation', { name: 'Main' });
    const links = within(nav).getAllByRole('link');
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/design',
      '/',
      '/calc',
      '/plc',
      '/search',
      '/review',
      '/team',
      '/pricing',
    ]);
    const current = within(nav).getByRole('link', { name: 'Calculate' });
    expect(current.getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Projects' }).getAttribute('aria-current')).toBe(
      null,
    );
  });

  it('opens the navigation drawer from a labelled menu button and closes it', () => {
    renderApp(<AppShell>body</AppShell>, { locale: 'en' });
    const button = screen.getByRole('button', { name: 'Open navigation' });
    expect(button.getAttribute('aria-expanded')).toBe('false');
    expect(document.getElementById(button.getAttribute('aria-controls') ?? '')).not.toBeNull();
    // One nav (the rail) while closed: the drawer's copy is not rendered.
    expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(1);

    fireEvent.click(button);
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getAllByRole('navigation', { name: 'Main' })).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(button.getAttribute('aria-expanded')).toBe('false');
  });

  it('renders the context slot and translates the shell', () => {
    renderApp(<AppShell context={<span>Project A</span>}>body</AppShell>, { locale: 'ar' });
    expect(screen.getAllByText('Project A').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'فتح قائمة التنقل' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'المشاريع' })).toBeTruthy();
  });
});
