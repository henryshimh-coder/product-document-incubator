from __future__ import annotations

import hashlib
from typing import Any

import streamlit as st


def render_single_file_upload() -> Any | None:
    uploaded = st.file_uploader(
        "选择资料文件",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=False,
        help="支持 PDF、DOCX、TXT、MD；单文件最大 20MB。文件先保存在本地。",
        key="ingest_file",
    )
    if uploaded is None:
        st.caption("比赛版每次处理 1 个文件；材料不会绕过本地脱敏与授权检查。")
        return None
    payload = uploaded.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    st.info(f"已选择：{uploaded.name} · {len(payload) / 1024:.1f} KB · SHA-256 {digest[:8]}")
    st.caption("本地保存位置：项目资料归档（不展示设备绝对路径）")
    return uploaded
