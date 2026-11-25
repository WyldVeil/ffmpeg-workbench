"""Tiny mutation tester: python devtools/mutate.py . MODULE_PATH TEST_FILE [pytest -k expr]

Makes one small change at a time to MODULE_PATH (a comparison flipped, and/or
swapped, a `not` dropped, a number nudged, True/False swapped), runs the tests,
and reports the changes the tests did not notice ("survivors")."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path

repo, module, tests = sys.argv[1], sys.argv[2], sys.argv[3]
kexpr = sys.argv[4] if len(sys.argv) > 4 else ""
path = os.path.join(repo, module)
original = Path(path).read_text(encoding="utf-8")
tree = ast.parse(original)

FLIP = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


def sites(t: ast.AST):
    for node in ast.walk(t):
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if type(op) in FLIP:
                    yield ("compare", node, i)
        elif isinstance(node, ast.BoolOp):
            yield ("boolop", node, 0)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            yield ("not", node, 0)
        elif (
            isinstance(node, ast.Constant)
            and type(node.value) is int
            and not isinstance(node.value, bool)
        ):
            yield ("number", node, 0)
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            yield ("bool", node, 0)


class Mutate(ast.NodeTransformer):
    def __init__(self, target: int) -> None:
        self.target, self.n, self.desc = target, -1, ""

    def visit(self, node):
        node = self.generic_visit(node)
        kinds = []
        if isinstance(node, ast.Compare):
            kinds = [("compare", i) for i, op in enumerate(node.ops) if type(op) in FLIP]
        elif isinstance(node, ast.BoolOp):
            kinds = [("boolop", 0)]
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            kinds = [("not", 0)]
        elif isinstance(node, ast.Constant) and type(node.value) is int:
            kinds = [("number", 0)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            kinds = [("bool", 0)]
        for kind, i in kinds:
            self.n += 1
            if self.n != self.target:
                continue
            line = getattr(node, "lineno", 0)
            if kind == "compare":
                old = node.ops[i]
                node.ops[i] = FLIP[type(old)]()
                self.desc = f"line {line}: {type(old).__name__} -> {type(node.ops[i]).__name__}"
            elif kind == "boolop":
                new = ast.Or() if isinstance(node.op, ast.And) else ast.And()
                self.desc = f"line {line}: {type(node.op).__name__} -> {type(new).__name__}"
                node.op = new
            elif kind == "not":
                self.desc = f"line {line}: dropped not"
                return node.operand
            elif kind == "number":
                self.desc = f"line {line}: {node.value} -> {node.value + 1}"
                return ast.copy_location(ast.Constant(node.value + 1), node)
            elif kind == "bool":
                self.desc = f"line {line}: {node.value} -> {not node.value}"
                return ast.copy_location(ast.Constant(not node.value), node)
        return node


total = sum(1 for _ in sites(ast.parse(original)))
survivors, killed = [], 0
env = dict(os.environ)
try:
    for k in range(total):
        m = Mutate(k)
        mutated = m.visit(ast.parse(original))
        ast.fix_missing_locations(mutated)
        Path(path).write_text(ast.unparse(mutated), encoding="utf-8")
        cmd = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests]
        if kexpr:
            cmd += ["-k", kexpr]
        t0 = time.time()
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, env=env, timeout=300)
        if r.returncode == 0:
            survivors.append(m.desc)
        else:
            killed += 1
        print(
            f"[{k + 1}/{total}] {'SURVIVED' if r.returncode == 0 else 'killed'} {m.desc} ({time.time() - t0:.0f}s)",
            flush=True,
        )
finally:
    Path(path).write_text(original, encoding="utf-8")
print(f"\n{module}: {total} mutants, {killed} killed, {len(survivors)} survived")
for s in survivors:
    print("  survived:", s)
