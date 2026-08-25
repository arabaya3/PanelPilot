import { dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import js from '@eslint/js';
import nextPlugin from '@next/eslint-plugin-next';
import prettier from 'eslint-config-prettier';
import tseslint from 'typescript-eslint';

const rootDir = dirname(fileURLToPath(import.meta.url));

export default tseslint.config(
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'] },

  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,

  // Next.js rules are wired straight from @next/eslint-plugin-next.
  //
  // We deliberately do NOT use `eslint-config-next`: its entrypoint calls
  // `require('@rushstack/eslint-patch/modern-module-resolution')`, which fails
  // under ESLint 9 flat config ("Failed to patch ESLint"). The plugin exposes
  // the same rules without the legacy resolution shim.
  {
    plugins: { '@next/next': nextPlugin },
    rules: {
      ...nextPlugin.configs.recommended.rules,
      ...nextPlugin.configs['core-web-vitals'].rules,
    },
  },

  {
    files: ['**/*.{ts,tsx,mts}'],
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: rootDir },
    },
    rules: {
      // API responses are typed in packages/shared-types; `any` here means the
      // contract drifted, so make it an error rather than a warning.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
      // Match tsconfig's noUnusedParameters, which exempts `_`-prefixed names.
      // Without this the two tools disagree and one of them is always wrong.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['../../*'],
              message: 'Use the @/ alias instead of reaching up through directories.',
            },
          ],
        },
      ],
    },
  },

  // Config files and other plain JS live outside the TypeScript project, so
  // the type-aware rules cannot run on them.
  {
    files: ['**/*.{js,mjs,cjs}'],
    extends: [tseslint.configs.disableTypeChecked],
  },

  // Must stay last: turns off stylistic rules Prettier owns.
  prettier,
);
