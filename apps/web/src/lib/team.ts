import type { components } from '@panelpilot/shared-types';

export type Team = components['schemas']['Team'];
export type InvitationOut = components['schemas']['InvitationOut'];
export type InvitationCreated = components['schemas']['InvitationCreated'];

/** A refusal with a code the page can say in the reader's language. */
export type TeamRefusal = { kind: 'refused'; code: string; params: Record<string, string> };
type Failure = TeamRefusal | { kind: 'unauthorized' } | { kind: 'failed' };

type Options = { token: string; fetchImpl?: typeof fetch };

async function send(
  options: Options,
  path: string,
  method: 'GET' | 'POST' | 'DELETE',
  body?: unknown,
): Promise<{ ok: true; payload: unknown } | Failure> {
  const { token, fetchImpl = fetch } = options;
  let response: Response;
  try {
    response = await fetchImpl(`/api/v1/team${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 401) return { kind: 'unauthorized' };
  let payload: unknown = null;
  try {
    payload = response.status === 204 ? null : await response.json();
  } catch {
    payload = null;
  }
  if ([400, 403, 404, 422].includes(response.status)) return refusal(payload);
  if (!response.ok) return { kind: 'failed' };
  return { ok: true, payload };
}

function refusal(payload: unknown): TeamRefusal {
  const body = (payload ?? {}) as { code?: unknown; params?: unknown };
  return {
    kind: 'refused',
    code: typeof body.code === 'string' ? body.code : '',
    params:
      typeof body.params === 'object' && body.params !== null
        ? (body.params as Record<string, string>)
        : {},
  };
}

/** The caller's team: `GET /api/v1/team`. */
export async function getTeam(options: Options): Promise<{ kind: 'team'; team: Team } | Failure> {
  const result = await send(options, '', 'GET');
  return 'ok' in result ? { kind: 'team', team: result.payload as Team } : result;
}

/** Invite a colleague: the token comes back once, to send. */
export async function invite(
  options: Options & { email: string },
): Promise<{ kind: 'invited'; invitation: InvitationCreated } | Failure> {
  const result = await send(options, '/invitations', 'POST', { email: options.email });
  return 'ok' in result
    ? { kind: 'invited', invitation: result.payload as InvitationCreated }
    : result;
}

/** The invitations still open. */
export async function listInvitations(
  options: Options,
): Promise<{ kind: 'listed'; invitations: InvitationOut[] } | Failure> {
  const result = await send(options, '/invitations', 'GET');
  return 'ok' in result
    ? { kind: 'listed', invitations: result.payload as InvitationOut[] }
    : result;
}

/** Withdraw an invitation. */
export async function revokeInvitation(
  options: Options & { id: string },
): Promise<{ kind: 'done' } | Failure> {
  const result = await send(options, `/invitations/${encodeURIComponent(options.id)}`, 'DELETE');
  return 'ok' in result ? { kind: 'done' } : result;
}

/** Take a member out of the team. */
export async function removeMember(
  options: Options & { id: string },
): Promise<{ kind: 'done' } | Failure> {
  const result = await send(options, `/members/${encodeURIComponent(options.id)}`, 'DELETE');
  return 'ok' in result ? { kind: 'done' } : result;
}

/** The link a colleague opens to join. */
export function joinLink(origin: string, token: string): string {
  return `${origin}/join?invite=${encodeURIComponent(token)}`;
}

export type JoinOutcome =
  { kind: 'joined' } | { kind: 'email-taken' } | TeamRefusal | { kind: 'failed' };

/** Create an account in the inviting team: `POST /api/v1/auth/signup` with the invitation. */
export async function signupWithInvite(options: {
  email: string;
  password: string;
  fullName: string;
  inviteToken: string;
  fetchImpl?: typeof fetch;
}): Promise<JoinOutcome> {
  const { fetchImpl = fetch } = options;
  let response: Response;
  try {
    response = await fetchImpl('/api/v1/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: options.email,
        password: options.password,
        ...(options.fullName.trim() ? { full_name: options.fullName.trim() } : {}),
        invite_token: options.inviteToken,
      }),
    });
  } catch {
    return { kind: 'failed' };
  }
  if (response.status === 409) return { kind: 'email-taken' };
  if (response.ok) return { kind: 'joined' };
  if (response.status === 400 || response.status === 422) {
    let payload: unknown = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    return refusal(payload);
  }
  return { kind: 'failed' };
}
