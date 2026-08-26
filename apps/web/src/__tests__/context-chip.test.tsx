import { fireEvent, screen, waitFor } from '@testing-library/react';
import type { components } from '@panelpilot/shared-types';
import { describe, expect, it } from 'vitest';

import { Chat } from '@/components/chat';
import { contextFromResponse, hasContext } from '@/components/chat/context-chip';
import type { StreamEvent, StreamOptions } from '@/lib/diagnosis-stream';

import { renderApp } from './helpers';

/**
 * Tests for the session context indicator.
 *
 * The acceptance criteria are both about what happens *after* an interaction —
 * the chip updates when context changes, and later questions carry it without
 * the engineer repeating themselves — so most of these drive the real controls
 * rather than the functions behind them. The last three defects on this lane
 * all hid in exactly that gap: a unit that worked, reached through a path that
 * did not.
 */

type DiagnosticResponse = components['schemas']['DiagnosticResponse'];
type EquipmentContext = components['schemas']['EquipmentContext'];

const CITATION = {
  document_id: 'doc-1',
  document_title: 'ACS880 Firmware Manual',
  manufacturer: 'ABB',
  page: 214,
  section: '6.3',
};

function response(model: string | null): DiagnosticResponse {
  return {
    session_id: 'session-1',
    answer: { text: 'x', citations: [CITATION] },
    diagnosis: {
      summary: 'The drive tripped on DC bus undervoltage.',
      summary_citation_ids: ['doc-1'],
      severity: 'critical',
      equipment_model: model,
      steps: [
        {
          order: 1,
          instruction: 'Measure the supply voltage.',
          rationale: 'r',
          citation_ids: ['doc-1'],
          severity: 'critical',
        },
      ],
    },
    confidence: {
      overall: 0.9,
      retrieval_score: 0.9,
      passage_agreement: 0.9,
      citation_density: 0.9,
    },
    low_confidence: false,
    refusal_message: null,
  };
}

/** A stream the test releases one event at a time. */
function controllableStream() {
  const queue: ((value: IteratorResult<StreamEvent, undefined>) => void)[] = [];
  const pending: StreamEvent[] = [];
  let done = false;

  async function* generator(): AsyncGenerator<StreamEvent> {
    for (;;) {
      if (pending.length > 0) {
        yield pending.shift() as StreamEvent;
        continue;
      }
      if (done) return;
      const next = await new Promise<IteratorResult<StreamEvent, undefined>>((resolve) => {
        queue.push(resolve);
      });
      if (next.done) return;
      yield next.value;
    }
  }

  return {
    generator,
    emit(event: StreamEvent) {
      const resolve = queue.shift();
      if (resolve) resolve({ done: false, value: event });
      else pending.push(event);
    },
    end() {
      done = true;
      const resolve = queue.shift();
      if (resolve) resolve({ done: true, value: undefined });
    },
  };
}

/** Render the chat, capturing every request the component sends. */
function renderChat(streams: ReturnType<typeof controllableStream>[]) {
  const sent: StreamOptions[] = [];
  let call = 0;
  const streamImpl = (options: StreamOptions) => {
    sent.push(options);
    return (streams[call++] ?? controllableStream()).generator();
  };
  renderApp(<Chat token="t" streamImpl={streamImpl} />);
  return { sent };
}

function ask(text: string) {
  const input = document.getElementById('chat-input') as HTMLInputElement;
  fireEvent.change(input, { target: { value: text } });
  fireEvent.submit(input.closest('form') as HTMLFormElement);
}

// --- the decision, on its own ------------------------------------------------

describe('contextFromResponse', () => {
  it('adopts a model the assistant named when nothing is set', () => {
    expect(contextFromResponse(null, response('ACS880'))).toMatchObject({ model: 'ACS880' });
  });

  it('never overwrites what the engineer entered', () => {
    // They are the one standing in front of the panel. A model inferred by
    // the assistant quietly replacing their entry would then be carried into
    // every later question in the session.
    const theirs: EquipmentContext = { manufacturer: 'Siemens', model: 'G120', fault_codes: [] };
    expect(contextFromResponse(theirs, response('ACS880'))).toBeNull();
  });

  it('leaves the context alone when the assistant named no model', () => {
    expect(contextFromResponse(null, response(null))).toBeNull();
  });

  it('does not guess a manufacturer from a model number', () => {
    // "ACS880 is an ABB drive" is true, and guessing is exactly what the
    // neutral state exists to prevent — a wrong brand on the chip rides along
    // on every subsequent question.
    const adopted = contextFromResponse(null, response('ACS880'));
    expect(adopted?.manufacturer).toBeNull();
  });

  it('treats a context with neither field as no context', () => {
    expect(hasContext({ manufacturer: null, model: null, fault_codes: [] })).toBe(false);
    expect(hasContext(null)).toBe(false);
    expect(hasContext({ manufacturer: null, model: 'G120', fault_codes: [] })).toBe(true);
  });
});

// --- through the actual controls ---------------------------------------------

describe('the chip, driven by hand', () => {
  it('starts neutral rather than guessing', () => {
    renderChat([]);
    const chip = screen.getByTestId('context-chip');
    expect(chip.getAttribute('data-known')).toBe('false');
    expect(chip.textContent).toContain('No equipment set');
  });

  it('updates the moment the first response returns a model', async () => {
    // The acceptance criterion, driven end to end: ask, let the answer
    // arrive, and the chip must change without any further interaction.
    const stream = controllableStream();
    renderChat([stream]);

    ask('Why is it tripping?');
    expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('false');

    stream.emit({ kind: 'result', response: response('ACS880') });
    stream.end();

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('true');
    });
    expect(screen.getByTestId('context-chip').textContent).toContain('ACS880');
  });

  it('carries the context on the next question without it being restated', async () => {
    // The other acceptance criterion, and the reason the feature exists.
    const streams = [controllableStream(), controllableStream()];
    const { sent } = renderChat(streams);

    ask('Why is it tripping?');
    // The first request has no context — nothing is known yet.
    expect(sent[0]?.request.equipment ?? null).toBeNull();

    streams[0]?.emit({ kind: 'result', response: response('ACS880') });
    streams[0]?.end();
    await waitFor(() => {
      expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('true');
    });

    ask('And what should I check next?');
    await waitFor(() => {
      expect(sent).toHaveLength(2);
    });
    expect(sent[1]?.request.equipment).toMatchObject({ model: 'ACS880' });
  });

  it('sends what the engineer typed on the next question', async () => {
    // Editing is the path the spec names explicitly, and it is only
    // meaningful if the edit reaches the wire.
    const streams = [controllableStream()];
    const { sent } = renderChat(streams);

    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Manufacturer'), { target: { value: 'Siemens' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('G120');
    });

    ask('Why is it tripping?');
    await waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    expect(sent[0]?.request.equipment).toMatchObject({
      manufacturer: 'Siemens',
      model: 'G120',
    });
  });

  it('keeps the engineer’s entry when a later answer names another model', async () => {
    // Driven through the UI rather than asserted on the function alone: the
    // component decides *when* to call it, and that is where this could still
    // go wrong.
    const streams = [controllableStream()];
    const { sent } = renderChat(streams);

    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    ask('Why is it tripping?');
    streams[0]?.emit({ kind: 'result', response: response('ACS880') });
    streams[0]?.end();

    await waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    // The assistant said ACS880; the engineer said G120. The engineer wins.
    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('G120');
    });
    expect(screen.getByTestId('context-chip').textContent).not.toContain('ACS880');
  });

  it('returns to neutral when both fields are cleared', async () => {
    // "I do not know" is a legitimate answer, and must not be indistinguishable
    // from an empty string on the wire.
    const streams = [controllableStream()];
    const { sent } = renderChat(streams);

    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('true');
    });

    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('false');
    });

    ask('Why is it tripping?');
    await waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    expect(sent[0]?.request.equipment ?? null).toBeNull();
  });

  it('discards an edit that is cancelled', () => {
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('false');
    expect(screen.queryByTestId('context-editor')).toBeNull();
  });

  it('reopens the editor showing what is currently set', async () => {
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(screen.queryByTestId('context-editor')).toBeNull();
    });

    fireEvent.click(screen.getByTestId('context-chip'));
    const field = screen.getByLabelText('Model');
    expect(field).toBeInstanceOf(HTMLInputElement);
    expect((field as HTMLInputElement).value).toBe('G120');
  });

  it('renders no empty placeholder for a missing manufacturer', () => {
    // The spec asks for optional fields to render conditionally. An empty
    // chip segment reads as a field the engineer failed to fill in.
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    const chip = screen.getByTestId('context-chip');
    expect(chip.querySelectorAll('bdi')).toHaveLength(1);
  });

  it('keeps the equipment LTR in every locale', () => {
    // A model number reordered inside Arabic prose is the wrong model number.
    for (const locale of ['en', 'ar', 'he'] as const) {
      const sent: StreamOptions[] = [];
      const streamImpl = (options: StreamOptions) => {
        sent.push(options);
        return controllableStream().generator();
      };
      const { unmount } = renderApp(<Chat token="t" streamImpl={streamImpl} />, { locale });

      fireEvent.click(screen.getByTestId('context-chip'));
      const model = screen.getAllByRole('textbox')[1] as HTMLInputElement;
      fireEvent.change(model, { target: { value: 'ACS880' } });
      fireEvent.submit(model.closest('form') as HTMLFormElement);

      const token = screen.getByText('ACS880');
      expect(token.tagName.toLowerCase(), locale).toBe('bdi');
      expect(token.getAttribute('dir'), locale).toBe('ltr');
      unmount();
    }
  });
});

// --- editing an existing context ---------------------------------------------

describe('reopening the editor', () => {
  it('prefills both fields, so changing one does not destroy the other', async () => {
    // The feature's central workflow: same brand, different unit. A mutation
    // that dropped the manufacturer prefill passed all 177 tests, because
    // every editor test that touched Manufacturer opened from the neutral
    // state where the correct prefill is '' anyway — so the bug and the fix
    // were indistinguishable. What it did to an engineer was silent: the
    // manufacturer vanished from the chip and from every later request, with
    // no error and nothing to notice at the moment of the edit.
    const streams = [controllableStream()];
    const { sent } = renderChat(streams);

    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Manufacturer'), { target: { value: 'Siemens' } });
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('G120');
    });

    // Reopen and change only the model.
    fireEvent.click(screen.getByTestId('context-chip'));
    const manufacturer = screen.getByLabelText('Manufacturer');
    expect(manufacturer).toBeInstanceOf(HTMLInputElement);
    expect((manufacturer as HTMLInputElement).value).toBe('Siemens');

    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G150' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('G150');
    });
    // The brand survived, on screen and on the wire.
    expect(screen.getByTestId('context-chip').textContent).toContain('Siemens');

    ask('Why is it tripping?');
    await waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    expect(sent[0]?.request.equipment).toMatchObject({
      manufacturer: 'Siemens',
      model: 'G150',
    });
  });
});

// --- keyboard and assistive technology ---------------------------------------

describe('the editor without a mouse', () => {
  it('cancels on Escape', () => {
    // Expected of any inline editor, and this one is deliberately
    // keyboard-first — an engineer in a plant room may have gloves on.
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });

    fireEvent.keyDown(screen.getByTestId('context-editor'), { key: 'Escape' });

    expect(screen.queryByTestId('context-editor')).toBeNull();
    expect(screen.getByTestId('context-chip').getAttribute('data-known')).toBe('false');
  });

  it('puts the caret in the first field when it opens', () => {
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    expect(document.activeElement).toBe(screen.getByLabelText('Manufacturer'));
  });

  it('hands focus back to the chip when it closes', () => {
    // Otherwise the keyboard user is dropped to the top of the document and
    // has to tab back to where they were — which undoes the point of
    // focusing the field on open.
    renderChat([]);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(document.activeElement).toBe(screen.getByTestId('context-chip'));
  });

  it('does not steal focus on first render', () => {
    // The close path fires an effect; without a guard it would run on mount
    // and pull focus to the chip before anyone has touched it.
    renderChat([]);
    expect(document.activeElement).not.toBe(screen.getByTestId('context-chip'));
  });

  it('says whether the editor is open', () => {
    renderChat([]);
    const chip = screen.getByTestId('context-chip');
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    fireEvent.click(chip);
    expect(screen.getByTestId('context-editor').getAttribute('role')).toBe('group');
  });

  it('does not run the hint into the equipment name', async () => {
    // Without a separator the accessible name reads as one run:
    // "Set the equipment for this sessionSiemensG120".
    const streams = [controllableStream()];
    renderChat(streams);
    fireEvent.click(screen.getByTestId('context-chip'));
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'G120' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      expect(screen.getByTestId('context-chip').textContent).toContain('G120');
    });
    expect(screen.getByTestId('context-chip').textContent).not.toContain('sessionG120');
  });
});
