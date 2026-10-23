"""Lightweight static checks that need no third-party packages.

Reports: (1) names read but never bound anywhere in the module (missing imports/typos),
(2) `from textile_automation... import X` where X is not defined in that module,
(3) unused imports. It is a safety net, not a replacement for ruff and mypy.
Usage: python scripts/static_check.py
"""

from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}


def module_path(name: str) -> Path | None:
    base = SRC.joinpath(*name.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def top_level_names(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                names.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.For | ast.If | ast.Try | ast.With):
            for n in ast.walk(node):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    names.add(n.id)
    return names


def bound_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store | ast.Del):
            out.add(n.id)
        elif isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            out.add(n.name)
            out.update(tp.name for tp in getattr(n, "type_params", []))  # PEP 695 generics
            if not isinstance(n, ast.ClassDef):
                a = n.args
                for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                    if arg:
                        out.add(arg.arg)
        elif isinstance(n, ast.Lambda):
            a = n.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg:
                    out.add(arg.arg)
        elif isinstance(n, ast.Import | ast.ImportFrom):
            out.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, ast.Global | ast.Nonlocal):
            out.update(n.names)
        elif isinstance(n, ast.MatchAs | ast.MatchStar) and getattr(n, "name", None):
            out.add(n.name)
    return out


def check_file(path: Path) -> list[str]:
    problems: list[str] = []
    text = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(text)
    rel = path.relative_to(ROOT)
    bound = bound_names(tree) | BUILTINS
    string_annotations = text  # names used only in string annotations count as used
    used: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            used.add(n.id)
            if n.id not in bound:
                problems.append(f"{rel}:{n.lineno}: undefined name '{n.id}'")
        elif isinstance(n, ast.Attribute):
            pass
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("textile_automation"):
            target = module_path(n.module)
            if target is None:
                problems.append(f"{rel}:{n.lineno}: module '{n.module}' not found")
                continue
            defined = top_level_names(target)
            for a in n.names:
                if (
                    a.name != "*"
                    and a.name not in defined
                    and module_path(f"{n.module}.{a.name}") is None
                ):
                    problems.append(f"{rel}:{n.lineno}: '{a.name}' is not defined in {n.module}")
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.startswith("textile_automation") and module_path(a.name) is None:
                    problems.append(f"{rel}:{n.lineno}: module '{a.name}' not found")
    if path.name != "__init__.py":
        for n in ast.walk(tree):
            if isinstance(n, ast.Import | ast.ImportFrom):
                if isinstance(n, ast.ImportFrom) and n.module == "__future__":
                    continue
                for a in n.names:
                    name = (a.asname or a.name).split(".")[0]
                    if name not in used and string_annotations.count(name) < 2:
                        problems.append(f"{rel}:{n.lineno}: unused import '{name}'")
    return problems


def main() -> int:
    files = [
        *SRC.rglob("*.py"),
        ROOT / "app.py",
        *(ROOT / "tests").rglob("*.py"),
        *(ROOT / "scripts").glob("*.py"),
    ]
    problems = [p for f in sorted(files) for p in check_file(f)]
    for p in problems:
        print(p)
    print(f"checked {len(files)} files, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
