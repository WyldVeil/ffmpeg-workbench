"""Tiny mutation tester: python devtools/mutate.py . MODULE_PATH TEST_FILE [pytest -k expr]

Makes one small change at a time to MODULE_PATH (a comparison flipped, and/or
swapped, a `not` dropped, a number nudged, True/False swapped), runs the tests,
and reports the changes the tests did not notice ("survivors").  The module is
restored afterwards (keep a copy anyway: a killed run can't restore it)."""

from __future__ import annotations

import ast
import subprocess
import sys
import time
from pathlib import Path

FLIP: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


class Mutate(ast.NodeTransformer):
    """Applies the *target*-th possible change (counting in visiting order)."""

    def __init__(self, target: int) -> None:
        self.target, self.seen, self.desc = target, -1, ""

    def _mine(self) -> bool:
        self.seen += 1
        return self.seen == self.target

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        self.generic_visit(node)
        for i, op in enumerate(node.ops):
            if type(op) in FLIP and self._mine():
                new = FLIP[type(op)]()
                self.desc = f"line {node.lineno}: {type(op).__name__} -> {type(new).__name__}"
                node.ops[i] = new
        return node

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        self.generic_visit(node)
        if self._mine():
            new: ast.boolop = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            self.desc = f"line {node.lineno}: {type(node.op).__name__} -> {type(new).__name__}"
            node.op = new
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, ast.Not) and self._mine():
            self.desc = f"line {node.lineno}: dropped not"
            return node.operand
        return node

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        value = node.value
        if isinstance(value, bool) and self._mine():
            self.desc = f"line {node.lineno}: {value} -> {not value}"
            return ast.copy_location(ast.Constant(not value), node)
        if type(value) is int and self._mine():
            self.desc = f"line {node.lineno}: {value} -> {value + 1}"
            return ast.copy_location(ast.Constant(value + 1), node)
        return node


def count(source: str) -> int:
    probe = Mutate(-1)
    probe.visit(ast.parse(source))
    return probe.seen + 1


def main(argv: list[str]) -> int:
    repo, module, tests = argv[1], argv[2], argv[3]
    kexpr = argv[4] if len(argv) > 4 else ""
    path = Path(repo, module)
    original = path.read_text(encoding="utf-8")
    total = count(original)
    survivors: list[str] = []
    try:
        for k in range(total):
            m = Mutate(k)
            mutated = ast.fix_missing_locations(m.visit(ast.parse(original)))
            path.write_text(ast.unparse(mutated), encoding="utf-8")
            cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests]
            cmd += ["-k", kexpr] if kexpr else []
            start = time.time()
            ok = subprocess.run(cmd, cwd=repo, capture_output=True, check=False).returncode == 0
            if ok:
                survivors.append(m.desc)
            status = "SURVIVED" if ok else "killed"
            print(f"[{k + 1}/{total}] {status} {m.desc} ({time.time() - start:.0f}s)", flush=True)
    finally:
        path.write_text(original, encoding="utf-8")
    print(f"\n{module}: {total} changes, {total - len(survivors)} caught, {len(survivors)} not")
    for s in survivors:
        print("  not caught:", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
