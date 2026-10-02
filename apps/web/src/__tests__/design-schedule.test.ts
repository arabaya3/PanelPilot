import { describe, expect, it } from 'vitest';

import {
  blankBoard,
  blankLoad,
  isComplete,
  rowsFrom,
  toRequest,
  withLanguage,
  type ProjectInfo,
} from '@/components/design/schedule';

/** The pure helpers behind the design form. */

const INFO: ProjectInfo = {
  name: ' Tower ',
  number: '',
  customer: '',
  consultant: '',
  contractor: '',
};

describe('design schedule helpers', () => {
  it('turns the form into a request, blanks as nulls and text trimmed', () => {
    const main = blankBoard(0, 1, 'MDB');
    main.loads = [{ ...blankLoad(1), description: ' Lights ', power: '1' }];
    const sub = blankBoard(2, 3, 'DB2', 'MDB');
    sub.faultLevel = '6';
    const request = toRequest(INFO, [main, sub], null);
    expect(request.info.name).toBe('Tower');
    expect(request.boards[0]).toMatchObject({ name: 'MDB', fed_from: null, location: null });
    expect(request.boards[0]?.loads[0]).toMatchObject({
      description: 'Lights',
      power_kw: '1',
      power_factor: null,
      phases: 1,
    });
    expect(request.boards[1]).toMatchObject({ fed_from: 'MDB' });
    expect(request.boards[1]?.supply?.fault_level_ka).toBe('6');
  });

  it('sends cable lengths, and a feeder length only for a fed board', () => {
    const main = blankBoard(0, 1, 'MDB');
    main.loads = [{ ...blankLoad(1), description: 'Pump', power: '5', length: ' 45 ' }];
    main.feederLength = '30';
    const sub = { ...blankBoard(2, 3, 'DB2', 'MDB'), feederLength: '80' };
    const request = toRequest(INFO, [main, sub], null);
    expect(request.boards[0]?.loads[0]?.length_m).toBe('45');
    expect(request.boards[0]?.feeder_length_m).toBeNull();
    expect(request.boards[1]?.feeder_length_m).toBe('80');
    expect(request.boards[1]?.loads[0]?.length_m).toBeNull();
  });

  it('adds the drawing language unless the settings state one', () => {
    expect(withLanguage(null, 'en')).toBeNull();
    expect(withLanguage(null, 'ar')).toEqual({ language: 'ar' });
    expect(withLanguage({ key: 'acme' }, 'en')).toEqual({ key: 'acme' });
    expect(withLanguage({ key: 'acme' }, 'ar')).toEqual({ key: 'acme', language: 'ar' });
    expect(withLanguage({ language: 'en' }, 'ar')).toEqual({ language: 'en' });
  });

  it('is complete only when every board names itself and fills every row', () => {
    const main = blankBoard(0, 1, 'MDB');
    expect(isComplete(INFO, [main])).toBe(false);
    main.loads = [{ ...blankLoad(1), description: 'Lights', power: '1' }];
    expect(isComplete(INFO, [main])).toBe(true);
    expect(isComplete(INFO, [{ ...main, name: ' ' }])).toBe(false);
    expect(isComplete({ ...INFO, name: '' }, [main])).toBe(false);
  });

  it('keys imported rows from the number given', () => {
    const rows = rowsFrom(
      [
        {
          description: 'AC',
          load: 'air_conditioning',
          power_kw: '4',
          phases: 3,
          power_factor: '0.85',
          controlled: false,
          starter: null,
        },
      ],
      40,
    );
    expect(rows).toEqual([
      {
        key: 40,
        description: 'AC',
        load: 'air_conditioning',
        power: '4',
        phases: '3',
        powerFactor: '0.85',
        controlled: false,
        starter: '',
        length: '',
      },
    ]);
  });
});

describe('motor starters', () => {
  it('sends a chosen starter and nothing for other loads', () => {
    const main = blankBoard(0, 1, 'MCC');
    main.loads = [
      { ...blankLoad(1), description: 'Pump', load: 'motor', power: '7.5', starter: 'dol' },
      { ...blankLoad(2), description: 'Lights', power: '1' },
    ];
    const loads = toRequest(INFO, [main], null).boards[0]?.loads;
    expect(loads?.map((load) => load.starter)).toEqual(['dol', null]);
  });
});
