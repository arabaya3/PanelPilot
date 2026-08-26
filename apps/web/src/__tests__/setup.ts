/**
 * Test setup.
 *
 * Testing Library normally registers its own cleanup via the global `afterEach`,
 * which only exists when Vitest runs with `globals: true`. This project does
 * not, so cleanup is registered explicitly — without it, rendered DOM from one
 * test is still mounted during the next, and a `getByTestId` that should match
 * one element finds several.
 */

import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

afterEach(() => {
  cleanup();
});
