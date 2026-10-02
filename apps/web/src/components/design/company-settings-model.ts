/**
 * The company settings as the form edits them: a JSON object holding only
 * what the company does differently from the default profile. Each field
 * reads and writes one path in it; clearing a field removes the path, so the
 * default applies again, and an emptied mapping is dropped with it.
 */

export type Settings = Record<string, unknown>;

/** The settings in `text`, `{}` for none, or 'invalid' where it is not a JSON object. */
export function parseSettings(text: string): Settings | 'invalid' {
  if (text.trim() === '') return {};
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === 'object' && value !== null && !Array.isArray(value)
      ? (value as Settings)
      : 'invalid';
  } catch {
    return 'invalid';
  }
}

/** The settings as the text the form keeps; empty for none. */
export function settingsText(settings: Settings): string {
  return Object.keys(settings).length === 0 ? '' : JSON.stringify(settings, null, 2);
}

function isObject(value: unknown): value is Settings {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The value at `path` as the field shows it; '' where it is unset. */
export function readSetting(settings: Settings, path: string[]): string {
  let current: unknown = settings;
  for (const part of path) {
    if (!isObject(current)) return '';
    current = current[part];
  }
  return typeof current === 'string' || typeof current === 'number' ? String(current) : '';
}

/**
 * The settings with `path` set to `value`, trimmed; an empty value removes
 * it, and any mapping left empty on the way.
 */
export function writeSetting(settings: Settings, path: string[], value: string): Settings {
  const [head, ...rest] = path;
  if (head === undefined) return settings;
  const others = Object.fromEntries(Object.entries(settings).filter(([name]) => name !== head));
  if (rest.length === 0) {
    const trimmed = value.trim();
    return trimmed === '' ? others : { ...others, [head]: trimmed };
  }
  const current = settings[head];
  const written = writeSetting(isObject(current) ? current : {}, rest, value);
  return Object.keys(written).length === 0 ? others : { ...others, [head]: written };
}
