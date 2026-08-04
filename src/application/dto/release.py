from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from src.domain.enums import ChangeReviewAction

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
ReviewCommentStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10, max_length=200),
]
ReleaseNoteStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=20, max_length=200),
]


class ReviewChangeRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    change_request_id: NonEmptyStr
    action: ChangeReviewAction
    reviewed_by: NonEmptyStr
    comment: ReviewCommentStr
    idempotency_key: NonEmptyStr


class PublishBaselineInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: NonEmptyStr
    change_request_id: NonEmptyStr
    approved_by: NonEmptyStr
    impact_reviewed: bool
    release_note: ReleaseNoteStr
