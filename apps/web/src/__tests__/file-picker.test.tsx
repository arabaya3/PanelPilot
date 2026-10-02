import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { FilePicker } from '@/components/file-picker';

import { renderApp } from './helpers';

/** The file input stands in the page's language, not the browser's. */

describe('file picker', () => {
  it('names the chosen file and hands it on', () => {
    const onFile = vi.fn();
    renderApp(<FilePicker id="f" label="Price list" accept=".csv" onFile={onFile} />);
    expect(screen.getByText('No file chosen')).toBeTruthy();
    const file = new File(['Key,Price'], 'prices.csv', { type: 'text/csv' });
    fireEvent.change(screen.getByLabelText('Price list'), { target: { files: [file] } });
    expect(onFile).toHaveBeenCalledWith(file);
    expect(screen.getByText('prices.csv')).toBeTruthy();
  });

  it('cannot be used while disabled', () => {
    renderApp(<FilePicker id="f" label="Schedule" accept=".csv" disabled onFile={vi.fn()} />);
    expect(screen.getByLabelText<HTMLInputElement>('Schedule').disabled).toBe(true);
  });
});
