from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class RunQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: Identifier
    question: Question
    scope: Literal["effective", "effective_with_notices", "historical"]
    historical_version: Identifier | None = None
    preferred_mode: Literal["realtime", "cache"] = "realtime"
