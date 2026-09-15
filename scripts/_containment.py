# SPDX-License-Identifier: Apache-2.0
"""Finding a program's name in code, and not in prose.

A string constant whose first word is one of the programs -- a bare name, an absolute path to it, or a whole command
line -- is what an argv is built from, wherever it is later handed to a spawn. Docstrings are prose and are skipped.
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


def names_program(node: ast.AST, programs: Iterable[str]) -> str | None:
    """The program a string constant names as its first word, if it is one of these."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        words = node.value.split()
        for program in programs:
            if words and PurePath(words[0]).name == program:
                return program
    return None


def allowed(rel: str, allowances: Collection[str]) -> bool:
    """Whether a file may name the programs: it is listed, or it is under a listed folder (one ending in "/")."""
    return any(rel == entry or (entry.endswith("/") and rel.startswith(entry)) for entry in allowances)


def offenders(
    repo_root: Path, scan_dirs: Iterable[str], allowances: Collection[str], programs: Iterable[str]
) -> list[tuple[str, int, str]]:
    """Every (file, line, program) naming one of `programs` outside the allowed files."""
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
            prose = docstring_nodes(tree)
            for node in ast.walk(tree):
                if id(node) in prose:
                    continue
                program = names_program(node, programs)
                if program is not None:
                    found.append((rel, getattr(node, "lineno", 0), program))
    return found
