"""T12 演示材料的唯一生成规则。

`tests/fixtures/sources/` 下的四份演示来源材料由本模块生成，集成测试据此断言
夹具与生成规则逐字节一致，避免夹具、bootstrap 基线材料与演示脚本各自漂移。

- `current_product.md` 与 `scripts/bootstrap_demo.py` 的基线材料逐字节相同
  （直接引用其常量，bootstrap 为既有已验收代码，不改写）。
- `risk_opinion.md` 与联合验收（joint_acceptance.py）使用的风险材料逐字节相同
  （联合验收侧有自己的独立常量，本模块与之保持同构生成，测试侧双向比对）。
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bootstrap_demo import BASE_SOURCE_CONTENT

CURRENT_PRODUCT_FILENAME = "current_product.md"
RISK_OPINION_FILENAME = "risk_opinion.md"
MEETING_MINUTES_FILENAME = "meeting_minutes.md"
TECHNICAL_REVIEW_FILENAME = "technical_review.md"

RISK_SENTENCE = "风险意见要求收紧目标客群。"
MEETING_CONCLUSION = "会议决定采纳风险意见，收紧目标客群规则。"
TECH_REVIEW_CONCLUSION = "技术评审确认客群规则调整方案可行，按变更单实施。"
DEMO_QUESTION = "当前目标客群是什么？"


def build_current_product_content() -> str:
    """正式基线材料：与 bootstrap 归档的演示基线来源逐字节一致。"""
    return BASE_SOURCE_CONTENT


def build_risk_opinion_content() -> str:
    """正式风险意见：驱动演示主流程（lint 冲突 → 决定 → 变更 → 发布）。"""
    return (
        "# 风险意见书\n\n"
        f"{RISK_SENTENCE}\n\n"
        "## 风险背景\n\n"
        + "\n\n".join(f"第{i}段说明文字，用于记录风险排查过程与数据口径。" for i in range(1, 301))
        + "\n"
    )


def build_meeting_minutes_content() -> str:
    """会议纪要：记录采纳风险意见并形成收紧口径的正式决定。"""
    return (
        "# 会议纪要\n\n"
        f"{MEETING_CONCLUSION}\n\n"
        "## 决议事项\n\n"
        "1. 目标客群由“符合准入要求的存量客户”收紧为“符合准入要求且通过风险评估的存量客户”。\n"
        "2. 产品负责人完成规则调整并提交变更单，明确修改前后内容与影响对象。\n"
        "3. 调整经人工批准且完成影响检查后，发布新版本基线并保留版本间差异。\n\n"
        "## 参会与责任\n\n"
        "产品负责人确认执行口径，风险负责人确认评估标准，会议记录归档备查。\n\n"
        "## 会议背景\n\n"
        + "\n\n".join(f"第{i}段会议讨论记录，用于还原决策过程与各方意见。" for i in range(1, 201))
        + "\n"
    )


def build_technical_review_content() -> str:
    """技术评审：确认调整方案可行并划定回归验证范围。"""
    return (
        "# 技术评审记录\n\n"
        f"{TECH_REVIEW_CONCLUSION}\n\n"
        "## 评审范围\n\n"
        "1. 目标客群规则字段调整对当前查询与一键自检链路的影响。\n"
        "2. 发布前后版本差异与六节点追溯关系的保留要求。\n"
        "3. 回归验证范围：当前查询、历史查询与追溯链路完整性。\n\n"
        "## 评审意见\n\n"
        "调整只影响目标客群卡片正文，接口约束保持不变；发布前需完成影响检查。\n\n"
        "## 评审背景\n\n"
        + "\n\n".join(f"第{i}段评审记录，用于说明技术口径与验证标准。" for i in range(1, 201))
        + "\n"
    )


MATERIAL_BUILDERS: dict[str, Callable[[], str]] = {
    CURRENT_PRODUCT_FILENAME: build_current_product_content,
    RISK_OPINION_FILENAME: build_risk_opinion_content,
    MEETING_MINUTES_FILENAME: build_meeting_minutes_content,
    TECHNICAL_REVIEW_FILENAME: build_technical_review_content,
}


def write_fixtures(fixtures_dir: Path) -> dict[str, str]:
    """把全部演示材料写入夹具目录，返回 文件名 → sha256。"""
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for filename, builder in MATERIAL_BUILDERS.items():
        payload = builder().encode("utf-8")
        (fixtures_dir / filename).write_bytes(payload)
        digests[filename] = hashlib.sha256(payload).hexdigest()
    return digests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the T12 demo source fixtures.")
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root / "tests/fixtures/sources",
        help="Fixture output directory (defaults to tests/fixtures/sources).",
    )
    arguments = parser.parse_args(argv)
    digests = write_fixtures(arguments.output.resolve())
    for filename, digest in sorted(digests.items()):
        print(f"FIXTURE_OK {filename} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
