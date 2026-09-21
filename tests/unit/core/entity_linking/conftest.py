"""Shared fakes for the spec #81 linker tests."""

import re
from typing import Callable, Dict, List, Optional, Union

from tests.conftest import MockLLMProvider
from thestill.core.entity_linking.types import NameGroup, surface_key
from thestill.models.entities import EntityMention

Answer = Union[Exception, dict, Callable[[str], dict]]


class ScriptedProvider(MockLLMProvider):
    """One scripted answer per ``generate_structured`` call, in order. An
    Exception is raised, a dict is validated as the response, a callable gets
    the user message and returns the dict. Every call fails or succeeds on its
    own (failure-mode catalogue: consistent-mock tests)."""

    def __init__(self, answers: List[Answer], model_name: str = "mock-linker"):
        super().__init__(model_name=model_name)
        self._answers = list(answers)
        self.user_messages: List[str] = []
        self.system_messages: List[str] = []

    def generate_structured(self, messages, response_model, temperature=None, max_tokens=None):
        self.system_messages.append(messages[0]["content"])
        self.user_messages.append(messages[-1]["content"])
        if not self._answers:
            raise AssertionError("ScriptedProvider exhausted: more LLM calls than scripted answers")
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            answer = answer(messages[-1]["content"])
        return response_model(**answer)


def ids_in(user_message: str) -> List[str]:
    return re.findall(r"^\[(n\d+)\]$", user_message, flags=re.MULTILINE)


def pick_first_candidate(user_message: str) -> dict:
    """Answers every name in the message with its first listed candidate."""
    choices = []
    for block in re.split(r"^\[(?=n\d+\]$)", user_message, flags=re.MULTILINE)[1:]:
        name_id = block.split("]", 1)[0]
        match = re.search(r"^- (Q\d+) \|", block, flags=re.MULTILINE)
        choices.append(
            {"id": name_id, "qid": match.group(1) if match else None, "confidence": "high", "reason": "fits"}
        )
    return {"choices": choices}


def group(name: str, label: Optional[str] = "person", mention_ids=(1,), excerpts=("said it",)) -> NameGroup:
    return NameGroup(
        surface_key=surface_key(name),
        surface_form=name,
        surface_label=label,
        mention_ids=list(mention_ids),
        excerpts=list(excerpts),
    )


def mention(
    mention_id: int, name: str, label: str = "person", excerpt: str = "", episode_id: str = "ep-1"
) -> EntityMention:
    return EntityMention(
        id=mention_id,
        episode_id=episode_id,
        segment_id=mention_id,
        start_ms=0,
        end_ms=1,
        surface_form=name,
        surface_label=label,
        quote_excerpt=excerpt or f"... {name} ...",
        confidence=0.9,
        extractor="gliner:test",
    )


Script = Dict[str, Answer]
