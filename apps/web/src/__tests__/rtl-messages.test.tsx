import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useNoteText, useOutcomeText } from '@/components/design/note-text';
import type { DesignNote, Refusal } from '@/lib/design';
import ar from '@/messages/ar.json';
import en from '@/messages/en.json';
import he from '@/messages/he.json';

import { renderApp } from './helpers';

/**
 * Arabic and Hebrew: values that keep their place in a right-to-left
 * sentence, counts in every form the language has, fields named as the form
 * names them, and the wording of revisions, reviews and export marks.
 */

const FSI = '⁨';
const LRI = '⁦';
const PDI = '⁩';
/** A value isolated from the right-to-left sentence around it. */
const iso = (value: string) => `${FSI}${value}${PDI}`;

function Notes({ notes }: { notes: DesignNote[] }) {
  const noteText = useNoteText();
  return (
    <ul>
      {notes.map((note, index) => (
        <li key={index} data-testid={`note-${String(index)}`}>
          {noteText(note)}
        </li>
      ))}
    </ul>
  );
}

function Said({ outcome }: { outcome: Refusal | { kind: 'failed' } }) {
  const say = useOutcomeText();
  return <p data-testid="said">{say(outcome)}</p>;
}

const said = () => screen.getByTestId('said').textContent;

/** Every `{count, plural, …}` message in a bundle. */
function countMessages(bundle: unknown): string[] {
  const found: string[] = [];
  const walk = (value: unknown) => {
    if (typeof value === 'string') {
      if (value.includes('{count, plural,')) found.push(value);
    } else if (typeof value === 'object' && value !== null) {
      Object.values(value).forEach(walk);
    }
  };
  walk(bundle);
  return found;
}

describe('values inside right-to-left sentences', () => {
  const formula: DesignNote = {
    code: 'split_points',
    params: { load: 'Sockets 1', points: '7', watts: '150', power: '1.05', source: 'given' },
    text: '',
  };

  it('isolates each value in Hebrew, leaving a select argument to the message', () => {
    renderApp(<Notes notes={[formula]} />, { locale: 'he' });
    expect(screen.getByTestId('note-0').textContent).toBe(
      `${iso('Sockets 1')}: ${iso('7')} × ${iso('150')} W = ${iso('1.05')} kW. מהתיאור`,
    );
  });

  it('adds nothing in English, which reads the same way as its values', () => {
    renderApp(<Notes notes={[formula]} />, { locale: 'en' });
    expect(screen.getByTestId('note-0').textContent).toBe(
      'Sockets 1: 7 × 150 W = 1.05 kW. as described',
    );
  });
});

describe('counts in Arabic and Hebrew', () => {
  const unknown = (fields: string[]): Refusal => ({
    kind: 'refused',
    detail: '',
    unknownFields: fields,
  });

  it('says two unknown fields in the Arabic dual', () => {
    renderApp(<Said outcome={unknown(['a', 'b'])} />, { locale: 'ar' });
    expect(said()).toContain('الحقلين');
    expect(said()).toContain('احذفهما');
  });

  it('says two unknown fields in Hebrew through its own branch', () => {
    renderApp(<Said outcome={unknown(['a', 'b'])} />, { locale: 'he' });
    expect(said()).toContain('השדות');
  });

  it('gives every Arabic count its one, two, few, many and other forms', () => {
    const messages = countMessages(ar);
    expect(messages.length).toBeGreaterThan(10);
    for (const message of messages) {
      for (const form of ['one', 'two', 'few', 'many', 'other']) {
        expect(message, message).toContain(` ${form} {`);
      }
    }
  });

  it('gives every Hebrew count its two form', () => {
    const messages = countMessages(he);
    expect(messages.length).toBeGreaterThan(10);
    for (const message of messages) expect(message, message).toContain(' two {');
  });
});

describe('fields the server refused, named as the form names them', () => {
  const tooLong = (path: string): Refusal => ({
    kind: 'refused',
    detail: 'String should have at most 200 characters',
    code: 'request_invalid',
    tooLong: [{ path, max: 200 }],
  });

  it('names a project field by its label', () => {
    renderApp(<Said outcome={tooLong('request.info.customer')} />, { locale: 'en' });
    expect(said()).toBe('Customer is too long: at most 200 characters.');
  });

  it('names a load row field by its board, its row and its label', () => {
    renderApp(<Said outcome={tooLong('request.boards[0].loads[1].description')} />, {
      locale: 'en',
    });
    expect(said()).toBe('Board 1 › Load 2 › Circuit is too long: at most 200 characters.');
  });

  it('names a supply field on its board, and a number too large by its label', () => {
    renderApp(
      <Said
        outcome={{
          kind: 'refused',
          detail: '',
          code: 'request_invalid',
          tooLarge: [{ path: 'boards[1].supply.fault_level_ka', max: '100' }],
        }}
      />,
      { locale: 'en' },
    );
    expect(said()).toBe('Board 2 › Fault level is too large: at most 100.');
  });

  it('keeps a path it has no label for readable', () => {
    renderApp(<Said outcome={tooLong('request.boards[0].standby.label')} />, { locale: 'en' });
    expect(said()).toBe('Board 1 › standby.label is too long: at most 200 characters.');
  });

  it('names the field in Arabic by its Arabic label', () => {
    renderApp(<Said outcome={tooLong('request.boards[0].loads[0].description')} />, {
      locale: 'ar',
    });
    expect(said()).toContain(ar.design.field.description);
    expect(said()).not.toContain('request.');
    expect(said()).not.toContain('description');
  });
});

describe('a request the server did not accept', () => {
  const invalid: Refusal = {
    kind: 'refused',
    detail: 'Input should be a valid number',
    code: 'request_invalid',
    invalidFields: ['request.boards[0].loads[0].power_kw'],
  };

  it('keeps the English reason out of an Arabic sentence, naming the field instead', () => {
    renderApp(<Said outcome={invalid} />, { locale: 'ar' });
    expect(said()).toContain(ar.design.errors.request_invalid);
    expect(said()).toContain(ar.design.field.power);
    expect(said()).not.toContain('Input should be');
  });

  it('and out of a Hebrew one', () => {
    renderApp(<Said outcome={invalid} />, { locale: 'he' });
    expect(said()).toContain(he.design.errors.request_invalid);
    expect(said()).toContain(he.design.field.power);
    expect(said()).not.toContain('Input should be');
  });

  it('still adds it to the English sentence', () => {
    renderApp(<Said outcome={invalid} />, { locale: 'en' });
    expect(said()).toBe(`${en.design.errors.request_invalid} Input should be a valid number`);
  });
});

describe('the wording of revisions, reviews and marks', () => {
  it('says revision as النسخة in Arabic, keeping المراجعة for review', () => {
    expect(ar.design.projects.approve).toBe('اعتماد النسخة {revision}');
    expect(ar.workspace.context.revision).toBe('النسخة {revision}');
    expect(ar.design.projects.approvedBy).toContain('النسخة {revision} معتمدة');
    expect(ar.design.projects.revisions).toContain('two {نسختان}');
    expect(ar.workspace.views.review).toBe('المراجعة والتصدير');
    expect(ar.design.exports.gapApproval).toBe('اعتماد هذه النسخة للمراجعة');
    expect(JSON.stringify(ar)).not.toMatch(/المراجعة \{revision\}/);
  });

  it('says a blocked step as محجوب', () => {
    expect(ar.design.readiness.state.blocked).toBe('محجوب');
  });

  it('says how a revision export is marked in a whole Arabic sentence', () => {
    expect(ar.design.exports.revisionBody).toContain(
      `إن لم تكن معتمدة تُوسم بـ ${LRI}DRAFT — NOT FOR EXECUTION${PDI}، وإن اعتُمدت للمراجعة تُوسم بـ ${LRI}REVIEW APPROVED — STUDY, NOT FOR EXECUTION${PDI}`,
    );
  });

  it('isolates every English mark inside Arabic and Hebrew left to right', () => {
    for (const bundle of [ar, he]) {
      const text = JSON.stringify(bundle);
      for (const mark of [
        'DRAFT — NOT FOR EXECUTION',
        'REVIEW APPROVED — STUDY, NOT FOR EXECUTION',
      ]) {
        const all = text.split(mark).length - 1;
        expect(all, mark).toBeGreaterThan(0);
        expect(text.split(`${LRI}${mark}${PDI}`).length - 1, mark).toBe(all);
      }
    }
  });

  it('names the enclosure width setting by its label, not its key', () => {
    for (const bundle of [en, ar, he]) {
      expect(bundle.design.note.enclosure_unknown_widths).not.toContain('rail_widths_mm');
      expect(bundle.design.note.enclosure_unknown_widths).toContain(bundle.design.settings.json);
    }
  });
});
