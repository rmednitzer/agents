"""Contract for the _example markdown-validator workload."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from harness import Contract, Severity, predicate

__all__ = [
    "Finding",
    "ValidationInput",
    "ValidationReport",
    "contract",
]


class ValidationInput(BaseModel):
    """Input: a markdown document to validate."""

    model_config = ConfigDict(frozen=True)

    content: str
    document_name: str = "untitled"


class Finding(BaseModel):
    """One style violation discovered in the document."""

    model_config = ConfigDict(frozen=True)

    rule: str
    line: int | None = None
    message: str


class ValidationReport(BaseModel):
    """Output: validation result with all findings."""

    model_config = ConfigDict(frozen=True)

    document_name: str
    passed: bool
    findings: list[Finding] = Field(default_factory=list)


@predicate(name="content_non_empty", severity=Severity.HARD)
def content_non_empty(state: ValidationInput) -> bool:
    """Reject empty or whitespace-only documents up front."""
    return bool(state.content and state.content.strip())


@predicate(name="passed_consistent_with_findings", severity=Severity.HARD)
def passed_consistent_with_findings(state: ValidationReport) -> bool:
    """passed must be true iff findings is empty."""
    return state.passed == (len(state.findings) == 0)


@predicate(name="document_name_preserved", severity=Severity.SOFT)
def document_name_preserved(state: ValidationReport) -> bool:
    """The output retains a document_name. Soft check for demonstration."""
    return bool(state.document_name)


contract: Contract[ValidationInput, ValidationReport] = Contract(
    name="_example",
    version="0.1.0",
    preconditions=[content_non_empty],
    postconditions=[passed_consistent_with_findings, document_name_preserved],
)
