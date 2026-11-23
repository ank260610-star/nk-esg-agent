"""Task 3 真机验证：真实模型从比亚迪 PDF 提取六指标。

链路：
1. pdfplumber 抽取真实 PDF 六指标相关页文本（p125/126/127）
2. 构造提取 prompt（metric-extraction.md 规则 + 证据文本）
3. 真实 DeepSeek Harness SDK 调用模型 → JSON 候选
4. 解析候选 → 走 extract_metrics（真实 revision/校验）→ 对照 gold

不在脚本内硬编码提取结果；模型输出什么就记录什么，如实报告正确/遗漏/错误。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

import pdfplumber
from deepseek_harness import DeepSeekHarness
from esg_app.extraction import ExtractionDeps, extract_metrics

PDF = Path(os.environ.get(
    "ESG_BYD_PDF",
    r"D:\wechat\xwechat_files\wxid_wcxepqc1irne22_7f1b\msg\file\2026-11\比亚迪_2024年度可持续发展报告(1).pdf",
))
DSH_HOME = os.environ.get("ESG_DSH_HOME", r"D:\esg-agent-runtime\dsh-home")
WORKSPACE = os.environ.get("ESG_WORKSPACE", r"D:\esg-agent-runtime\workspace")
MODEL = os.environ.get("DSH_MODEL", "deepseek-v4-flash")

# 六指标 2024 gold 值。表头确认为 2022/2023/2024 正序（p125 L3 / p127 L2），
# 故 2024 = 每行最后一列。此前侦察稿把 E01/E02 的中间列(2023)误当 2024，已据表头修正。
GOLD = {
    "E01": {"value": "10101826.20", "unit": "吨二氧化碳当量", "pdf_page": 127, "label": "范围1+2排放总量(2024)"},
    "E02": {"value": "0.1300", "unit": "吨二氧化碳当量/万元营收", "pdf_page": 127, "label": "排放强度(2024)"},
    "E03": {"value": "3326312.49", "unit": "吨二氧化碳当量", "pdf_page": 127, "label": "范围3排放(2024)"},
    "E04": {"value": "23552569.28", "unit": "兆瓦时", "pdf_page": 125, "label": "能源总耗量(2024)"},
    "E05": {"value": "94268627", "unit": "立方米", "pdf_page": 126, "label": "总取水量(2024)"},
    "E06": {"value": "55317707", "unit": "立方米", "pdf_page": 126, "label": "总耗水量(2024)"},
}
METRIC_YEAR = 2024

# 真实指标行名关键词，用于从页面文本中抓取证据行
ROW_HINTS = {
    "E01": ["范围1", "范围一", "温室气体排放总量"],
    "E02": ["温室气体排放密度", "排放强度", "排放密度"],
    "E03": ["范围3", "范围三"],
    "E04": ["能源总耗量", "能源消耗总量", "总能耗", "能源消耗量", "综合能耗", "能源消费总量"],
    "E05": ["总取水量", "取水总量"],
    "E06": ["总耗水量", "耗水总量"],
}


def extract_evidence_lines() -> dict[str, list[tuple[int, str]]]:
    """从真实 PDF 的相关页提取六指标证据（页码 + 文本）。

    双栏混排报告的标签行与数值行常错位，故命中行后连带其后 2 行
    一起纳入证据块，让模型能拿到完整年度序列（这不是替模型填值）。
    """
    target_pages = {125, 126, 127}  # 1-based PDF 物理页
    found: dict[str, list[tuple[int, str]]] = {k: [] for k in GOLD}
    with pdfplumber.open(str(PDF)) as pdf:
        for pno in sorted(target_pages):
            lines = [ln.strip() for ln in (pdf.pages[pno - 1].extract_text() or "").splitlines()]
            for i, line in enumerate(lines):
                if not line:
                    continue
                for mid, hints in ROW_HINTS.items():
                    if any(h in line for h in hints):
                        # 双栏错位：数值可能在标签上一行或后两行，全部纳入证据块
                        lo, hi = max(0, i - 1), min(len(lines), i + 3)
                        block = " | ".join(x for x in lines[lo:hi] if x)
                        if re.search(r"\d", block):
                            found[mid].append((pno, block))
    return found


def build_prompt(evidence: dict[str, list[tuple[int, str]]]) -> str:
    """构造后端提取 prompt（证据数据 + 六指标要求 + JSON 输出约束）。"""
    ev_blocks = []
    for mid, lines in evidence.items():
        for pno, text in lines:
            ev_blocks.append(f"[{mid} 候选证据 PDF页{pno}] {text}")
    evidence_text = "\n".join(ev_blocks) if ev_blocks else "（未定位到证据行）"

    return f"""你是后端指标提取模型。只根据 below 证据，为 6 个指标输出候选 JSON 数组，不要 Markdown 围栏，不要解释。

指标与年度（每个 metric_id × 2024 都要有一个对象，无证据也要输出 status=unknown 且 extraction_note 说明）：
- E01 范围1+范围2温室气体排放量，单位 吨二氧化碳当量
- E02 温室气体排放强度，单位 吨二氧化碳当量/万元营收
- E03 范围3温室气体排放量，单位 吨二氧化碳当量
- E04 能源总耗量，单位 兆瓦时
- E05 总取水量，单位 立方米
- E06 总耗水量，单位 立方米

证据（来自比亚迪2024可持续发展报告真实 PDF，PDF物理页）：
{evidence_text}

关键列序线索（表格阅读通用规则，非答案）：
- 表格年度通常为 2022 / 2023 / 2024 三列，2024 是最后一列。每行若有 3 个数值，取最后一个作为 2024 值。
- 注意区分口径相近的行：范围1+范围2 ≠ 范围1+范围2+范围3；排放密度（范围1+范围2）≠ 排放密度（含范围3）。只取指标定义对应的那一行。
- 双栏混排可能导致数值序列出现在标签行的上一行或下一行；若同一指标出现多个候选行，按指标全名（含括号内的范围说明）精确匹配，不确定则输出 conflict 保留各候选。

规则：
- value 只能是纯十进制字符串（如 "12341455"）或 null；不要用 JSON 数字，不要千分位逗号。
- status: extracted（有可靠值）/ missing_marker（原文"/"）/ unknown（未定位）/ parse_error / conflict。
- evidence_ids 引用给出的 PDF页，格式如 "pyd_p127"；每个候选至少引用一条。
- 同指标同年多个不同值要各出一条 status=conflict 候选，不要二选一。
- 不得凭记忆或训练知识补数值；证据不足就 unknown。

每个对象字段（全部必填，可空用 null）：
report_id, metric_id, metric_year, raw_value, value, raw_unit, canonical_unit, conversion_rule, denominator, boundary_text, boundary_key, method_text, scope3_categories, evidence_ids, status, origin, supersedes_id, derivation, extraction_note

report_id 固定 "byd_report_2024"；origin 固定 "reported"；metric_year 固定 2024。"""


def parse_model_json(response: str) -> list[dict]:
    """从模型输出里提取 JSON 数组（容忍围栏前后文字）。"""
    text = response.strip()
    # 去 markdown 围栏
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # 找第一个 [ 到最后一个 ]
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def normalize(v: str | None) -> str | None:
    if v is None:
        return None
    return v.replace(",", "").strip()


def main() -> None:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise SystemExit("DEEPSEEK_API_KEY missing")
    print("=== STEP 1 真实 PDF 证据抽取 ===")
    evidence = extract_evidence_lines()
    for mid, lines in evidence.items():
        print(f"{mid}: {len(lines)} 行证据")
        for pno, text in lines[:2]:
            print(f"   p{pno}: {text[:90]}")

    prompt = build_prompt(evidence)
    print("\n=== STEP 2 真实模型调用 ===")
    with DeepSeekHarness(
        dsh_home=DSH_HOME, cwd=WORKSPACE, profile="sdk",
        provider="deepseek-official", model=MODEL,
        reasoning_effort="low", max_tokens=49_152, initialize_timeout_seconds=60,
        env={"DSH_PERMISSION_MODE": "read-only"},
    ) as harness:
        result = harness.run(prompt, session_id=f"extract-byd-{int(time.time())}")
    print("finish_reason:", result.finish_reason)
    response = result.final_response or ""
    print("response_len:", len(response))
    # 诊断：若 final_response 为空但 events 里有文本，打印事件类型辅助定位
    if not response and result.events:
        from collections import Counter
        print("event_types:", Counter(e.get("type", "?") for e in result.events if isinstance(e, dict)))

    print("\n=== STEP 3 解析候选 ===")
    candidates = parse_model_json(response)
    print(f"模型返回候选数: {len(candidates)}")
    for c in candidates:
        print(f"  {c.get('metric_id')}/{c.get('metric_year')} value={c.get('value')} "
              f"status={c.get('status')} note={str(c.get('extraction_note',''))[:40]}")

    print("\n=== STEP 4 走 extract_metrics 校验链 ===")
    allowed_evidence_ids = {c for cand in candidates for c in (cand.get("evidence_ids") or [])}
    if not allowed_evidence_ids:
        allowed_evidence_ids = {f"pyd_p{p}" for p in (125, 126, 127)}
    deps = ExtractionDeps(
        model_fn=lambda ctx: candidates, extractor_version="backend-extractor-v0.1",
        model_id=MODEL, prompt_version="metric-extraction-v0.2.0",
    )
    extraction = extract_metrics(
        task_id="byd_extract_task", expected_revision=1, current_revision=1,
        metric_ids=list(GOLD.keys()), years=[METRIC_YEAR],
        allowed_evidence_ids=allowed_evidence_ids, deps=deps,
    )
    print("extraction.status:", extraction.status)
    print("accepted records:", len(extraction.records))
    print("issues:", [i["code"] for i in extraction.issues])

    print("\n=== STEP 5 对照 gold ===")
    correct, wrong, missing = [], [], []
    got_by_metric: dict[str, list[str]] = {}
    for r in extraction.records:
        got_by_metric.setdefault(r["metric_id"], []).append(normalize(r.get("value")))
    for mid, gold in GOLD.items():
        vals = got_by_metric.get(mid, [])
        gold_v = normalize(gold["value"])
        if gold_v in vals:
            correct.append(mid)
            print(f"  ✅ {mid} {gold['label']} = {gold_v}")
        elif vals:
            wrong.append(mid)
            print(f"  ❌ {mid} {gold['label']} 期望 {gold_v}, 得到 {vals}")
        else:
            missing.append(mid)
            print(f"  ⬜ {mid} {gold['label']} 未提取")
    print(f"\n结果: 正确 {len(correct)}/6, 错误 {len(wrong)}, 遗漏 {len(missing)}")
    print("run.usage:", json.dumps(extraction.run.usage, ensure_ascii=False))


if __name__ == "__main__":
    main()
