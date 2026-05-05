from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from chloe.actions.schema import AuthClass


@dataclass
class ToolVerb:
    name: str
    schema: dict
    auth_class: AuthClass
    reversibility: float
    cost_per_call_usd: float = 0.0
    description_for_model: str = ""
    description_for_human: str = ""
    dry_run: bool = False
    reverse_verb: str | None = None


@dataclass
class ToolResult:
    success: bool
    data: dict | None = None
    error: str | None = None
    artifact_ref: str | None = None
    artifact_kind: str | None = None
    is_dry_run: bool = False


class FeatureDisabledError(Exception):
    pass


class CapExceededError(Exception):
    pass


class Tool(ABC):
    name: str
    verbs: dict[str, ToolVerb]

    @abstractmethod
    async def execute(self, verb: str, args: dict[str, Any]) -> ToolResult: ...

    def dry_run(self, verb: str, args: dict[str, Any]) -> str:
        tv = self.verbs.get(verb)
        if not tv:
            return f"[{self.name}.{verb}] unknown verb"
        args_summary = ", ".join(f"{k}={repr(v)[:40]}" for k, v in args.items())
        return f"Would {self.name}.{verb}({args_summary})"

    def get_verb(self, verb: str) -> ToolVerb | None:
        return self.verbs.get(verb)
