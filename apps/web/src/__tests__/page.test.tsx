import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { renderApp } from './helpers';

import HomePage from '@/app/page';

// A smoke test, deliberately: it proves the toolchain renders a real component
// through the same `@/` alias the app uses. Its job is to fail if the test
// runner, the JSX transform, or module resolution breaks.
describe('HomePage', () => {
  it('renders the product name', () => {
    renderApp(
      <>
        <HomePage />
      </>,
      { theme: 'light' },
    );
    // getByText throws when absent, so this is a real assertion, not a no-op.
    expect(screen.getByText('PanelPilot')).toBeTruthy();
  });
});
