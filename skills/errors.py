"""Exception hierarchy for the skills package."""

from __future__ import annotations

__all__ = [
    "DispatchError",
    "NoSkillFound",
    "SkillError",
    "SkillLoadError",
    "SkillManifestError",
]


class SkillError(Exception):
    """Base for skills-package errors."""


class SkillLoadError(SkillError):
    """A skill could not be loaded from disk."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Failed to load skill at {path}: {reason}")
        self.path = path
        self.reason = reason


class SkillManifestError(SkillError):
    """A SKILL.md manifest failed validation."""

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"Skill manifest at {path} is invalid: {reason}")
        self.path = path
        self.reason = reason


class DispatchError(SkillError):
    """A dispatcher failed to produce a result."""


class NoSkillFound(DispatchError):
    """No skill in the registry matched the dispatcher's query."""

    def __init__(self, query: str, dispatcher: str) -> None:
        super().__init__(f"Dispatcher '{dispatcher}' found no skill for query: {query!r}")
        self.query = query
        self.dispatcher = dispatcher
