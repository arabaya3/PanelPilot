import type { components } from '@panelpilot/shared-types';

export type BoardDesignRequest = components['schemas']['BoardDesignRequest'];
export type BoardDesignResponse = components['schemas']['BoardDesignResponse'];
/** A project as the API returns it, and as it is sent back to export. */
export type DesignProject = components['schemas']['DesignProject-Output'];
export type ExportFormat = components['schemas']['ExportFormat'];
export type LoadKind = components['schemas']['LoadKind'];

export type DesignOutcome =
  | { kind: 'designed'; response: BoardDesignResponse }
  /** The schedule or the company settings were refused; `detail` says why. */
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

function detailOf(payload: unknown): string {
  const detail = (payload as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item as { msg?: unknown }).msg)
      .filter((msg): msg is string => typeof msg === 'string')
      .join('; ');
  }
  return '';
}

/**
 * Design a distribution board: `POST /api/v1/design/distribution-board`.
 *
 * A 400 or 422 is the design refusing an input (a load no table protects, a
 * malformed company setting) -- an answer to show, not a fault.
 */
export async function designBoard(options: {
  token: string;
  request: BoardDesignRequest;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<DesignOutcome> {
  const {
    token,
    request,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/distribution-board',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify(request),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 400 || response.status === 422) {
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok || typeof payload !== 'object' || payload === null) return { kind: 'failed' };
  if (!('project' in payload) || !('profile' in payload)) return { kind: 'failed' };
  return { kind: 'designed', response: payload as BoardDesignResponse };
}

export type ExportOutcome =
  | { kind: 'exported'; blob: Blob; filename: string }
  | { kind: 'refused'; detail: string }
  | { kind: 'unauthorized' }
  | { kind: 'failed' };

/** Export a project as a file: `POST /api/v1/design/export`. */
export async function exportDesign(options: {
  token: string;
  project: DesignProject;
  format: ExportFormat;
  profile?: Record<string, unknown> | null;
  fetchImpl?: typeof fetch;
  endpoint?: string;
}): Promise<ExportOutcome> {
  const {
    token,
    project,
    format,
    profile = null,
    fetchImpl = fetch,
    endpoint = '/api/v1/design/export',
  } = options;
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ project, format, profile }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  if (response.status === 400 || response.status === 422) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      // A refusal without a body still refuses.
    }
    return { kind: 'refused', detail: detailOf(payload) };
  }
  if (!response.ok) return { kind: 'failed' };
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const match = /filename="([^"]+)"/.exec(disposition);
  try {
    return {
      kind: 'exported',
      blob: await response.blob(),
      filename: match?.[1] ?? `project.${format}`,
    };
  } catch {
    return { kind: 'failed' };
  }
}
