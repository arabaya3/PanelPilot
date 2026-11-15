import { describe, expect, it } from 'vitest';

import {
  addStrip,
  addStripPoint,
  emptyConfiguration,
  removeEntry,
  removeStripPoint,
  stripPoints,
  stripsOf,
} from '@/components/design/configuration-model';

/**
 * Adversarial review of M7b round 2. SYNTHETIC markings only.
 * The server derives a point id from (strip id, point id), and a feeder link
 * stores that derived id; reusing a removed id silently retargets the link.
 */
describe('M7b review 2: ids are never reused after a removal', () => {
  it('a point added after removing the last one gets a new id', () => {
    let config = addStrip(emptyConfiguration(), {
      board: 'MB-1',
      template: null,
      designation: '-X1',
    });
    const stripId = stripsOf(config)[0]?.id ?? '';
    config = addStripPoint(config, stripId, { marking: '1', level: null });
    config = addStripPoint(config, stripId, { marking: '2', level: null });
    const removed = stripPoints(stripsOf(config)[0] ?? {})[1]?.id ?? '';
    config = removeStripPoint(config, stripId, removed);
    config = addStripPoint(config, stripId, { marking: '7', level: null });
    const added = stripPoints(stripsOf(config)[0] ?? {}).find((p) => p.marking === '7');
    expect(added?.id).not.toBe(removed);
  });

  it('a strip added after removing one gets a new id', () => {
    let config = addStrip(emptyConfiguration(), {
      board: 'MB-1',
      template: null,
      designation: '-X1',
    });
    const removed = stripsOf(config)[0]?.id ?? '';
    config = removeEntry(config, 'strips', removed);
    config = addStrip(config, { board: 'SB-1', template: null, designation: '-X9' });
    expect(stripsOf(config)[0]?.id).not.toBe(removed);
  });
});

describe('a conflict names its entry by designation', () => {
  it('says the strip designation, not its internal id', async () => {
    const { entryName } = await import('@/components/design/configuration-model');
    const doc = {
      strips: [
        {
          id: 'strip-2',
          board: 'SB-1',
          template: null,
          designation: { function: null, location: null, product: 'X2' },
        },
      ],
    };
    expect(entryName(doc, 'strip', 'strip-2')).toBe('X2');
    expect(
      entryName(
        { strips: [{ id: 'strip-3', board: 'SB-1', designation: null }] },
        'strip',
        'strip-3',
      ),
    ).toBe('strip-3');
  });
});
