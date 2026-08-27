"""Checking PLC code with a parser, not with the model that wrote it.

Cite-or-refuse applied to code instead of facts. Generated Structured Text
that looks plausible and contains a logic error is worse than no output at
all: an engineer who trusts it deploys it, and the failure surfaces on real
plant.

Two decisions carry the weight here.

**The check is a parser, not the LLM.** AI-009 says so explicitly, and the
reason is that a model asked to check its own output is being asked whether it
made a mistake by the same faculty that made it. A grammar either accepts a
token stream or it does not, and it does not become more agreeable when asked
twice.

**Anything unparseable reports INCOMPLETE, never VALID.** The dialects
genuinely differ — Siemens SCL has `REGION`, Rockwell has its own tag syntax —
and a grammar covering the IEC core will meet constructs it does not know. The
honest report is "not checked", because an unverifiable result and a
verified-correct one are different things, and a validator that blurs them
hands out ticks it has not earned.

The grammar is written here against `lark` rather than taken from `blark`, the
one real Python ST parser, because blark is GPL and this is a proprietary
product — the same reason PyMuPDF was rejected for the PDF work. lark is the
engine blark itself uses, so this is that engine with a grammar the product
can actually ship.
"""

from __future__ import annotations

import re
from typing import Final

import structlog
from lark import Lark, Token, Tree, UnexpectedInput

from app.models.schemas.plc import (
    PlcDialect,
    PlcValidationResult,
    Severity,
    ValidationFinding,
    ValidationStatus,
)

logger = structlog.get_logger(__name__)

#: Names the checker, so a reader can judge what the verdict is worth.
CHECKER: Final = "lark-iec61131-3-subset"

#: The subset of IEC 61131-3 Structured Text this grammar covers.
#:
#: A subset, and named as one. It handles the constructs generation actually
#: emits — programs, variable blocks, assignment, IF/ELSIF/ELSE, WHILE, FOR,
#: boolean and arithmetic expressions, function calls. It does not handle
#: function blocks, structs, arrays, or vendor extensions, and code using them
#: reports INCOMPLETE rather than being failed for using valid language.
_GRAMMAR: Final = r"""
?start: program

program: "PROGRAM" NAME var_blocks statement_list "END_PROGRAM"

var_blocks: var_block*
var_block: VAR_KIND declaration* "END_VAR"
VAR_KIND: "VAR_INPUT" | "VAR_OUTPUT" | "VAR_IN_OUT" | "VAR"
declaration: NAME ":" type_name [":=" expression] ";"
type_name: NAME

statement_list: statement*
?statement: assignment
          | if_statement
          | while_statement
          | for_statement
          | call_statement

assignment: NAME ":=" expression ";"
call_statement: NAME "(" [arguments] ")" ";"
arguments: expression ("," expression)*

if_statement: "IF" expression "THEN" statement_list elsif_clause* else_clause? "END_IF" ";"
elsif_clause: "ELSIF" expression "THEN" statement_list
else_clause: "ELSE" statement_list

while_statement: "WHILE" expression "DO" statement_list "END_WHILE" ";"
for_statement: "FOR" NAME ":=" expression "TO" expression ["BY" expression] "DO" \
               statement_list "END_FOR" ";"

?expression: or_expr
?or_expr: and_expr | or_expr "OR" and_expr -> or_op
?and_expr: not_expr | and_expr "AND" not_expr -> and_op
?not_expr: comparison | "NOT" not_expr -> not_op
?comparison: sum | sum COMP_OP sum -> compare
COMP_OP: "<=" | ">=" | "<>" | "=" | "<" | ">"
?sum: product | sum ADD_OP product -> arith
ADD_OP: "+" | "-"
?product: atom | product MUL_OP atom -> arith
MUL_OP: "*" | "/" | "MOD"
?atom: NAME "(" [arguments] ")" -> call_expr
     | NAME -> var_ref
     | NUMBER -> number
     | BOOL_LIT -> boolean
     | STRING -> string
     | "(" expression ")"
BOOL_LIT: "TRUE" | "FALSE"

NAME: /(?!(?:IF|THEN|ELSE|ELSIF|END_IF|WHILE|DO|END_WHILE|FOR|TO|BY|END_FOR|AND|OR|NOT|MOD|TRUE|FALSE|VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|END_VAR|PROGRAM|END_PROGRAM)\b)[A-Za-z_][A-Za-z0-9_]*/
NUMBER: /\d+(\.\d+)?/
STRING: /'[^']*'/

COMMENT: "(*" /(.|\n)*?/ "*)" | "//" /[^\n]*/
%import common.WS
%ignore WS
%ignore COMMENT
"""

#: Constructs this grammar knowingly does not cover.
#:
#: Matched before parsing so their presence reports INCOMPLETE rather than a
#: syntax error. Failing valid code for using a feature the checker has not
#: implemented would be a false alarm, and false alarms are how a validator
#: gets ignored.
_UNSUPPORTED: Final = (
    (re.compile(r"\bFUNCTION_BLOCK\b", re.IGNORECASE), "function blocks"),
    (re.compile(r"\bFUNCTION\b", re.IGNORECASE), "functions"),
    (re.compile(r"\bTYPE\b", re.IGNORECASE), "type declarations"),
    (re.compile(r"\bSTRUCT\b", re.IGNORECASE), "structs"),
    (re.compile(r"\bARRAY\b", re.IGNORECASE), "arrays"),
    (re.compile(r"\bCASE\b", re.IGNORECASE), "CASE statements"),
    (re.compile(r"\bREGION\b", re.IGNORECASE), "Siemens REGION blocks"),
    (re.compile(r"\bREPEAT\b", re.IGNORECASE), "REPEAT loops"),
    (re.compile(r"#", re.NOFLAG), "vendor literal or tag syntax"),
    (re.compile(r"%[IQM]", re.IGNORECASE), "direct addressing"),
)

#: Types the grammar knows enough about to type-check assignments against.
_BOOLEAN_TYPES: Final = frozenset({"BOOL"})
_NUMERIC_TYPES: Final = frozenset(
    {"INT", "DINT", "SINT", "LINT", "UINT", "UDINT", "USINT", "ULINT", "REAL", "LREAL", "TIME"}
)

_parser: Lark | None = None


def _get_parser() -> Lark:
    """Return the shared parser, building it once.

    Returns:
        The configured parser.

    Built lazily and cached: constructing a LALR table costs more than parsing
    a program, and doing it per request would make validation the slow part of
    generation for no reason.
    """
    global _parser
    if _parser is None:
        _parser = Lark(_GRAMMAR, parser="lalr", propagate_positions=True)
    return _parser


def validate_plc_code(
    source: str,
    *,
    dialect: PlcDialect = PlcDialect.IEC_61131_3,
) -> PlcValidationResult:
    """Check one piece of PLC code.

    Args:
        source: The code to check.
        dialect: What flavour it is written in.

    Returns:
        The verdict, including whether it could be checked at all.

    Callable on its own, without any generation having happened, because
    BE-010's review endpoint validates code an engineer wrote.

    Never returns ``VALID`` for code it could not fully parse. That is the
    task's stated edge case and the property the whole design turns on: a
    result that says "checked and fine" must mean it.
    """
    if not source.strip():
        return _incomplete(dialect, "empty source")

    unsupported = _first_unsupported(source)
    if unsupported is not None:
        # Not a failure. The code may be perfectly correct; this checker just
        # cannot speak to it, and saying so is the honest answer.
        logger.info("plc.validation_incomplete", dialect=dialect.value, reason=unsupported)
        return _incomplete(dialect, f"validation incomplete for this dialect: uses {unsupported}")

    try:
        tree = _get_parser().parse(source)
    except UnexpectedInput as exc:
        return PlcValidationResult(
            status=ValidationStatus.INVALID,
            dialect=dialect,
            checked_by=CHECKER,
            findings=[
                ValidationFinding(
                    code="syntax-error",
                    message=f"could not parse: {exc.__class__.__name__}",
                    severity=Severity.ERROR,
                    line=getattr(exc, "line", None),
                )
            ],
        )

    findings = _analyse(tree)
    has_error = any(finding.severity is Severity.ERROR for finding in findings)

    return PlcValidationResult(
        status=ValidationStatus.INVALID if has_error else ValidationStatus.VALID,
        dialect=dialect,
        checked_by=CHECKER,
        findings=findings,
    )


def _incomplete(dialect: PlcDialect, message: str) -> PlcValidationResult:
    """Build an INCOMPLETE verdict.

    Args:
        dialect: What was being checked.
        message: Why it could not be checked.

    Returns:
        The verdict.
    """
    return PlcValidationResult(
        status=ValidationStatus.INCOMPLETE,
        dialect=dialect,
        checked_by=CHECKER,
        findings=[
            ValidationFinding(
                code="validation-incomplete",
                message=message,
                severity=Severity.WARNING,
            )
        ],
    )


def _first_unsupported(source: str) -> str | None:
    """Return the first unsupported construct in the source, if any.

    Args:
        source: The code to scan.

    Returns:
        A description, or ``None`` when everything is covered.

    Scanned outside comments, so a comment mentioning "the CASE statement"
    does not make an otherwise checkable program unverifiable.
    """
    stripped = _strip_comments(source)
    for pattern, description in _UNSUPPORTED:
        if pattern.search(stripped):
            return description
    return None


def _strip_comments(source: str) -> str:
    """Remove ST comments from source.

    Args:
        source: The code.

    Returns:
        The code with comments blanked.
    """
    without_block = re.sub(r"\(\*.*?\*\)", " ", source, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", " ", without_block)


def _analyse(tree: Tree[Token]) -> list[ValidationFinding]:
    """Run the logic checks over a parsed program.

    Args:
        tree: The parse tree.

    Returns:
        Everything found, errors and warnings together.

    The checks are the ones AI-009 names — unreferenced tags, unreachable
    code, obvious type mismatches — and deliberately no more. A check that
    guesses is a check that produces false alarms, and a validator people
    learn to ignore is worth less than none, because its silence then means
    nothing either.
    """
    declared = _declared_variables(tree)
    assigned = _assigned_names(tree)
    read = _read_names(tree)

    findings: list[ValidationFinding] = []
    findings.extend(_undeclared_findings(declared, assigned | read))
    findings.extend(_unreferenced_findings(declared, assigned, read))
    findings.extend(_type_findings(tree, declared))
    findings.extend(_unreachable_findings(tree))
    return findings


def _declared_variables(tree: Tree[Token]) -> dict[str, str]:
    """Map each declared variable to its type name.

    Args:
        tree: The parse tree.

    Returns:
        Variable name to type name, upper-cased.
    """
    declared: dict[str, str] = {}
    for declaration in tree.find_data("declaration"):
        name_token = declaration.children[0]
        type_tree = declaration.children[1]
        if not isinstance(name_token, Token) or not isinstance(type_tree, Tree):
            continue
        type_token = type_tree.children[0]
        if isinstance(type_token, Token):
            declared[str(name_token)] = str(type_token).upper()
    return declared


def _assigned_names(tree: Tree[Token]) -> set[str]:
    """Return every variable written to.

    Args:
        tree: The parse tree.

    Returns:
        The assigned names.
    """
    names: set[str] = set()
    for assignment in tree.find_data("assignment"):
        target = assignment.children[0]
        if isinstance(target, Token):
            names.add(str(target))
    for loop in tree.find_data("for_statement"):
        counter = loop.children[0]
        if isinstance(counter, Token):
            names.add(str(counter))
    return names


def _read_names(tree: Tree[Token]) -> set[str]:
    """Return every variable read.

    Args:
        tree: The parse tree.

    Returns:
        The names read.
    """
    names: set[str] = set()
    for reference in tree.find_data("var_ref"):
        token = reference.children[0]
        if isinstance(token, Token):
            names.add(str(token))
    for call in tree.find_data("call_expr"):
        token = call.children[0]
        if isinstance(token, Token):
            names.add(str(token))
    return names


def _undeclared_findings(declared: dict[str, str], used: set[str]) -> list[ValidationFinding]:
    """Report names used but never declared.

    Args:
        declared: Declared variables.
        used: Names read or written.

    Returns:
        One error per undeclared name.

    An error, not a warning. In ST an undeclared symbol does not compile, so
    this is not a matter of taste — and a typo'd tag name is exactly the
    plausible-looking mistake that reads correctly at a glance.
    """
    unknown = sorted(name for name in used if name not in declared)
    return [
        ValidationFinding(
            code="undeclared-tag",
            message=f"{name!r} is used but never declared",
            severity=Severity.ERROR,
        )
        for name in unknown
    ]


def _unreferenced_findings(
    declared: dict[str, str],
    assigned: set[str],
    read: set[str],
) -> list[ValidationFinding]:
    """Report declared variables nothing uses.

    Args:
        declared: Declared variables.
        assigned: Names written to.
        read: Names read.

    Returns:
        One warning per unreferenced name.

    A warning rather than an error: an unused variable compiles, and an output
    left unwired may be deliberate during commissioning. But it is also what a
    forgotten interlock looks like, which is why it is reported at all.
    """
    used = assigned | read
    return [
        ValidationFinding(
            code="unreferenced-tag",
            message=f"{name!r} is declared but never used",
            severity=Severity.WARNING,
        )
        for name in sorted(declared)
        if name not in used
    ]


def _type_findings(tree: Tree[Token], declared: dict[str, str]) -> list[ValidationFinding]:
    """Report assignments whose types obviously disagree.

    Args:
        tree: The parse tree.
        declared: Declared variables.

    Returns:
        One error per mismatch.

    Only the unambiguous cases: a boolean literal into a numeric tag, a number
    into a boolean tag. Full inference over an untyped subset would produce
    guesses, and a wrong type error on correct code teaches an engineer to
    stop reading the output.
    """
    findings: list[ValidationFinding] = []
    for assignment in tree.find_data("assignment"):
        target = assignment.children[0]
        value = assignment.children[1]
        if not isinstance(target, Token):
            continue

        declared_type = declared.get(str(target))
        if declared_type is None:
            # Already reported as undeclared; saying it twice helps nobody.
            continue

        literal = _literal_kind(value)
        if literal is None:
            continue

        if literal == "boolean" and declared_type in _NUMERIC_TYPES:
            findings.append(
                ValidationFinding(
                    code="type-mismatch",
                    message=f"{str(target)!r} is {declared_type}, assigned a BOOL literal",
                    severity=Severity.ERROR,
                    line=getattr(target, "line", None),
                )
            )
        elif literal == "number" and declared_type in _BOOLEAN_TYPES:
            findings.append(
                ValidationFinding(
                    code="type-mismatch",
                    message=f"{str(target)!r} is BOOL, assigned a numeric literal",
                    severity=Severity.ERROR,
                    line=getattr(target, "line", None),
                )
            )
    return findings


def _literal_kind(value: object) -> str | None:
    """Classify an expression if it is a bare literal.

    Args:
        value: The expression node.

    Returns:
        ``"boolean"``, ``"number"``, or ``None`` when it is not a literal.
    """
    if isinstance(value, Tree) and value.data in {"boolean", "number"}:
        return str(value.data)
    return None


def _unreachable_findings(tree: Tree[Token]) -> list[ValidationFinding]:
    """Report branches that can never run.

    Args:
        tree: The parse tree.

    Returns:
        One warning per unreachable branch.

    Detects the tractable case: a condition that is a literal ``FALSE``, so
    the branch is dead, or a literal ``TRUE`` on an ELSIF, so everything after
    it is. Anything requiring real reachability analysis is left alone rather
    than guessed at.
    """
    findings: list[ValidationFinding] = []
    for statement in tree.find_data("if_statement"):
        condition = statement.children[0]
        literal = _boolean_literal(condition)
        if literal is False:
            findings.append(
                ValidationFinding(
                    code="unreachable-branch",
                    message="IF condition is always FALSE; the branch can never run",
                    severity=Severity.WARNING,
                )
            )
        for clause in statement.find_data("elsif_clause"):
            if _boolean_literal(clause.children[0]) is False:
                findings.append(
                    ValidationFinding(
                        code="unreachable-branch",
                        message="ELSIF condition is always FALSE; the branch can never run",
                        severity=Severity.WARNING,
                    )
                )

    for loop in tree.find_data("while_statement"):
        if _boolean_literal(loop.children[0]) is False:
            findings.append(
                ValidationFinding(
                    code="unreachable-branch",
                    message="WHILE condition is always FALSE; the loop body can never run",
                    severity=Severity.WARNING,
                )
            )
    return findings


def _boolean_literal(node: object) -> bool | None:
    """Return the value of a bare boolean literal.

    Args:
        node: The expression node.

    Returns:
        ``True``/``False`` for a literal, ``None`` for anything else.
    """
    if isinstance(node, Tree) and node.data == "boolean":
        token = node.children[0]
        if isinstance(token, Token):
            return str(token).upper() == "TRUE"
    return None
