import {
  clearTrial,
  readTrial,
  resumeTrial,
  startTrial,
  storeTrial,
  type ActiveTrial,
} from '@/lib/trial';

/**
 * A token for a page other than the chat: this browser's trial, resumed, or a
 * new one.
 *
 * The same order the home page follows, for the same reasons: resuming keeps
 * the conversation the visitor already has, and a stored trial is forgotten
 * only when the server says it is gone -- never on a network failure, which
 * would strand a conversation that is still reachable. Tokens stay in memory;
 * only the claim pair is stored.
 */
export type TrialAcquired =
  | ({ kind: 'ready' } & ActiveTrial)
  | { kind: 'unavailable' }
  | { kind: 'rate-limited' }
  | { kind: 'failed' };

export async function acquireTrial(): Promise<TrialAcquired> {
  const existing = readTrial();
  if (existing) {
    const resumed = await resumeTrial(existing);
    if (resumed.kind === 'resumed') {
      storeTrial(resumed.trial);
      const { kind: _kind, ...active } = resumed;
      return { kind: 'ready', ...active };
    }
    if (resumed.kind === 'failed') return { kind: 'failed' };
    clearTrial();
  }

  const started = await startTrial();
  if (started.kind !== 'started') return started;
  storeTrial(started.trial);
  const { kind: _kind, ...active } = started;
  return { kind: 'ready', ...active };
}
