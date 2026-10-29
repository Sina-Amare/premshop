"""Core cannot depend on a shop module, even inside a function."""

import ast
from pathlib import Path


def test_production_core_has_no_upward_imports():
    root = Path(__file__).resolve().parents[1]
    violations = []
    for path in root.rglob("*.py"):
        if "tests" in path.relative_to(root).parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "apps":
                    names = ["apps." + alias.name for alias in node.names]
                elif node.module:
                    names = [node.module]
                if node.level >= len(path.relative_to(root).parts):
                    names += ["apps." + alias.name for alias in node.names]
            if any(name.startswith("apps.") and not name.startswith("apps.core") for name in names):
                violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not violations, violations
