import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PlcView } from '@/components/plc-view';
import { tokeniseLine, tokeniseProgram } from '@/components/plc-view/tokenise';

/**
 * Tests for the PLC display component.
 *
 * Two acceptance criteria, and the second is the one with teeth:
 *
 * > Generated code displays with correct syntax highlighting for both Ladder
 * > and ST; validation warnings are visually attached to the relevant lines.
 *
 * Plus the stated edge case — a failed verdict must *block* the "looks done"
 * impression rather than relying on inline marks someone could miss.
 *
 * The ladder tests deliberately go past the trivial contact-coil rung, because
 * the task says so and because a renderer that only handles the simple case
 * cannot draw a seal-in — the single most common rung in the trade.
 */

const VALID_ST = `PROGRAM MotorStart
VAR_INPUT
    StartButton : BOOL;
END_VAR
    IF StartButton THEN
        MotorRun := TRUE;
    END_IF;
END_PROGRAM`;

function validation(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    status: 'valid',
    findings: [],
    dialect: 'iec-61131-3',
    checked_by: 'lark-iec61131-3-subset',
    ...overrides,
  } as never;
}

function finding(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    code: 'undeclared-tag',
    message: "'MotorRun' is used but never declared",
    severity: 'error',
    line: 6,
    ...overrides,
  };
}

// --- Structured Text highlighting ---------------------------------------------

describe('Structured Text highlighting', () => {
  it('colours keywords apart from identifiers', () => {
    const { tokens } = tokeniseLine('IF StartButton THEN');

    expect(tokens.find((t) => t.text === 'IF')?.kind).toBe('keyword');
    expect(tokens.find((t) => t.text === 'StartButton')?.kind).toBe('identifier');
    expect(tokens.find((t) => t.text === 'THEN')?.kind).toBe('keyword');
  });

  it('recognises keywords whatever their case', () => {
    // IEC 61131-3 conventionally upper-cases keywords but does not require it.
    // A manual's lower-case example is the same program, and colouring it as
    // an identifier would suggest otherwise.
    const { tokens } = tokeniseLine('if Started then');

    expect(tokens.find((t) => t.text === 'if')?.kind).toBe('keyword');
    expect(tokens.find((t) => t.text === 'then')?.kind).toBe('keyword');
  });

  it('separates types from keywords', () => {
    const { tokens } = tokeniseLine('Counter : INT;');

    expect(tokens.find((t) => t.text === 'INT')?.kind).toBe('type');
  });

  it('marks boolean literals', () => {
    const { tokens } = tokeniseLine('Enabled := TRUE;');

    expect(tokens.find((t) => t.text === 'TRUE')?.kind).toBe('literal');
  });

  it('reads := as one operator, not two', () => {
    // Splitting it would colour the `=` as a comparison, which reads as a
    // different program than the one on screen.
    const { tokens } = tokeniseLine('A := B;');

    expect(tokens.find((t) => t.text === ':=')).toBeDefined();
    expect(tokens.find((t) => t.text === '=')).toBeUndefined();
  });

  it('reads <= as one operator', () => {
    const { tokens } = tokeniseLine('IF A <= B THEN');

    expect(tokens.find((t) => t.text === '<=')).toBeDefined();
  });

  it('treats a line comment as comment to the end', () => {
    const { tokens } = tokeniseLine('A := TRUE; // set IF THEN whatever');
    const comment = tokens.find((t) => t.kind === 'comment');

    expect(comment?.text).toContain('IF THEN');
  });

  it('carries a block comment across lines', () => {
    // The state has to thread through, or the second line of a comment is
    // highlighted as code and reads as a statement nobody wrote.
    const lines = tokeniseProgram('(* first\nsecond *)\nA := TRUE;');

    expect(lines[0]?.[0]?.kind).toBe('comment');
    expect(lines[1]?.[0]?.kind).toBe('comment');
    expect(lines[2]?.find((t) => t.text === ':=')?.kind).toBe('operator');
  });

  it('does not let an unterminated string swallow the rest of the file', () => {
    // The validator will report it. The display should not also lose every
    // following line to one colour.
    const lines = tokeniseProgram("Msg := 'unterminated\nA := TRUE;");

    expect(lines[1]?.find((t) => t.text === ':=')?.kind).toBe('operator');
  });

  it('handles a doubled quote inside a string', () => {
    const { tokens } = tokeniseLine("Msg := 'it''s fine';");
    const string = tokens.find((t) => t.kind === 'string');

    expect(string?.text).toBe("'it''s fine'");
  });

  it('preserves the exact text of every line', () => {
    // The point of a display component: whatever colouring decides, the code
    // shown must be the code given. A tokeniser that dropped a character
    // would show a program that differs from the one validated.
    for (const line of VALID_ST.split('\n')) {
      const { tokens } = tokeniseLine(line);
      expect(tokens.map((t) => t.text).join('')).toBe(line);
    }
  });
});

// --- findings attached to lines -----------------------------------------------

describe('validation findings', () => {
  it('attaches a finding to the line it is about', () => {
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ status: 'invalid', findings: [finding()] })}
      />,
    );

    expect(screen.getByTestId('finding-line-6').textContent).toContain('never declared');
  });

  it('marks the line itself, not only the message below it', () => {
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ status: 'invalid', findings: [finding()] })}
      />,
    );

    expect(screen.getByTestId('code-line-6').getAttribute('data-has-finding')).toBe('true');
    expect(screen.getByTestId('code-line-5').getAttribute('data-has-finding')).toBeNull();
  });

  it('shows several findings on one line', () => {
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({
          status: 'invalid',
          findings: [finding(), finding({ message: 'second problem' })],
        })}
      />,
    );

    expect(screen.getAllByTestId('finding-line-6')).toHaveLength(2);
  });

  it('shows whole-program findings separately rather than pinning them to line one', () => {
    // An unreferenced tag is about the program, not about line 1. Attaching it
    // there would send someone looking at code that is not the problem.
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({
          findings: [finding({ line: null, severity: 'warning', message: 'unused tag' })],
        })}
      />,
    );

    expect(screen.getByTestId('general-finding').textContent).toContain('unused tag');
  });
});

// --- the banner ---------------------------------------------------------------

describe('the verdict banner', () => {
  it('blocks the looks-done impression when validation failed', () => {
    // The stated edge case. Inline marks alone are what a hurried engineer's
    // eye skips, and code on a screen reads as finished work.
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ status: 'invalid', findings: [finding()] })}
      />,
    );

    const banner = screen.getByTestId('verdict-banner');
    expect(banner.getAttribute('data-status')).toBe('invalid');
    expect(banner.getAttribute('role')).toBe('alert');
    expect(banner.textContent).toContain('Validation failed');
  });

  it('treats an unverified result as seriously as a failed one', () => {
    // `incomplete` means nothing checked this. Styling it as a mild note would
    // put unverified code one glance from looking approved.
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ status: 'incomplete', checked_by: 'validation-unavailable' })}
      />,
    );

    const banner = screen.getByTestId('verdict-banner');
    expect(banner.getAttribute('role')).toBe('alert');
    expect(banner.textContent).toContain('not checked');
  });

  it('says in words why unverified is not a pass', () => {
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ status: 'incomplete' })}
      />,
    );

    expect(screen.getByTestId('verdict-banner').textContent).toContain(
      'unverified result is not a verified-correct one',
    );
  });

  it('shows no banner at all when the code passed cleanly', () => {
    // A banner on every result is a banner nobody reads, which is how the
    // failing one stops working.
    render(<PlcView language="structured-text" source={VALID_ST} validation={validation()} />);

    expect(screen.queryByTestId('verdict-banner')).toBeNull();
  });

  it('shows a non-alerting banner when only warnings were found', () => {
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({ findings: [finding({ severity: 'warning', line: null })] })}
      />,
    );

    const banner = screen.getByTestId('verdict-banner');
    expect(banner.getAttribute('role')).not.toBe('alert');
    expect(banner.textContent).toContain('1 warning');
  });

  it('names what did the checking', () => {
    // "Checked" means little without knowing whether a parser or a language
    // model did it, and this whole feature exists because those differ.
    render(
      <PlcView
        language="structured-text"
        source={VALID_ST}
        validation={validation({
          findings: [finding({ severity: 'warning', line: null })],
        })}
      />,
    );

    expect(screen.getByTestId('verdict-banner').textContent).toContain('lark-iec61131-3-subset');
  });
});

// --- ladder -------------------------------------------------------------------

const CONTACT_COIL = [
  {
    comment: 'Simple contact to coil',
    elements: [{ tag: 'StartButton', kind: 'no' }],
    output: { tag: 'MotorRun', kind: 'coil' },
  },
];

const SEAL_IN = [
  {
    comment: 'Seal in around the start button',
    elements: [
      {
        paths: [[{ tag: 'StartButton', kind: 'no' }], [{ tag: 'MotorRun', kind: 'no' }]],
      },
      { tag: 'StopButton', kind: 'nc' },
    ],
    output: { tag: 'MotorRun', kind: 'coil' },
  },
];

const WITH_TIMER = [
  {
    comment: 'Confirm at speed after a delay',
    elements: [
      { tag: 'MotorRun', kind: 'no' },
      { kind: 'TON', tag: 'StartDelay', parameters: { PT: 'T#5s' } },
    ],
    output: { tag: 'AtSpeed', kind: 'coil' },
  },
];

describe('ladder rendering', () => {
  it('draws SVG rather than an image or text art', () => {
    // "so it stays crisp at any zoom" — a raster diagram is unreadable at the
    // magnification someone inspects a contact at.
    render(<PlcView language="ladder" rungs={CONTACT_COIL as never} validation={validation()} />);

    const diagram = screen.getByTestId('ladder-diagram');
    expect(diagram.tagName.toLowerCase()).toBe('svg');
  });

  it('labels the diagram for a screen reader', () => {
    render(<PlcView language="ladder" rungs={CONTACT_COIL as never} validation={validation()} />);

    expect(screen.getByRole('img', { name: /ladder diagram/i })).not.toBeNull();
  });

  it('draws the simple contact-to-coil rung', () => {
    render(<PlcView language="ladder" rungs={CONTACT_COIL as never} validation={validation()} />);

    const rung = screen.getByTestId('rung-0');
    expect(within(rung).getByText('StartButton')).not.toBeNull();
    expect(within(rung).getByText('MotorRun')).not.toBeNull();
  });

  it('draws a branch as parallel paths, not as a series', () => {
    // The case the task names explicitly. A renderer that flattened a branch
    // would draw a circuit that only runs while the button is held — a
    // different machine than the one the program describes.
    render(<PlcView language="ladder" rungs={SEAL_IN as never} validation={validation()} />);

    const rung = screen.getByTestId('rung-0');
    expect(within(rung).getByText('StartButton')).not.toBeNull();
    expect(within(rung).getAllByText('MotorRun').length).toBeGreaterThanOrEqual(2);
    expect(within(rung).getByText('StopButton')).not.toBeNull();
  });

  it('places the parallel paths at different heights', () => {
    // The geometric property, not a proxy. An earlier version counted lines
    // with x1 === x2, which a mutant dodged by moving one endpoint a single
    // pixel — still drawing a collapsed branch, still passing.
    const { container } = render(
      <PlcView language="ladder" rungs={SEAL_IN as never} validation={validation()} />,
    );

    const rows = [...container.querySelectorAll('text')]
      .filter((node) => node.textContent === 'StartButton' || node.textContent === 'MotorRun')
      .map((node) => Number(node.getAttribute('y')));

    // StartButton and the branch's MotorRun contact sit on different rows.
    // A flattened branch would put every contact on one.
    expect(new Set(rows).size).toBeGreaterThan(1);
  });

  it('joins the parallel paths with connectors that span them', () => {
    // What says "any of these conducts" rather than "all of these in a row".
    //
    // Measured against the paths' own rows rather than by counting verticals:
    // the two power rails are also vertical and span the whole diagram, so a
    // bare count passes even when the branch has collapsed. A connector has to
    // start and end on the rows the branch actually occupies.
    const { container } = render(
      <PlcView language="ladder" rungs={SEAL_IN as never} validation={validation()} />,
    );

    const contactRows = [...container.querySelectorAll('text')]
      .filter((node) => node.textContent === 'StartButton' || node.textContent === 'MotorRun')
      .map((node) => Number(node.getAttribute('y')))
      .sort((a, b) => a - b);

    const top = contactRows[0] ?? 0;
    const bottom = contactRows[contactRows.length - 1] ?? 0;
    expect(bottom - top).toBeGreaterThan(0);

    const connectors = [...container.querySelectorAll('line')].filter((line) => {
      if (line.getAttribute('x1') !== line.getAttribute('x2')) return false;
      const y1 = Number(line.getAttribute('y1'));
      const y2 = Number(line.getAttribute('y2'));
      const span = Math.abs(y2 - y1);
      // Spans the branch, without spanning the whole diagram like a rail.
      return span >= (bottom - top) * 0.5 && span <= (bottom - top) * 1.5;
    });

    expect(connectors.length).toBe(2);
  });

  it('draws a function block with its parameters', () => {
    // A timer preset means nothing to this component and everything to the
    // engineer reading it, so it is passed through verbatim.
    render(<PlcView language="ladder" rungs={WITH_TIMER as never} validation={validation()} />);

    const rung = screen.getByTestId('rung-0');
    expect(within(rung).getByText('TON')).not.toBeNull();
    expect(within(rung).getByText('PT=T#5s')).not.toBeNull();
    expect(within(rung).getByText('StartDelay')).not.toBeNull();
  });

  it('marks a normally-closed contact differently from a normally-open one', () => {
    // Confusing the two inverts the logic. NC carries a diagonal, which is
    // what every ladder editor draws.
    const { container: nc } = render(
      <PlcView language="ladder" rungs={SEAL_IN as never} validation={validation()} />,
    );
    const { container: no } = render(
      <PlcView language="ladder" rungs={CONTACT_COIL as never} validation={validation()} />,
    );

    const diagonals = (root: HTMLElement) =>
      [...root.querySelectorAll('line')].filter(
        (line) =>
          line.getAttribute('x1') !== line.getAttribute('x2') &&
          line.getAttribute('y1') !== line.getAttribute('y2'),
      ).length;

    expect(diagonals(nc)).toBeGreaterThan(diagonals(no));
  });

  it('draws several rungs', () => {
    render(
      <PlcView
        language="ladder"
        rungs={[...CONTACT_COIL, ...WITH_TIMER] as never}
        validation={validation()}
      />,
    );

    expect(screen.getByTestId('rung-0')).not.toBeNull();
    expect(screen.getByTestId('rung-1')).not.toBeNull();
  });

  it('shows the rung comment', () => {
    render(<PlcView language="ladder" rungs={SEAL_IN as never} validation={validation()} />);

    expect(screen.getByText('Seal in around the start button')).not.toBeNull();
  });

  it('still shows the failure banner over a ladder diagram', () => {
    // The banner is about the verdict, not about the language. A failed ladder
    // must block the looks-done impression exactly as failed text does.
    render(
      <PlcView
        language="ladder"
        rungs={SEAL_IN as never}
        validation={validation({ status: 'invalid', findings: [finding({ line: null })] })}
      />,
    );

    expect(screen.getByTestId('verdict-banner').getAttribute('data-status')).toBe('invalid');
  });
});

// --- empty states -------------------------------------------------------------

describe('empty states', () => {
  it('says so when there is no code', () => {
    render(<PlcView language="structured-text" source="" validation={validation()} />);

    expect(screen.getByText('No code to display.')).not.toBeNull();
  });

  it('says so when there are no rungs', () => {
    render(<PlcView language="ladder" rungs={[]} validation={validation()} />);

    expect(screen.getByText('No rungs to display.')).not.toBeNull();
  });

  it('still shows the verdict when there is nothing to display', () => {
    // An empty result with a failed verdict is exactly when someone needs
    // telling — the absence of code is not the absence of a problem.
    render(
      <PlcView
        language="structured-text"
        source=""
        validation={validation({ status: 'incomplete' })}
      />,
    );

    expect(screen.getByTestId('verdict-banner')).not.toBeNull();
  });
});
