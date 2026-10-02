import type {
  LoadKind,
  LoadScheduleImport,
  MotorStarter,
  ProjectDesignRequest,
} from '@/lib/design';

/** One load schedule row as the engineer edits it: every field a string. */
export type Load = {
  key: number;
  description: string;
  load: LoadKind;
  power: string;
  phases: '1' | '3';
  powerFactor: string;
  controlled: boolean;
  /** How a motor is started; empty for any other load. */
  starter: MotorStarter | '';
  /** Cable route length in metres; empty leaves voltage drop unchecked. */
  length: string;
};

/** One board's header and schedule as the engineer edits it. */
export type BoardForm = {
  key: number;
  name: string;
  location: string;
  voltage: string;
  phases: '1' | '3';
  faultLevel: string;
  /** The board that feeds this one; empty for the project's own supply. */
  fedFrom: string;
  /** Route length of the cable from `fedFrom`, in metres. */
  feederLength: string;
  loads: Load[];
};

/** Title-block fields for the whole project. */
export type ProjectInfo = {
  name: string;
  number: string;
  customer: string;
  consultant: string;
  contractor: string;
};

export const LOAD_KINDS: LoadKind[] = [
  'lighting',
  'socket',
  'air_conditioning',
  'water_heater',
  'kitchen',
  'fan',
  'motor',
  'lift',
  'sub_board',
  'control',
  'data',
  'other',
];

/** The kinds of load a starter can be chosen for. */
export const MOTOR_KINDS: LoadKind[] = ['motor', 'fan', 'lift'];

export const STARTERS: MotorStarter[] = ['dol', 'star_delta', 'drive'];

export function blankLoad(key: number): Load {
  return {
    key,
    description: '',
    load: 'socket',
    power: '',
    phases: '1',
    powerFactor: '',
    controlled: false,
    starter: '',
    length: '',
  };
}

/** A new board with one empty row; `loadKey` must be unused like `key`. */
export function blankBoard(key: number, loadKey: number, name: string, fedFrom = ''): BoardForm {
  return {
    key,
    name,
    location: '',
    voltage: '400',
    phases: '3',
    faultLevel: '',
    fedFrom,
    feederLength: '',
    loads: [blankLoad(loadKey)],
  };
}

/** Rows from an imported or suggested schedule, keyed from `firstKey`. */
export function rowsFrom(loads: LoadScheduleImport['loads'], firstKey: number): Load[] {
  return loads.map((load, index) => ({
    key: firstKey + index,
    description: load.description,
    load: load.load,
    power: load.power_kw,
    phases: load.phases === 3 ? '3' : '1',
    powerFactor: load.power_factor ?? '',
    controlled: load.controlled,
    starter: load.starter ?? '',
    length: load.length_m ?? '',
  }));
}

/** Whether every field the design needs is filled in. */
export function isComplete(info: ProjectInfo, boards: BoardForm[]): boolean {
  return (
    info.name.trim() !== '' &&
    boards.every(
      (board) =>
        board.name.trim() !== '' &&
        board.loads.every((load) => load.description.trim() !== '' && load.power.trim() !== ''),
    )
  );
}

function optional(text: string): string | null {
  const trimmed = text.trim();
  return trimmed === '' ? null : trimmed;
}

/** The API request for what the engineer has entered. */
export function toRequest(
  info: ProjectInfo,
  boards: BoardForm[],
  profile: Record<string, unknown> | null,
): ProjectDesignRequest {
  return {
    info: {
      name: info.name.trim(),
      number: info.number.trim(),
      customer: info.customer.trim(),
      consultant: info.consultant.trim(),
      contractor: info.contractor.trim(),
    },
    boards: boards.map((board) => ({
      name: board.name.trim(),
      location: optional(board.location),
      fed_from: optional(board.fedFrom),
      feeder_length_m: board.fedFrom.trim() === '' ? null : optional(board.feederLength),
      supply: {
        voltage_v: board.voltage.trim(),
        phases: Number(board.phases),
        frequency_hz: '50',
        earthing: 'TN-S',
        fault_level_ka: optional(board.faultLevel),
      },
      loads: board.loads.map((load) => ({
        description: load.description.trim(),
        load: load.load,
        power_kw: load.power.trim(),
        phases: Number(load.phases),
        power_factor: optional(load.powerFactor),
        controlled: load.controlled,
        starter: load.starter === '' ? null : load.starter,
        length_m: optional(load.length),
      })),
    })),
    profile,
  };
}

/** The server's own profile (`DEFAULT_PROFILE_KEY` in app/design/profile.py). */
export const DEFAULT_PROFILE_KEY = 'iec-default';

/** Languages the drawing set can be written in. */
export const DRAWING_LANGUAGES = ['en', 'ar'] as const;
export type DrawingLanguage = (typeof DRAWING_LANGUAGES)[number];

/**
 * The company settings sent with a request, with the drawing language
 * chosen on the page. English is the server's default, so it adds nothing; a
 * language the settings state themselves wins.
 */
export function withLanguage(
  profile: Record<string, unknown> | null,
  language: DrawingLanguage,
): Record<string, unknown> | null {
  if (language === 'en' || (profile && 'language' in profile)) return profile;
  // Settings name the profile they adjust; with none typed, the default one.
  return { ...(profile ?? { key: DEFAULT_PROFILE_KEY }), language };
}
