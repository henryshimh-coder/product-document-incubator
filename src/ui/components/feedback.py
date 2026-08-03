from __future__ import annotations

from typing import Literal

import streamlit as st
from pydantic import BaseModel, ConfigDict


class UserFeedback(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    impact: str
    next_action: str
    error_code: str
    offer_cache: bool = False
    offer_local: bool = False
    level: Literal["warning", "error"] = "error"


def render_feedback(feedback: UserFeedback) -> None:
    message = (
        f"**{feedback.title}**  \n"
        f"影响：{feedback.impact}  \n"
        f"下一步：{feedback.next_action}  \n"
        f"错误码：`{feedback.error_code}`"
    )
    if feedback.level == "warning":
        st.warning(message)
    else:
        st.error(message)
