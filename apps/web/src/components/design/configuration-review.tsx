'use client';

import { useTranslations } from 'next-intl';
import type { ReactNode } from 'react';

import { TechnicalToken } from '@/components/technical-token';
import { Notice, StatusBadge, TableFrame, type Tone } from '@/components/ui';
import type { DesignFinding, V2Finding, V2ModelSummary, V2Value } from '@/lib/design';

/**
 * A `pp-design/2` model as a reviewer reads it: the definitions it embeds,
 * its components, strips, terminals, nets, connections and wires, and its
 * structural findings grouped by what they hold back.
 *
 * Review content only. Every value says where it came from (entered by the
 * user, data from a definition, an assumption with its statement, or
 * unresolved), in words, never by colour alone. Nothing here states or
 * implies that a template, a rule or a design was approved by an engineer.
 */

/** Where a value came from, as the API names it. */
type Source = 'user' | 'data' | 'assumption' | 'unresolved' | 'configuration';

const SOURCE_TONE: Record<Source, Tone> = {
  user: 'accent',
  configuration: 'accent',
  data: 'neutral',
  assumption: 'warning',
  unresolved: 'info',
};

function sourceOf(source: string): Source {
  return source in SOURCE_TONE ? (source as Source) : 'unresolved';
}

/** A value's source, as a labelled badge. */
export function SourceBadge({ source }: { source: string }) {
  const t = useTranslations('configuration');
  const known = sourceOf(source);
  return (
    <StatusBadge tone={SOURCE_TONE[known]} testId={`source-${known}`}>
      {t(`source.${known}`)}
    </StatusBadge>
  );
}

/** A reviewed value and its source; an assumption shows what it states. */
export function ValueCell({ value }: { value: V2Value }) {
  const t = useTranslations('configuration');
  return (
    <div className="flex flex-col items-start gap-1">
      {value.value !== null && value.value !== '' ? (
        <TechnicalToken>{value.value}</TechnicalToken>
      ) : (
        <span className="text-text-muted">{t('noValue')}</span>
      )}
      <SourceBadge source={value.source} />
      {value.source === 'assumption' && (
        <span className="text-xs text-text-muted" data-testid="assumption-statement">
          {t('assumptionStatement')}{' '}
          {value.assumption_id && <TechnicalToken>{value.assumption_id}</TechnicalToken>}
          {': '}
          <bdi dir="auto">{value.assumption_statement ?? t('noStatement')}</bdi>
        </span>
      )}
    </div>
  );
}

/** A terminal's requiredness: required, not required, or unresolved. */
export function RequirednessCell({ value }: { value: V2Value }) {
  const t = useTranslations('configuration');
  if (value.source === 'unresolved') {
    return (
      <StatusBadge tone="info" testId="required-unresolved">
        {t('required.unresolved')}
      </StatusBadge>
    );
  }
  const key =
    value.value === 'required'
      ? 'required'
      : value.value === 'not required'
        ? 'notRequired'
        : 'notApplicable';
  return (
    <div className="flex flex-col items-start gap-1">
      <span data-testid={`required-${key}`}>{t(`required.${key}`)}</span>
      {value.value !== null && <SourceBadge source={value.source} />}
    </div>
  );
}

const REVIEW_TONE: Record<string, Tone> = {
  unreviewed: 'warning',
  rejected: 'critical',
  approved: 'neutral',
};

/** A definition's review status as embedded in the data, never more than that. */
export function ReviewStatus({ status }: { status: string }) {
  const t = useTranslations('configuration');
  const known = status in REVIEW_TONE ? status : 'unknown';
  return (
    <StatusBadge tone={REVIEW_TONE[known] ?? 'warning'} testId={`review-${known}`}>
      {t(`review.${known}`)}
    </StatusBadge>
  );
}

/** The four groups findings fall in, in the order a reviewer deals with them. */
export const FINDING_GROUPS = [
  'blocksApproval',
  'blocksExecution',
  'unresolved',
  'needsConfirmation',
  'other',
] as const;
export type FindingGroup = (typeof FINDING_GROUPS)[number];

/**
 * The group a structural finding is listed in: the first of its effects, in
 * the order above. A finding that blocks approval also blocks execution
 * (through approval); it is listed once, under the stronger effect.
 */
export function groupOf(finding: V2Finding): FindingGroup {
  if (finding.blocks_approval) return 'blocksApproval';
  if (finding.blocks_execution) return 'blocksExecution';
  if (finding.unresolved) return 'unresolved';
  if (finding.requires_engineering_confirmation) return 'needsConfirmation';
  return 'other';
}

/** The findings of each group, each group in the order given. */
export function groupFindings(findings: readonly V2Finding[]): Record<FindingGroup, V2Finding[]> {
  const groups: Record<FindingGroup, V2Finding[]> = {
    blocksApproval: [],
    blocksExecution: [],
    unresolved: [],
    needsConfirmation: [],
    other: [],
  };
  for (const finding of findings) groups[groupOf(finding)].push(finding);
  return groups;
}

const SEVERITY_TONE: Record<string, Tone> = {
  blocking: 'critical',
  warning: 'warning',
  info: 'info',
};

/** A finding's severity in words; one the page does not know reads as blocking. */
function SeverityBadge({ severity }: { severity: string }) {
  const t = useTranslations('configuration');
  const known = severity in SEVERITY_TONE ? severity : 'blocking';
  return (
    <StatusBadge tone={SEVERITY_TONE[known] ?? 'critical'}>{t(`severity.${known}`)}</StatusBadge>
  );
}

function locationText(location: Record<string, string>): string {
  return Object.entries(location)
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
}

/** Structural findings grouped by effect, each with its code, message and location. */
export function GroupedFindings({ findings }: { findings: readonly V2Finding[] }) {
  const t = useTranslations('configuration');
  const groups = groupFindings(findings);
  return (
    <div className="flex flex-col gap-4" data-testid="structural-findings">
      {findings.length === 0 && <p className="text-sm text-text-muted">{t('findings.none')}</p>}
      {FINDING_GROUPS.filter((group) => groups[group].length > 0).map((group) => (
        <section key={group} data-testid={`findings-${group}`} className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">
            {t(`findings.${group}`, { count: groups[group].length })}
          </h3>
          <ul className="flex flex-col gap-2">
            {groups[group].map((finding, index) => (
              <li
                key={`${finding.code}-${String(index)}`}
                className="rounded-md border border-border-subtle p-3 text-sm"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <TechnicalToken className="font-semibold">{finding.code}</TechnicalToken>
                  <SeverityBadge severity={finding.severity} />
                  {finding.requires_engineering_confirmation && (
                    <StatusBadge tone="info">{t('findings.confirmFlag')}</StatusBadge>
                  )}
                </div>
                {/* The server words findings in English; kept as one LTR run. */}
                <p className="mt-1" dir="ltr" lang="en">
                  {finding.message}
                </p>
                {Object.keys(finding.location).length > 0 && (
                  <p className="mt-1 text-xs text-text-muted">
                    {t('findings.location')}{' '}
                    <TechnicalToken>{locationText(finding.location)}</TechnicalToken>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

/** What validation checks and what it does not: always shown with a review. */
export function ValidationScopeNote() {
  const t = useTranslations('configuration');
  return (
    <Notice tone="info" live={false} title={t('scope.title')} testId="validation-scope">
      <p>{t('scope.proves')}</p>
      <p className="mt-1">{t('scope.doesNot')}</p>
      <p className="mt-1">{t('scope.review')}</p>
    </Notice>
  );
}

function Table({
  label,
  name,
  testId,
  head,
  children,
  empty,
}: {
  label: string;
  /** The region's and table's accessible name: the label, with whose review it is. */
  name: string;
  testId: string;
  head: string[];
  children: ReactNode[];
  empty: string;
}) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">{label}</h3>
      {children.length === 0 ? (
        <p className="text-sm text-text-muted">{empty}</p>
      ) : (
        <TableFrame label={name}>
          <table className="data-table w-full text-sm" aria-label={name} data-testid={testId}>
            <thead>
              <tr>
                {head.map((cell) => (
                  <th key={cell} className="p-2 text-start">
                    {cell}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>{children}</tbody>
          </table>
        </TableFrame>
      )}
    </section>
  );
}

function Token({ value }: { value: string | null | undefined }) {
  return value ? <TechnicalToken>{value}</TechnicalToken> : <span>—</span>;
}

function Tokens({ values }: { values: readonly string[] }) {
  if (values.length === 0) return <span>—</span>;
  return (
    <div className="flex flex-col gap-1">
      {values.map((value) => (
        <TechnicalToken key={value}>{value}</TechnicalToken>
      ))}
    </div>
  );
}

/**
 * Every table of a model, read-only. `context` names whose review it is
 * ("Saved revision 2", "Validation result"), so two reviews on one page never
 * share region or table names.
 */
export function ModelReview({ model, context }: { model: V2ModelSummary; context: string }) {
  const t = useTranslations('configuration');
  const named = (label: string) => t('tableName', { context, label });
  const h = (key: string) => t(`column.${key}`);
  const empty = t('emptyTable');
  return (
    <div className="flex flex-col gap-5" data-testid="model-review">
      <Notice tone="warning" live={false} testId="definitions-await-review">
        {t('awaitReview')}
      </Notice>
      <Table
        label={t('tables.definitions')}
        name={named(t('tables.definitions'))}
        testId="table-definitions"
        empty={empty}
        head={[h('ref'), h('kind'), h('reviewStatus'), h('provenance'), h('restrictedScope')]}
      >
        {model.definitions.map((definition) => (
          <tr key={`${definition.kind}-${definition.ref}`}>
            <td className="p-2">
              <TechnicalToken>{definition.ref}</TechnicalToken>
            </td>
            <td className="p-2">
              {t(definition.kind === 'pattern' ? 'kind.pattern' : 'kind.template')}
            </td>
            <td className="p-2">
              <ReviewStatus status={definition.review_status} />
            </td>
            <td className="p-2">
              {definition.provenance_source ? (
                <bdi dir="auto">{definition.provenance_source}</bdi>
              ) : (
                <StatusBadge tone="info">{t('provenanceMissing')}</StatusBadge>
              )}
            </td>
            <td className="p-2">
              <Tokens values={definition.restricted_scope ?? []} />
            </td>
          </tr>
        ))}
      </Table>
      <Table
        label={t('tables.components')}
        name={named(t('tables.components'))}
        testId="table-components"
        empty={empty}
        head={[h('id'), h('board'), h('designation'), h('template'), h('patterns'), h('source')]}
      >
        {model.components.map((component) => (
          <tr key={component.id}>
            <td className="p-2">
              <TechnicalToken>{component.id}</TechnicalToken>
            </td>
            <td className="p-2">
              <Token value={component.board_id} />
            </td>
            <td className="p-2">
              <Token value={component.designation} />
            </td>
            <td className="p-2">
              <Token value={component.template_ref} />
            </td>
            <td className="p-2">
              <Tokens values={component.pattern_refs} />
            </td>
            <td className="p-2">
              <SourceBadge source={component.source} />
            </td>
          </tr>
        ))}
      </Table>
      <Table
        label={t('tables.strips')}
        name={named(t('tables.strips'))}
        testId="table-strips"
        empty={empty}
        head={[h('strip'), h('board'), h('marking'), h('position'), h('level'), h('kind')]}
      >
        {model.strips.flatMap((strip) =>
          strip.points.map((point) => (
            <tr key={`${strip.id}-${point.id}`}>
              <td className="p-2">
                <Token value={strip.designation ?? strip.id} />
              </td>
              <td className="p-2">
                <Token value={strip.board_id} />
              </td>
              <td className="p-2">
                <Token value={point.marking} />
              </td>
              <td className="p-2">
                <TechnicalToken>{String(point.position)}</TechnicalToken>
              </td>
              <td className="p-2">
                <TechnicalToken>{String(point.level)}</TechnicalToken>
              </td>
              <td className="p-2">
                <Token value={point.kind} />
              </td>
            </tr>
          )),
        )}
      </Table>
      <Table
        label={t('tables.terminals')}
        name={named(t('tables.terminals'))}
        testId="table-terminals"
        empty={empty}
        head={[
          h('board'),
          h('owner'),
          h('marking'),
          h('side'),
          h('required'),
          h('net'),
          h('connectedTo'),
          h('flags'),
        ]}
      >
        {model.terminals.map((row) => (
          <tr key={`${row.owner_id}-${row.terminal_id}-${row.side ?? ''}`}>
            <td className="p-2">
              <Token value={row.board_id} />
            </td>
            <td className="p-2">
              <Token value={row.designation ?? row.owner_id} />
            </td>
            <td className="p-2">
              <Token value={row.marking} />
            </td>
            <td className="p-2">
              <Token value={row.side} />
            </td>
            <td className="p-2">
              <RequirednessCell value={row.required} />
            </td>
            <td className="p-2">
              <Token value={row.signal ?? row.net_id} />
            </td>
            <td className="p-2">
              <Tokens values={row.connected_to} />
            </td>
            <td className="p-2">
              <Tokens values={row.flags} />
            </td>
          </tr>
        ))}
      </Table>
      <Table
        label={t('tables.nets')}
        name={named(t('tables.nets'))}
        testId="table-nets"
        empty={empty}
        head={[h('id'), h('board'), h('signal'), h('origin'), h('members')]}
      >
        {model.nets.map((net) => (
          <tr key={net.id}>
            <td className="p-2">
              <TechnicalToken>{net.id}</TechnicalToken>
            </td>
            <td className="p-2">
              <Token value={net.board_id} />
            </td>
            <td className="p-2">
              <Token value={net.signal} />
            </td>
            <td className="p-2">
              <Token value={net.origin} />
            </td>
            <td className="p-2">
              <Tokens values={net.members} />
            </td>
          </tr>
        ))}
      </Table>
      <Table
        label={t('tables.connections')}
        name={named(t('tables.connections'))}
        testId="table-connections"
        empty={empty}
        head={[h('id'), h('net'), h('start'), h('end'), h('via'), h('cableCore')]}
      >
        {model.connections.map((connection) => (
          <tr key={connection.id}>
            <td className="p-2">
              <TechnicalToken>{connection.id}</TechnicalToken>
            </td>
            <td className="p-2">
              <Token value={connection.net_id} />
            </td>
            <td className="p-2">
              <Token value={connection.start} />
            </td>
            <td className="p-2">
              <Token value={connection.end} />
            </td>
            <td className="p-2">
              <Token value={connection.via} />
            </td>
            <td className="p-2">
              <Token value={connection.cable_core} />
            </td>
          </tr>
        ))}
      </Table>
      <Table
        label={t('tables.wires')}
        name={named(t('tables.wires'))}
        testId="table-wires"
        empty={empty}
        head={[
          h('wire'),
          h('board'),
          h('start'),
          h('end'),
          h('signal'),
          h('number'),
          h('crossSection'),
          h('colour'),
        ]}
      >
        {model.wires.map((wire) => (
          <tr key={wire.wire_id}>
            <td className="p-2">
              <TechnicalToken>{wire.wire_id}</TechnicalToken>
            </td>
            <td className="p-2">
              <Token value={wire.board_id} />
            </td>
            <td className="p-2">
              <Token value={wire.start} />
            </td>
            <td className="p-2">
              <Token value={wire.end} />
            </td>
            <td className="p-2">
              <Token value={wire.signal} />
            </td>
            <td className="p-2">
              <ValueCell value={wire.number} />
            </td>
            <td className="p-2">
              <ValueCell value={wire.cross_section_mm2} />
            </td>
            <td className="p-2">
              <ValueCell value={wire.colour} />
            </td>
          </tr>
        ))}
      </Table>
      <section className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">{t('tables.findings')}</h3>
        <GroupedFindings findings={model.findings} />
      </section>
    </div>
  );
}

/**
 * The design's other findings a save would record (its v1 checks), beside
 * the structural ones the model lists: each with its code and message.
 */
export function GroupedFindingsOfDesign({ findings }: { findings: readonly DesignFinding[] }) {
  const t = useTranslations('configuration');
  const others = findings.filter((finding) => finding.source !== 'integrity');
  if (others.length === 0) return null;
  return (
    <section className="flex flex-col gap-2" data-testid="design-findings">
      <h3 className="text-sm font-semibold">{t('tables.designFindings')}</h3>
      <ul className="flex flex-col gap-2">
        {others.map((finding, index) => (
          <li
            key={`${finding.code}-${String(index)}`}
            className="rounded-md border border-border-subtle p-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <TechnicalToken className="font-semibold">{finding.code}</TechnicalToken>
              <SeverityBadge severity={finding.severity} />
              {finding.board_id && <TechnicalToken>{finding.board_id}</TechnicalToken>}
            </div>
            <p className="mt-1" dir="ltr" lang="en">
              {finding.message}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
