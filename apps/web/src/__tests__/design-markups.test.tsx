import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { MarkupsPanel } from '@/components/design/markups-panel';
import { readMarkups, type DesignProject } from '@/lib/design';

import { renderApp } from './helpers';

/** A reviewed drawing set's comments, listed on the drawings they sit on. */

const PROJECT = { info: { name: 'Tower' }, boards: [], parts: [] } as unknown as DesignProject;

describe('the markups panel', () => {
  it('lists each comment with its page, board and label', async () => {
    const readImpl = vi.fn<typeof readMarkups>().mockResolvedValue({
      kind: 'read',
      report: {
        matched: true,
        markups: [
          {
            page: 3,
            kind: 'Text',
            author: 'Consultant',
            text: 'Make this C20',
            sheet: 'Main power',
            board: 'MDB',
            near: '-Q3',
          },
        ],
      },
    });
    renderApp(<MarkupsPanel token="tok" project={PROJECT} profile={null} readImpl={readImpl} />);
    const file = new File(['%PDF'], 'set.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('Reviewed drawing set (PDF)'), {
      target: { files: [file] },
    });
    const result = await screen.findByTestId('markups-result');
    expect(result.textContent).toContain('1 comment, each placed on its drawing.');
    expect(result.textContent).toContain('Page 3 · MDB · near -Q3 · Consultant');
    expect(result.textContent).toContain('Make this C20');
    expect(readImpl.mock.calls[0]?.[0]).toMatchObject({ filename: 'set.pdf', project: PROJECT });
  });

  it('says a refusal in the reader’s words', async () => {
    const readImpl = vi.fn<typeof readMarkups>().mockResolvedValue({
      kind: 'refused',
      detail: 'x',
      code: 'markups_unreadable',
    });
    renderApp(<MarkupsPanel token="tok" project={PROJECT} profile={null} readImpl={readImpl} />);
    fireEvent.change(screen.getByLabelText('Reviewed drawing set (PDF)'), {
      target: { files: [new File(['x'], 'x.pdf')] },
    });
    expect((await screen.findByTestId('markups-message')).textContent).toBe(
      'The file could not be read as a PDF.',
    );
  });
});

describe('readMarkups', () => {
  it('sends the PDF with the project and settings', async () => {
    const fetchImpl = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(JSON.stringify({ markups: [], matched: false })));
    const outcome = await readMarkups({
      token: 't',
      fetchImpl,
      file: new Blob(['%PDF']),
      filename: 'set.pdf',
      project: PROJECT,
      profile: { key: 'acme' },
    });
    expect(outcome.kind).toBe('read');
    const body = fetchImpl.mock.calls[0]?.[1]?.body as FormData;
    expect(body.get('project')).toBe(JSON.stringify(PROJECT));
    expect(body.get('profile')).toBe('{"key":"acme"}');
  });
});
