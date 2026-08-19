from __future__ import annotations


class ModuleError(Exception):
    """Raised when a module operation fails. Carries module name + version for traceability."""

    def __init__(self, module: str, version: str, message: str, cause: Exception | None = None):
        self.module = module
        self.version = version
        self.message = message
        self.cause = cause
        super().__init__(f"[{module}@{version}] {message}")


class NotFoundError(ModuleError):
    """The candidate (or their assembled test) does not exist. Surfaced as HTTP 404 rather than
    500 — a `/v1/tests/start` for someone who never signed up is a client mistake, not a fault."""


class ConflictError(ModuleError):
    """The request is well-formed but would break an invariant the bank has to keep — deleting a
    question out of a candidate's pending paper, or shrinking a DI set below its five. Surfaced as
    HTTP 409 so the admin panel can show the reason rather than a generic failure."""


class TopicMappingError(ModuleError): ...


class TestMappingError(ModuleError): ...


class EvaluationReportError(ModuleError): ...


class QuestionBankError(ModuleError): ...
