"""Rule base class and metadata (IMPLEMENTATION_PLAN.md section 7.2)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from drawing_qa.model.enums import Severity

if TYPE_CHECKING:
    from drawing_qa.model.findings import Finding
    from drawing_qa.rules.context import RuleContext
    from drawing_qa.rules.findings import FindingBuilder


class Requirement(StrEnum):
    """Data a rule needs. Rules whose requirements are not available are skipped, not failed."""

    DRAWINGS = "DRAWINGS"  # title blocks read
    CLAIMS = "CLAIMS"  # parser output
    GRID = "GRID"  # grid frames read
    GRAPH = "GRAPH"  # linked engineering model (M3)
    TRACES = "TRACES"  # signal traces (M3-M5)
    TERMINATIONS = "TERMINATIONS"  # termination strips read (M2); missing when TERM is left out of the review


class Category(StrEnum):
    TAG = "Tag Validation"
    WIRE = "Wire Validation"
    PLC = "PLC Validation"
    PATH = "Path Validation"
    TERM = "Termination Validation"
    DEV = "Device Validation"
    DRW = "Drawing Validation"
    PLACEHOLDER = "Placeholder Validation"
    COMP = "Completeness Validation"


@dataclass(frozen=True)
class RuleMeta:
    rule_id: str
    title: str
    category: Category
    default_severity: Severity
    description: str
    requires: frozenset[Requirement] = field(default_factory=lambda: frozenset({Requirement.CLAIMS}))
    version: str = "1.0"


class Rule(ABC):
    meta: ClassVar[RuleMeta]

    @abstractmethod
    def evaluate(self, ctx: RuleContext, make: FindingBuilder) -> Iterable[Finding]:
        """Yield findings. Create them only through `make`."""
