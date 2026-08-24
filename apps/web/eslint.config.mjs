import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import next from 'eslint-config-next';
import prettier from 'eslint-config-prettier';

export default tseslint.config(
  { ignores: ['.next/**', 'node_modules/**', 'next-env.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  ...next(),
  {
    languageOptions: {
      parserOptions: { projectService: true, tsconfigRootDir: import.meta.dirname },
    },
    rules: {
      // API responses are typed in packages/shared-types; `any` here means the
      // contract drifted, so make it an error rather than a warning.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
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
  // Must stay last: turns off stylistic rules Prettier owns.
  prettier,
);
