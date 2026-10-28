'use client';

import { useTranslations } from 'next-intl';
import { useCallback } from 'react';

import type { SignupProblem } from '@/lib/trial';

/**
 * Says why a signup was refused as entered, one sentence per field, in the
 * reader's language. Shared by the trial's signup and `/join`, which post to
 * the same endpoint and are refused for the same reasons.
 */
export function useSignupProblemText(): (problems: readonly SignupProblem[]) => string {
  const t = useTranslations('trial.invalid');
  return useCallback(
    (problems) =>
      problems
        .map((problem) => {
          switch (problem.field) {
            case 'email':
              return t('email');
            case 'password':
              if (problem.min !== undefined) return t('passwordShort', { min: problem.min });
              if (problem.max !== undefined) return t('passwordLong', { max: problem.max });
              return t('password');
            case 'full_name':
              return problem.max !== undefined ? t('nameLong', { max: problem.max }) : t('name');
            case 'other':
              return t('other');
          }
        })
        .join(' '),
    [t],
  );
}
