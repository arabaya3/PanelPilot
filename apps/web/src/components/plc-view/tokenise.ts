/**
 * Tokenising IEC 61131-3 Structured Text for display.
 *
 * Hand-written rather than Shiki, which the task offers as an example. Shiki
 * ships a WASM regex engine and a TextMate grammar loader to support a hundred
 * languages; this needs one, whose entire vocabulary is the list below. The
 * cost is not the bundle alone — it is a grammar file to keep in step with the
 * validator's grammar, in a component whose job is to display exactly what
 * that validator checked.
 *
 * Highlighting here is a reading aid, not a claim about correctness. The
 * validator decides what is wrong; nothing in this file may imply otherwise.
 * That is why there is no "error" token kind: a squiggle drawn by a tokeniser
 * that does not parse would be a guess wearing the same colour as a verdict.
 */

/** What a token is, for the purpose of colouring it. */
export type TokenKind =
  | 'keyword'
  | 'type'
  | 'literal'
  | 'number'
  | 'string'
  | 'comment'
  | 'operator'
  | 'identifier'
  | 'plain';

/** One coloured span of a line. */
export interface Token {
  readonly kind: TokenKind;
  readonly text: string;
}

/**
 * Control-flow and declaration keywords.
 *
 * The same set the validator's grammar reserves. Kept in one place so a
 * keyword the parser knows and the display does not — or the reverse — is a
 * visible difference rather than a silent one.
 */
const KEYWORDS = new Set([
  'PROGRAM',
  'END_PROGRAM',
  'FUNCTION',
  'END_FUNCTION',
  'FUNCTION_BLOCK',
  'END_FUNCTION_BLOCK',
  'VAR',
  'VAR_INPUT',
  'VAR_OUTPUT',
  'VAR_IN_OUT',
  'VAR_GLOBAL',
  'VAR_TEMP',
  'END_VAR',
  'IF',
  'THEN',
  'ELSE',
  'ELSIF',
  'END_IF',
  'CASE',
  'OF',
  'END_CASE',
  'FOR',
  'TO',
  'BY',
  'DO',
  'END_FOR',
  'WHILE',
  'END_WHILE',
  'REPEAT',
  'UNTIL',
  'END_REPEAT',
  'RETURN',
  'EXIT',
  'CONTINUE',
  'AND',
  'OR',
  'NOT',
  'XOR',
  'MOD',
  'REGION',
  'END_REGION',
]);

/** Elementary data types. */
const TYPES = new Set([
  'BOOL',
  'BYTE',
  'WORD',
  'DWORD',
  'LWORD',
  'SINT',
  'INT',
  'DINT',
  'LINT',
  'USINT',
  'UINT',
  'UDINT',
  'ULINT',
  'REAL',
  'LREAL',
  'TIME',
  'DATE',
  'STRING',
  'WSTRING',
  'ARRAY',
  'STRUCT',
  'END_STRUCT',
]);

/** Boolean literals. */
const LITERALS = new Set(['TRUE', 'FALSE']);

/**
 * Multi-character operators, longest first.
 *
 * Order matters: matching `<` before `<=` would split the comparison into two
 * tokens and colour the `=` as an assignment, which reads as a different
 * program than the one on screen.
 */
const OPERATORS = [':=', '<=', '>=', '<>', '=>', '**', '+', '-', '*', '/', '<', '>', '='];

const IDENTIFIER_START = /[A-Za-z_]/;
const IDENTIFIER_PART = /[A-Za-z0-9_]/;
const DIGIT = /[0-9]/;

/**
 * Split one line of Structured Text into coloured spans.
 *
 * @param line - The line to tokenise, without its newline.
 * @param inBlockComment - Whether a `(*` opened on an earlier line is still open.
 * @returns The line's tokens, and whether a block comment is still open after it.
 *
 * Line-at-a-time, with the block-comment state threaded through, because the
 * component renders per line: a line needs its own tokens to attach a
 * validation finding to, and a whole-file tokeniser would have to be re-split
 * afterwards along boundaries it had already discarded.
 */
export function tokeniseLine(
  line: string,
  inBlockComment = false,
): { tokens: Token[]; inBlockComment: boolean } {
  const tokens: Token[] = [];
  let index = 0;
  let open = inBlockComment;

  while (index < line.length) {
    if (open) {
      const close = line.indexOf('*)', index);
      if (close === -1) {
        push(tokens, 'comment', line.slice(index));
        return { tokens, inBlockComment: true };
      }
      push(tokens, 'comment', line.slice(index, close + 2));
      index = close + 2;
      open = false;
      continue;
    }

    const rest = line.slice(index);

    // Line comment: everything after it is comment, whatever it contains.
    if (rest.startsWith('//')) {
      push(tokens, 'comment', rest);
      return { tokens, inBlockComment: false };
    }

    if (rest.startsWith('(*')) {
      const close = line.indexOf('*)', index + 2);
      if (close === -1) {
        push(tokens, 'comment', rest);
        return { tokens, inBlockComment: true };
      }
      push(tokens, 'comment', line.slice(index, close + 2));
      index = close + 2;
      continue;
    }

    const character = line[index] as string;

    if (character === "'") {
      const end = findStringEnd(line, index);
      push(tokens, 'string', line.slice(index, end));
      index = end;
      continue;
    }

    if (/\s/.test(character)) {
      const end = advanceWhile(line, index, (c) => /\s/.test(c));
      push(tokens, 'plain', line.slice(index, end));
      index = end;
      continue;
    }

    if (DIGIT.test(character)) {
      const end = advanceWhile(line, index, (c) => /[0-9._#A-Fa-f]/.test(c));
      push(tokens, 'number', line.slice(index, end));
      index = end;
      continue;
    }

    if (IDENTIFIER_START.test(character)) {
      const end = advanceWhile(line, index, (c) => IDENTIFIER_PART.test(c));
      const word = line.slice(index, end);
      push(tokens, classifyWord(word), word);
      index = end;
      continue;
    }

    const operator = OPERATORS.find((candidate) => rest.startsWith(candidate));
    if (operator !== undefined) {
      push(tokens, 'operator', operator);
      index += operator.length;
      continue;
    }

    push(tokens, 'plain', character);
    index += 1;
  }

  return { tokens, inBlockComment: open };
}

/**
 * Classify a bare word.
 *
 * @param word - The word to classify.
 * @returns Its token kind.
 *
 * Case-insensitive: IEC 61131-3 keywords are conventionally upper case but not
 * required to be, and a manual's example written in lower case is still the
 * same program. Colouring it as an identifier would suggest otherwise.
 */
function classifyWord(word: string): TokenKind {
  const upper = word.toUpperCase();
  if (KEYWORDS.has(upper)) return 'keyword';
  if (TYPES.has(upper)) return 'type';
  if (LITERALS.has(upper)) return 'literal';
  return 'identifier';
}

/**
 * Find the end of a single-quoted string, past any doubled quotes.
 *
 * @param line - The line being scanned.
 * @param start - Index of the opening quote.
 * @returns Index just past the closing quote, or the line's end if unterminated.
 *
 * An unterminated string runs to the end of the line rather than swallowing
 * the rest of the file. The validator will report it; the display should not
 * also lose every following line to a colour.
 */
function findStringEnd(line: string, start: number): number {
  let index = start + 1;
  while (index < line.length) {
    if (line[index] === "'") {
      // A doubled quote is an escaped quote, not the end.
      if (line[index + 1] === "'") {
        index += 2;
        continue;
      }
      return index + 1;
    }
    index += 1;
  }
  return line.length;
}

/**
 * Advance while a predicate holds.
 *
 * @param line - The line being scanned.
 * @param start - Where to begin.
 * @param predicate - Test applied to each character.
 * @returns The first index where the predicate fails.
 */
function advanceWhile(line: string, start: number, predicate: (c: string) => boolean): number {
  let index = start;
  while (index < line.length && predicate(line[index] as string)) {
    index += 1;
  }
  return index;
}

/**
 * Append a token, skipping empties.
 *
 * @param tokens - The list being built.
 * @param kind - The token's kind.
 * @param text - Its text.
 */
function push(tokens: Token[], kind: TokenKind, text: string): void {
  if (text.length > 0) {
    tokens.push({ kind, text });
  }
}

/**
 * Tokenise a whole program, line by line.
 *
 * @param source - The program text.
 * @returns One token list per line, in order.
 *
 * Returns lines rather than a flat stream so a validation finding reported
 * against line 7 can be attached to line 7 without counting newlines twice.
 */
export function tokeniseProgram(source: string): Token[][] {
  const lines = source.split('\n');
  const result: Token[][] = [];
  let open = false;

  for (const line of lines) {
    const { tokens, inBlockComment } = tokeniseLine(line, open);
    result.push(tokens);
    open = inBlockComment;
  }

  return result;
}
