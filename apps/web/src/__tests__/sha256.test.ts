import { describe, expect, it } from 'vitest';

import { deriveId, sha256Hex } from '@/lib/sha256';

describe('sha256Hex', () => {
  it('matches the standard vectors', () => {
    expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
    expect(sha256Hex('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    // Several blocks.
    expect(sha256Hex('a'.repeat(1000))).toBe(
      '41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3',
    );
  });
});

describe('deriveId', () => {
  it("gives the ids the server's ids.derive gives (values computed with the API)", () => {
    expect(deriveId('k', 'component-a1b2c3')).toBe('k-ac9decc4a2bb0df9fccdd809');
    expect(deriveId('p', 'strip-x', 'direct', 'p-01')).toBe('p-bad4c28f0d6b4cf7fd60032c');
    expect(deriveId('s', 'strip-x')).toBe('s-543c1421a9e637a626173c00');
    // Non-ASCII text is hashed as UTF-8, as the server does (ensure_ascii=False).
    expect(deriveId('k', 'مكوّن-1')).toBe('k-93d77143f31a283652408b8c');
  });
});
