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

/**
 * Give jsdom a viewport.
 *
 * jsdom performs no layout, so every element reports `clientHeight: 0` and a
 * zeroed `getBoundingClientRect`. A windowed list asks the scroll container
 * how tall it is to decide what to render, is told zero, and renders nothing —
 * so a transcript test would assert against an empty container and a component
 * that rendered no messages at all would pass every one of them.
 *
 * These stubs are the minimum needed for the virtualizer to believe it has a
 * viewport. They are deliberately not a layout engine: anything that depends
 * on real measurement belongs in a browser test, not here.
 */
// `offsetWidth`/`offsetHeight` are what the virtualizer actually measures the
// scroll container with — not `clientHeight`, and not `getBoundingClientRect`.
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
  configurable: true,
  get(this: HTMLElement) {
    return this.getAttribute('data-testid') === 'transcript' ? 600 : 160;
  },
});

Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
  configurable: true,
  get: () => 800,
});

Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
  configurable: true,
  get(this: HTMLElement) {
    return this.getAttribute('data-testid') === 'transcript' ? 600 : 160;
  },
});

Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
  configurable: true,
  get: () => 800,
});

HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect(this: HTMLElement) {
  const height = this.getAttribute('data-testid') === 'transcript' ? 600 : 160;
  return {
    x: 0,
    y: 0,
    top: 0,
    // DOMRect field names, not CSS: a rect has no direction to flip with.
    left: 0, // allow-physical-property
    right: 800, // allow-physical-property
    bottom: height,
    width: 800,
    height,
    toJSON: () => ({}),
  };
};

// The virtualizer observes the scroll element for resizes; jsdom has no
// implementation, and an absent constructor throws on construction.
const StubResizeObserver = class {
  observe() {
    /* no layout to observe */
  }
  unobserve() {
    /* no layout to observe */
  }
  disconnect() {
    /* no layout to observe */
  }
};
globalThis.ResizeObserver = StubResizeObserver;

// `scrollToIndex` calls this on the scroll container.
HTMLElement.prototype.scrollTo = function scrollTo() {
  /* no scrolling in jsdom */
};
