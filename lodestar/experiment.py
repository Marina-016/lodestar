"""V3 最小闭环：Research → Experiment → Build。

- extract_opportunities：从 Research Brief 的「Project Opportunities」提取可验证方向（PRD §14）。
- scaffold_experiment：确定性生成实验项目骨架（假设文档 + A/B eval harness 模板）。
- build_experiment：scaffold 后用 coding agent（codex/claude）实现 baseline/candidate。
"""
from __future__ import annotations

import re
from pathlib import Path

EXPERIMENT_DIR_PREFIX = "experiment_"


def extract_opportunities(brief_md: str) -> list[str]:
    """从 brief 的「## Project Opportunities」节提取 bullets（去掉 `- **可验证方向**：` 前缀）。"""
    if not brief_md:
        return []
    m = re.search(r"## Project Opportunities\s*\n(.*?)(?=\n## |\Z)", brief_md, re.S)
    if not m:
        return []
    out = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        if re.fullmatch(r"[-*_]{3,}", line):  # markdown 分隔线（---/***）不是 bullet
            continue
        item = re.sub(r"^-\s*\**可验证方向\**\s*[:：]?\s*", "", line).strip()
        if item:
            out.append(item)
    return out


# ----------------------------------------------------------------------
# 确定性 scaffold
# ----------------------------------------------------------------------
def scaffold_experiment(exp: dict, out_dir: Path, brief_hypothesis: str | None = None) -> Path:
    """生成实验项目骨架，返回项目目录。确定性 code（PRD §26③）。"""
    project = Path(out_dir) / f"{EXPERIMENT_DIR_PREFIX}{exp['id']}"
    project.mkdir(parents=True, exist_ok=True)
    hypothesis = brief_hypothesis or exp.get("hypothesis", "（未填写假设）")
    task_id = exp.get("task_id") or "—"

    (project / "README.md").write_text(
        f"# 实验 {exp['id']}：{hypothesis[:60]}\n\n"
        f"- 来源研究任务：`{task_id}`\n"
        f"- 假设：{hypothesis}\n"
        f"- 描述：{exp.get('description') or '（无）'}\n\n"
        "## 运行\n```bash\npython eval.py\n```\n"
        "eval.py 会比较 baseline 与 candidate 在固定指标上的表现。\n",
        encoding="utf-8",
    )
    (project / "hypothesis.md").write_text(
        f"# 假设与验证计划\n\n- **来源任务**：{task_id}\n- **假设**：{hypothesis}\n"
        f"- **原始依据**：{exp.get('source_claim') or '（无）'}\n\n"
        "## 验证方式\n固定一组 Research Cases / 指标，比较 baseline 与 candidate：\n"
        "Source Quality / Novelty / Recall / Task Success（按主题调整）。\n",
        encoding="utf-8",
    )
    (project / "baseline.py").write_text(
        "# baseline 实现（A/B 的对照组）\n"
        "def run() -> dict:\n"
        "    \"\"\"返回 {指标名: 值}。TODO：把这里替换成真实 baseline 逻辑。\"\"\"\n"
        "    return {\"score\": 0.0, \"note\": \"baseline 占位\"}\n",
        encoding="utf-8",
    )
    (project / "candidate.py").write_text(
        "# candidate 实现（A/B 的实验组，由 coding agent 或你实现）\n"
        "def run() -> dict:\n"
        "    \"\"\"接口必须与 baseline.run 一致。TODO：实现你的假设。\"\"\"\n"
        "    return {\"score\": 1.0, \"note\": \"candidate 占位\"}\n",
        encoding="utf-8",
    )
    (project / "eval.py").write_text(
        "import json\nfrom baseline import run as baseline_run\n"
        "from candidate import run as candidate_run\n\n"
        "b = baseline_run()\nc = candidate_run()\n"
        "print('=== baseline ==='); print(json.dumps(b, ensure_ascii=False, indent=2))\n"
        "print('=== candidate ==='); print(json.dumps(c, ensure_ascii=False, indent=2))\n"
        "print('\\n对比：candidate 相对 baseline 的提升需按指标逐项判定（此处仅打印）。')\n",
        encoding="utf-8",
    )
    (project / "requirements.txt").write_text("# 在此列出实验依赖\n", encoding="utf-8")
    return project


# ----------------------------------------------------------------------
# Build：scaffold + coding agent 实现
# ----------------------------------------------------------------------
def build_experiment(exp: dict, out_dir: Path, executor, hypothesis: str | None = None,
                     timeout: int = 300) -> tuple[Path, object]:
    """scaffold 后调用 coding agent（codex/claude）实现 baseline/candidate。

    返回 (项目目录, ExecutorResult)。executor 不可用/被拒时抛出 RuntimeError。
    """
    project = scaffold_experiment(exp, out_dir, hypothesis)
    prompt = (
        "读取当前目录的 README.md、hypothesis.md、baseline.py、candidate.py、eval.py。"
        "把 baseline.py 与 candidate.py 实现成最小可运行版本：两者 run() 接口必须一致、"
        "返回相同结构的指标 dict，并且能跑通 `python eval.py`（不要改 eval.py 的接口与 import）。"
        "如果假设无法落地成代码，就在文件里写清楚为什么，并保持占位可运行。"
    )
    result = executor.run(prompt, cwd=str(project), timeout=timeout)
    return project, result
