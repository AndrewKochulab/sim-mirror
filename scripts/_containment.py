# SPDX-License-Identifier: Apache-2.0
"""Finding a program's name where it starts a command, and not in prose or as a plain word.

A program is named where it starts a command: as the first item of a list or tuple (an argv being built), as the first
argument of a call (a spawn, or a runner handed its subcommand), or as the first word of a string with spaces in it (a
command line). Those are what an argv is made from, wherever it is later handed to a spawn. The same word standing
anywhere else -- the name of a connector in a list of choices, a `Literal` -- runs nothing, and docstrings are prose.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable
from pathlib import Path, PurePath


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every docstring constant, which is prose, not code."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def command_starts(tree: ast.AST) -> set[int]:
    """The ids of every node in a place that starts a command: a sequence's first item, a call's first argument."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            found.add(id(node.elts[0]))
        elif isinstance(node, ast.Call) and node.args:
            found.add(id(node.args[0]))
    return found


def names_program(node: ast.AST, programs: Iterable[str], *, starts_command: bool) -> str | None:
    """The program a string constant starts a command with, if it is one of these."""
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return None
    words = node.value.split()
    if not words or (len(words) == 1 and not starts_command):
        return None
    first = PurePath(words[0]).name
    return next((program for program in programs if first == program), None)


def allowed(rel: str, allowances: Collection[str]) -> bool:
    """Whether a file may name the programs: it is listed, or it is under a listed folder (one ending in "/")."""
    return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in allowances)


def offenders(
    repo_root: Path, scan_dirs: Iterable[str], allowances: Collection[str], programs: Iterable[str]
) -> list[tuple[str, int, str]]:
    """Every (file, line, program) starting a command with one of `programs` outside the allowed files."""
    programs = tuple(programs)
    found: list[tuple[str, int, str]] = []
    for directory in scan_dirs:
        for path in sorted((repo_root / directory).rglob("*.py")):
            rel = path.relative_to(repo_root).as_posix()
            if allowed(rel, allowances) or "__pycache__" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            prose, starts = docstring_nodes(tree), command_starts(tree)
            for node in ast.walk(tree):
                if id(node) in prose:
                    continue
                program = names_program(node, programs, starts_command=id(node) in starts)
                if program is not None:
                    found.append((rel, getattr(node, "lineno", 0), program))
    return sorted(found)
