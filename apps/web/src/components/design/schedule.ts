import type {
  LoadKind,
  MarkupSuggestion,
  LoadScheduleImport,
  MotorStarter,
  ProjectDesignRequest,
  SavedRequest,
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

/** The system earthing a board's supply can have. */
export const EARTHING = ['TN-S', 'TN-C-S', 'TT'] as const;
export type Earthing = (typeof EARTHING)[number];

/** One board's header and schedule as the engineer edits it. */
export type BoardForm = {
  key: number;
  name: string;
  location: string;
  voltage: string;
  phases: '1' | '3';
  faultLevel: string;
  earthing: Earthing;
  /** Ze in ohms; empty leaves earth fault disconnection unchecked. */
  earthLoop: string;
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
    earthing: 'TN-S',
    earthLoop: '',
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
        earthing: board.earthing,
        fault_level_ka: optional(board.faultLevel),
        earth_loop_ohm: optional(board.earthLoop),
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

/**
 * A saved project back as the form: its title block and boards, keyed from
 * `firstKey`. Returns the keys it used, so the caller's counter moves past.
 */
export function fromRequest(
  request: SavedRequest,
  firstKey: number,
): { info: ProjectInfo; boards: BoardForm[]; used: number } {
  let key = firstKey;
  const boards = request.boards.map((board) => {
    const boardKey = key;
    const loads = rowsFrom(board.loads, boardKey + 1);
    key += loads.length + 1;
    return {
      key: boardKey,
      name: board.name,
      location: board.location ?? '',
      voltage: board.supply?.voltage_v ?? '400',
      phases: board.supply?.phases === 1 ? ('1' as const) : ('3' as const),
      faultLevel: board.supply?.fault_level_ka ?? '',
      earthing: EARTHING.find((value) => value === board.supply?.earthing) ?? ('TN-S' as const),
      earthLoop: board.supply?.earth_loop_ohm ?? '',
      fedFrom: board.fed_from ?? '',
      feederLength: board.feeder_length_m ?? '',
      loads,
    };
  });
  const info = request.info;
  return {
    info: {
      name: info.name,
      number: info.number,
      customer: info.customer,
      consultant: info.consultant,
      contractor: info.contractor,
    },
    boards,
    used: key - firstKey,
  };
}

/**
 * Saved company settings back as the settings box and the drawing language:
 * the language goes to its own field, and settings that only named the
 * default profile to carry it leave the box empty.
 */
export function profileFrom(profile: Record<string, unknown> | null | undefined): {
  text: string;
  language: DrawingLanguage | null;
} {
  if (!profile) return { text: '', language: null };
  const { language, ...rest } = profile;
  const chosen = DRAWING_LANGUAGES.find((known) => known === language) ?? null;
  const onlyDefault = Object.keys(rest).length === 1 && rest.key === DEFAULT_PROFILE_KEY;
  return {
    text: Object.keys(rest).length === 0 || onlyDefault ? '' : JSON.stringify(rest, null, 2),
    language: chosen,
  };
}

/**
 * The boards with a reviewer's suggested change applied, or null where the
 * board or circuit it names is no longer in the form. A circuit is found at
 * its place in the schedule when its description still matches there, and
 * by its description otherwise, since rows may have moved since the design.
 */
export function applySuggestion(
  boards: BoardForm[],
  suggestion: MarkupSuggestion,
): BoardForm[] | null {
  const board = boards.find((candidate) => candidate.name.trim() === suggestion.board);
  if (!board) return null;
  const value = suggestion.value ?? '';
  const replace = (changed: BoardForm) =>
    boards.map((candidate) => (candidate.key === board.key ? changed : candidate));
  if (suggestion.field === 'feeder_length_m') return replace({ ...board, feederLength: value });
  const atIndex = suggestion.load_index != null ? board.loads[suggestion.load_index] : undefined;
  const load =
    atIndex && atIndex.description.trim() === suggestion.circuit
      ? atIndex
      : board.loads.find((candidate) => candidate.description.trim() === suggestion.circuit);
  if (!load) return null;
  if (suggestion.field === 'remove') {
    if (board.loads.length === 1) return null;
    return replace({ ...board, loads: board.loads.filter((row) => row.key !== load.key) });
  }
  const patch: Partial<Load> =
    suggestion.field === 'power_kw'
      ? { power: value }
      : suggestion.field === 'length_m'
        ? { length: value }
        : suggestion.field === 'power_factor'
          ? { powerFactor: value }
          : suggestion.field === 'phases'
            ? { phases: value === '1' ? '1' : '3' }
            : { starter: STARTERS.find((starter) => starter === value) ?? '' };
  return replace({
    ...board,
    loads: board.loads.map((row) => (row.key === load.key ? { ...row, ...patch } : row)),
  });
}
