import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { pushView, useUnsavedChangesGuard } from '@/components/design/workspace';

/**
 * The guard's history buffer: pushed while work is unsaved so Back can be
 * caught, and stepped back over once the work is clean again -- otherwise the
 * next Back lands on the same page and appears to do nothing.
 */

afterEach(() => {
  window.history.replaceState(null, '', '/');
});

describe('the unsaved-changes history buffer', () => {
  it('adds one entry while dirty and removes it once clean again', async () => {
    window.history.replaceState(null, '', '/design');
    const start = window.history.length;
    const { rerender } = renderHook(({ dirty }) => useUnsavedChangesGuard(dirty), {
      initialProps: { dirty: false },
    });
    expect(window.history.length).toBe(start);

    rerender({ dirty: true });
    expect(window.history.length).toBe(start + 1);
    expect(window.history.state).toMatchObject({ __panelpilotUnsavedBuffer: true });

    rerender({ dirty: false });
    // Back over the buffer: the current entry is the page's own again.
    await waitFor(() => {
      expect(window.history.state).toBeNull();
    });
  });

  it('does not ask when the guard steps back over its own buffer', async () => {
    window.history.replaceState(null, '', '/design');
    const { result, rerender } = renderHook(({ dirty }) => useUnsavedChangesGuard(dirty), {
      initialProps: { dirty: true },
    });
    rerender({ dirty: false });
    await waitFor(() => {
      expect(window.history.state).toBeNull();
    });
    expect(result.current.asking).toBe(false);
  });

  it('still asks on Back while dirty', async () => {
    window.history.replaceState(null, '', '/design');
    const { result } = renderHook(() => useUnsavedChangesGuard(true));
    act(() => {
      window.history.back();
    });
    await waitFor(() => {
      expect(result.current.asking).toBe(true);
    });
  });

  it('leaves a view pushed while dirty in place once the work is clean', async () => {
    window.history.replaceState(null, '', '/design');
    const { rerender } = renderHook(({ dirty }) => useUnsavedChangesGuard(dirty), {
      initialProps: { dirty: false },
    });
    rerender({ dirty: true });
    act(() => {
      pushView('schedule');
    });
    // The view is a real entry, not the buffer it was pushed from.
    expect(window.history.state).not.toHaveProperty('__panelpilotUnsavedBuffer');
    rerender({ dirty: false });
    // Let a stray step back (if any) land before checking where the page is.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(window.location.search).toBe('?view=schedule');
  });

  it('does not step back over a buffer left by an earlier visit', async () => {
    window.history.replaceState(null, '', '/');
    window.history.pushState(
      { __panelpilotWorkspace: true, __panelpilotUnsavedBuffer: true },
      '',
      '/design',
    );
    window.history.pushState(null, '', '/other');
    act(() => {
      window.history.back();
    });
    await waitFor(() => {
      expect(window.location.pathname).toBe('/design');
    });
    renderHook(() => useUnsavedChangesGuard(false));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(window.location.pathname).toBe('/design');
    expect(window.history.state).toMatchObject({ __panelpilotUnsavedBuffer: true });
  });

  it('does not push or ask when a jump lands on another page', async () => {
    window.history.replaceState(null, '', '/');
    window.history.pushState(null, '', '/design');
    const { result } = renderHook(() => useUnsavedChangesGuard(true));
    const length = window.history.length;
    act(() => {
      window.history.go(-2);
    });
    await waitFor(() => {
      expect(window.location.pathname).toBe('/');
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(window.location.pathname).toBe('/');
    expect(window.history.length).toBe(length);
    expect(result.current.asking).toBe(false);
  });
});
