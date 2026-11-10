"""Nested tool inputs: explicit schemas, bounded values, no unknown fields."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Nonempty = Annotated[str, Field(min_length=1, max_length=2000)]


class SubtaskDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    kind: Literal["analyze", "implement", "test"]
    allowed_files: Annotated[list[Nonempty], Field(min_length=1, max_length=100)]
    acceptance: Annotated[list[Nonempty], Field(min_length=1, max_length=100)]


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: Nonempty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    required_content: list[Nonempty] = Field(default_factory=list)


class SubtaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
    status: Literal["PASS", "FAIL", "BLOCKED", "NOT_RUN", "SUBSTITUTED"]
    evidence: list[EvidenceReference] = Field(default_factory=list)
