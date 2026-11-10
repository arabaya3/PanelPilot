import { describe, expect, it } from 'vitest';

import { parseDraft } from '@/components/design/draft';
import {
  blankLink,
  calculatedValues,
  decodeTerminal,
  encodeTerminal,
  feederLinksOf,
  isBlankLink,
  linkFindingsOf,
  linkFormsOf,
  linkValueProblem,
  parseLink,
  storedLinksOf,
  terminalOptions,
  valuesDiffer,
  writesLinks,
  type LinkForm,
} from '@/components/design/feeder-link-model';
import { blankBoard, fromRequest, toRequest, type BoardForm } from '@/components/design/schedule';
import type { SavedRequest } from '@/lib/design';

import { LINK_MODEL } from './feeder-link-fixtures';

/** M7a: the feeder link's request entry, saved form, draft, terminals and comparisons. Synthetic data. */

function link(patch: (form: LinkForm) => void): LinkForm {
  const form = blankLink();
  patch(form);
  return form;
}

function boards(): [BoardForm, BoardForm] {
  const main = blankBoard(0, 1, 'MDB');
  const fed = { ...blankBoard(2, 3, 'DB1'), fedFrom: 'MDB', feederLength: '40' };
  return [main, fed];
}

describe('the request entry (feeder_links)', () => {
  it('sends exactly the contract shape: fed board, ends with terminal and/or label, typed attributes', () => {
    const [main, fed] = boards();
    const q1 = encodeTerminal({ component: 'cmp-q1', terminal: 't-2' });
    fed.link = link((f) => {
      f.from.terminal = q1;
      f.to.label = ' Incomer lugs ';
      f.attributes.cores = '5';
      f.attributes.cross_section_mm2 = '16';
      f.attributes.colour = 'black';
    });
    expect(feederLinksOf([main, fed])).toEqual([
      {
        fed_board: 'DB1',
        from_end: { terminal: { component: 'cmp-q1', terminal: 't-2' } },
        to_end: { label: 'Incomer lugs' },
        attributes: { cores: 5, cross_section_mm2: '16', colour: 'black' },
      },
    ]);
  });

  it('leaves blank ends and attributes out (unset), never defaults them', () => {
    const [main, fed] = boards();
    fed.link = link((f) => {
      f.attributes.number = 'W12';
    });
    expect(feederLinksOf([main, fed])).toEqual([
      { fed_board: 'DB1', attributes: { number: 'W12' } },
    ]);
  });

  it('sends a strip point with its side', () => {
    const [main, fed] = boards();
    fed.link = link((f) => {
      f.to.terminal = encodeTerminal({ point: 'pt-9', side: 'inner' });
    });
    expect(feederLinksOf([main, fed])[0]?.to_end).toEqual({
      terminal: { point: 'pt-9', side: 'inner' },
    });
  });

  it('sends nothing for a board no longer fed from another, or a blank link', () => {
    const [main, fed] = boards();
    fed.link = link((f) => {
      f.attributes.colour = 'grey';
    });
    expect(feederLinksOf([main, { ...fed, fedFrom: '' }])).toEqual([]);
    expect(feederLinksOf([main, { ...fed, link: blankLink() }])).toEqual([]);
  });

  it('adds feeder_links to the request only where a link is entered', () => {
    const plain = toRequest(
      { name: 'P', number: '', customer: '', consultant: '', contractor: '' },
      boards(),
      null,
    );
    expect('feeder_links' in plain).toBe(false);
    const withLink = boards();
    withLink[1].link = link((f) => {
      f.from.label = 'Q1 out';
    });
    const request = toRequest(
      { name: 'P', number: '', customer: '', consultant: '', contractor: '' },
      withLink,
      null,
    ) as unknown as { feeder_links: unknown[] };
    expect(request.feeder_links).toEqual([{ fed_board: 'DB1', from_end: { label: 'Q1 out' } }]);
  });
});

describe('a saved request back as the form', () => {
  const saved = {
    info: { name: 'P', number: '', customer: '', consultant: '', contractor: '' },
    boards: [
      { name: 'MDB', fed_from: null, loads: [] },
      { name: 'DB1', fed_from: 'MDB', feeder_length_m: '40', loads: [] },
    ],
    profile: null,
    feeder_links: [
      {
        fed_board: 'DB1',
        from_end: { terminal: { point: 'pt-1', side: 'outer' } },
        to_end: { label: 'Lugs' },
        attributes: { cores: 4, length_m: '38.5' },
      },
    ],
  } as unknown as SavedRequest;

  it('restores each fed board’s link, blanks staying blank', () => {
    const { boards: form } = fromRequest(saved, 0);
    expect(form[0]?.link).toBeUndefined();
    const restored = form[1]?.link;
    expect(decodeTerminal(restored?.from.terminal ?? '')).toEqual({ point: 'pt-1', side: 'outer' });
    expect(restored?.to).toEqual({ terminal: '', label: 'Lugs' });
    expect(restored?.attributes).toMatchObject({
      cores: '4',
      length_m: '38.5',
      material: '',
      cross_section_mm2: '',
    });
  });

  it('round-trips through the request unchanged', () => {
    const { info, boards: form } = fromRequest(saved, 0);
    const again = toRequest(info, form, null) as unknown as { feeder_links: unknown };
    expect(again.feeder_links).toEqual([
      {
        fed_board: 'DB1',
        from_end: { terminal: { point: 'pt-1', side: 'outer' } },
        to_end: { label: 'Lugs' },
        attributes: { cores: 4, length_m: '38.5' },
      },
    ]);
  });

  it('reads nothing from a request without links', () => {
    expect(linkFormsOf({ boards: [] }).size).toBe(0);
    expect(linkFormsOf(null).size).toBe(0);
  });
});

describe('the unsaved-work draft keeps the link form', () => {
  it('parses a stored board link and drops one it did not write', () => {
    const typed = link((f) => {
      f.to.label = 'Lugs';
      f.attributes.insulation = 'XLPE';
    });
    const board = (extra: Record<string, unknown>) => ({
      name: 'DB1',
      location: '',
      voltage: '400',
      phases: '3',
      faultLevel: '',
      earthing: 'TN-S',
      earthLoop: '',
      fedFrom: 'MDB',
      feederLength: '',
      loads: [],
      ...extra,
    });
    const draft = (extra: Record<string, unknown>) =>
      parseDraft({
        v: 1,
        session: 's',
        clean: '',
        profileText: '',
        market: '',
        project: null,
        info: { name: '', number: '', customer: '', consultant: '', contractor: '' },
        language: null,
        boards: [board(extra)],
      });
    expect(draft({ link: typed })?.boards[0]?.link).toEqual(typed);
    expect(draft({ link: { from: 'x' } })?.boards[0]?.link).toBeUndefined();
    expect(draft({})?.boards[0]?.link).toBeUndefined();
    expect(parseLink(blankLink())).toBeUndefined();
  });
});

describe('terminals an end can name', () => {
  it('lists only the board’s own modelled terminals and strip points', () => {
    const supply = terminalOptions(LINK_MODEL, 'MDB');
    expect(supply.map((option) => option.text)).toEqual(['Q1:2', 'X0:1 (outer)']);
    expect(terminalOptions(LINK_MODEL, 'DB1').map((option) => option.text)).toEqual([
      'X1:L1 (inner)',
    ]);
    expect(terminalOptions(null, 'MDB')).toEqual([]);
  });

  it('encodes and decodes a terminal, refusing anything else', () => {
    const ref = { component: 'c', terminal: 't' };
    expect(decodeTerminal(encodeTerminal(ref))).toEqual(ref);
    expect(decodeTerminal('')).toBeNull();
    expect(decodeTerminal('{"point":"p","side":"left"}')).toBeNull();
    expect(decodeTerminal('nope')).toBeNull();
  });
});

describe('values, comparisons and blanks', () => {
  it('checks typed values by type and range only', () => {
    expect(linkValueProblem('cores', '0')).toBe('cores');
    expect(linkValueProblem('cores', '101')).toBe('cores');
    expect(linkValueProblem('cores', '2.5')).toBe('cores');
    expect(linkValueProblem('cores', '5')).toBeNull();
    expect(linkValueProblem('cross_section_mm2', '0')).toBe('positive');
    expect(linkValueProblem('length_m', '-1')).toBe('nonNegative');
    expect(linkValueProblem('length_m', '0')).toBeNull();
    expect(linkValueProblem('colour', 'anything')).toBeNull();
    expect(linkValueProblem('cores', '')).toBeNull();
  });

  it('reads the feeder calculation’s cable from the supply board’s circuit that feeds the board', () => {
    const design = {
      boards: [
        {
          name: 'MDB',
          circuits: [
            { feeds: null, cable_id: 'w1' },
            { feeds: 'DB1', cable_id: 'w2' },
          ],
          cables: [
            { id: 'w1', cores: 3, cross_section_mm2: '2.5', material: 'copper', insulation: 'PVC' },
            {
              id: 'w2',
              cores: 5,
              cross_section_mm2: '16',
              material: 'copper',
              insulation: 'XLPE',
              length_m: null,
            },
          ],
        },
        { name: 'DB1', feeder_length_m: '40' },
      ],
    };
    expect(calculatedValues(design, 'MDB', 'DB1')).toEqual({
      cores: '5',
      cross_section_mm2: '16',
      material: 'copper',
      insulation: 'XLPE',
      length_m: '40',
    });
    expect(calculatedValues(design, 'MDB', 'DB9')).toBeNull();
    expect(calculatedValues(null, 'MDB', 'DB1')).toBeNull();
  });

  it('compares numbers by value and text ignoring case; a blank never differs', () => {
    expect(valuesDiffer('cross_section_mm2', '16.0', '16')).toBe(false);
    expect(valuesDiffer('cross_section_mm2', '25', '16')).toBe(true);
    expect(valuesDiffer('material', 'Copper', 'copper')).toBe(false);
    // The designer stores the symbol; the form offers the name: the same metal.
    expect(valuesDiffer('material', 'copper', 'Cu')).toBe(false);
    expect(valuesDiffer('material', 'aluminium', 'Cu')).toBe(true);
    expect(valuesDiffer('insulation', 'PVC', 'XLPE')).toBe(true);
    expect(valuesDiffer('cores', '', '5')).toBe(false);
    expect(valuesDiffer('cores', '4', null)).toBe(false);
  });

  it('treats a link with nothing entered as none', () => {
    expect(isBlankLink(undefined)).toBe(true);
    expect(isBlankLink(blankLink())).toBe(true);
    expect(isBlankLink(link((f) => (f.to.label = '  ')))).toBe(true);
    expect(isBlankLink(link((f) => (f.attributes.colour = 'red')))).toBe(false);
  });
});

describe('what the server says', () => {
  it('shows links only where the opened project says the deployment writes pp-design/3', () => {
    expect(writesLinks({ written_schema: 'pp-design/3', snapshot_schema: 'pp-design/2' })).toBe(
      true,
    );
    expect(writesLinks({ snapshot_schema: 'pp-design/3' })).toBe(true);
    expect(writesLinks({ snapshot_schema: 'pp-design/2' })).toBe(false);
    expect(writesLinks({ written_schema: 'pp-design/1', snapshot_schema: 'pp-design/1' })).toBe(
      false,
    );
    expect(writesLinks(null)).toBe(false);
  });

  it('reads stored links and keeps only the link findings', () => {
    const stored = { id: 'l-1', ends: [], feeder: { supply_board: 'MDB', fed_board: 'DB1' } };
    expect(storedLinksOf({ links: [stored, { bogus: true }] })).toEqual([stored]);
    expect(storedLinksOf({ model_v2: { links: [stored] } })).toEqual([stored]);
    expect(storedLinksOf({})).toEqual([]);
    const findings = linkFindingsOf([
      { code: 'link_endpoint_unresolved' },
      { code: 'cable_undersized' },
      { code: 'link_value_differs_from_calculation' },
    ]);
    expect(findings.map((finding) => finding.code)).toEqual([
      'link_endpoint_unresolved',
      'link_value_differs_from_calculation',
    ]);
  });
});

describe('a configured save with only link edits pending', () => {
  it('counts link edits as no change to the design, and anything else as one', async () => {
    const { designDiffers } = await import('@/components/design-screen');
    const base = { info: { name: 'SYN' }, boards: [{ name: 'SB-1', fed_from: 'MB-1', loads: [] }] };
    const clean = JSON.stringify(base);
    const linkOnly = JSON.stringify({
      ...base,
      boards: [{ ...base.boards[0], link: { from: { terminal: 'x' } } }],
    });
    const designChanged = JSON.stringify({
      ...base,
      boards: [{ ...base.boards[0], fed_from: 'MB-2' }],
    });
    expect(designDiffers(linkOnly, clean)).toBe(false);
    expect(designDiffers(designChanged, clean)).toBe(true);
    expect(designDiffers(clean, clean)).toBe(false);
    // A placeholder that is not a work snapshot compares as text, never throws.
    expect(designDiffers('stored', clean)).toBe(true);
    expect(designDiffers('stored', 'stored')).toBe(false);
  });
});
