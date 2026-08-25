import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  resolve: {
    // Mirror the `@/*` alias from tsconfig.json. Without it, tsc and Vitest
    // would disagree about what `@/app/page` means.
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    // Tests live outside src/app so the Next App Router never treats them as
    // routes, and so `next build` does not compile them into the bundle.
    include: ['src/__tests__/**/*.test.{ts,tsx}'],
  },
});
