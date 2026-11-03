from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EvaluationSample:
    query: str
    relevant_asset_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    metrics: dict[str, float]


class RetrievalEvaluator(Protocol):
    """Evaluates retrieval output against a labeled dataset."""

    async def evaluate(self, samples: list[EvaluationSample]) -> EvaluationReport: ...

