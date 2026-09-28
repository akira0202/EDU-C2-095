"""Regression: every quantifier in the PII / credential detection patterns is bounded.

These patterns run over attacker-influenced text (caller free text or LLM output). An
unbounded quantifier (``+``, ``*``, ``{N,}``) degrades to quadratic backtracking there, so
the bound is a security property, not a style choice. Parsed from source with ``ast`` so the
check needs no framework import and no runtime dependency of the module under test.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MODULES = {
    "src/services/service.py": {"_SUMMARY_EMAIL_RE", "_SUMMARY_LONG_DIGITS_RE", "_SUMMARY_PHONE_RE"},
}

_ESCAPE_RE = re.compile(r"\\.")
_CHAR_CLASS_RE = re.compile(r"\[[^\]]*\]")
_UNBOUNDED_RE = re.compile(r"[+*]|\{\d+,\}")


def _unbounded(pattern: str) -> list[str]:
    """Quantifiers left unbounded, ignoring escapes and character-class contents."""
    normalized = _CHAR_CLASS_RE.sub("C", _ESCAPE_RE.sub("", pattern))
    return _UNBOUNDED_RE.findall(normalized)


def _patterns_of(module: str, names: set[str]) -> dict[str, list[str]]:
    tree = ast.parse(Path(module).read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = getattr(node.targets[0], "id", "")
        if target not in names:
            continue
        found[target] = [
            child.args[0].value
            for child in ast.walk(node.value)
            if isinstance(child, ast.Call)
            and getattr(child.func, "attr", "") == "compile"
            and child.args
            and isinstance(child.args[0], ast.Constant)
            and isinstance(child.args[0].value, str)
        ]
    return found


class TestDetectionPatternsAreBounded:
    def test_every_declared_pattern_is_found(self):
        for module, names in _MODULES.items():
            found = _patterns_of(module, names)
            assert set(found) == names, f"{module}: {names - set(found)} not found"
            for name, patterns in found.items():
                assert patterns, f"{module}:{name} declares no compiled pattern"

    def test_no_unbounded_quantifier(self):
        for module, names in _MODULES.items():
            for name, patterns in _patterns_of(module, names).items():
                for pattern in patterns:
                    left = _unbounded(pattern)
                    assert not left, f"{module}:{name} unbounded {left} in {pattern!r}"
