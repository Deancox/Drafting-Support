"""Evidence layer: immutable claims written by parsers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from drawing_qa.model.enums import DocType, EntityType, ExtractionMethod, Predicate
from drawing_qa.model.source import SourceRef


class Claim(BaseModel):
    """One fact from one place on one sheet, as read by one parser.

    Keys are canonical (produced by the identifier grammar); the original
    spelling is kept in source.raw_text.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    parser: DocType
    parser_version: str
    method: ExtractionMethod
    confidence: float = 1.0
    subject_type: EntityType
    subject_key: str
    predicate: Predicate
    object_type: EntityType | None = None
    object_key: str | None = None
    value: dict[str, Any] = Field(default_factory=dict)
    source: SourceRef
