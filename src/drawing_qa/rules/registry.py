"""Rule registry. Rules register with @rule; the engine imports the category modules."""

from __future__ import annotations

import importlib

from drawing_qa.rules.base import Rule

_RULES: dict[str, type[Rule]] = {}

# Category modules, imported for their @rule side effects.
_MODULES = ("tag", "wire", "plc", "path", "term", "dev", "comp", "placeholder", "drw")


def rule(cls: type[Rule]) -> type[Rule]:
    rule_id = cls.meta.rule_id
    if rule_id in _RULES and _RULES[rule_id] is not cls:
        raise ValueError(f"Duplicate rule id {rule_id}: {_RULES[rule_id].__name__} and {cls.__name__}")
    _RULES[rule_id] = cls
    return cls


def all_rules() -> list[type[Rule]]:
    for name in _MODULES:
        importlib.import_module(f"drawing_qa.rules.{name}")
    return [_RULES[k] for k in sorted(_RULES, key=_sort_key)]


def _sort_key(rule_id: str) -> tuple[str, int]:
    prefix, _, number = rule_id.rpartition("-")
    return prefix, int(number) if number.isdigit() else 0
