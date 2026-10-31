import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { Notice } from '@/components/ui';

/**
 * A notice's actions wrap below its text before the text gets narrower than
 * 12rem. `flex-1` (basis 0) let the text shrink to a few words a line beside
 * the actions at 390 px, where buttons covered it; the text's own basis is
 * what makes the row wrap. Measured in a real browser at 390 and 1280 px in
 * en, ar and he (see the commit); here only the classes can be checked.
 */
describe('Notice layout', () => {
  it('gives the text a 12rem basis so the actions wrap below it', () => {
    const { getByTestId } = render(
      <Notice testId="n" action={<button type="button">Act</button>}>
        Text
      </Notice>,
    );
    const notice = getByTestId('n');
    const text = notice.children[1];
    expect(text?.className).toContain('flex-[1_1_12rem]');
    expect(text?.className).not.toMatch(/(^|\s)flex-1(\s|$)/);
    expect(notice.children[2]?.className).toContain('max-w-full');
  });
});
