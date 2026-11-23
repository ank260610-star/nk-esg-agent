"""Task 6 导出失败测试：同版本三格式、缺失不当0、公式注入转义、路径安全。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))

from esg_app.exports import (  # noqa: E402
    ArtifactStore, ExportError, build_export_bundle, csv_safe_cell,
)


def _records():
    return [
        {"metric_id": "E01", "metric_year": 2024, "raw_value": "10,101,826.20",
         "value": "10101826.20", "raw_unit": "tCO2e", "canonical_unit": "tCO2e",
         "status": "extracted", "origin": "reported", "evidence_ids": ["ev1"],
         "is_current": True},
        # 缺失：value=None，raw_value="/" —— CSV 必须是空格/斜线原文，绝不能写 0
        {"metric_id": "E03", "metric_year": 2023, "raw_value": "/", "value": None,
         "raw_unit": "tCO2e", "canonical_unit": "tCO2e", "status": "missing_marker",
         "origin": "reported", "evidence_ids": ["ev2"], "is_current": True},
    ]


def _score():
    return {
        "revision": 7, "rule_version": "disclosure-v0.1",
        "coverage": "0.125", "reference_score": "75", "final_score": None,
        "lower_bound": "9.375", "upper_bound": "96.875",
        "dimension_results": [], "label": "insufficient_evidence",
        "is_current": True,
    }


class TestCsvSafety:
    def test_missing_never_written_as_zero(self):
        cell = csv_safe_cell(None)
        assert cell == ""
        assert csv_safe_cell("/") == "/"
        assert cell != "0"

    @pytest.mark.parametrize("payload", ["=1+1", "+cmd|'/c calc", "@SUM(A1)", "-1+1", "\t=EVIL", "\r=EVIL"])
    def test_formula_injection_escaped(self, payload):
        assert csv_safe_cell(payload).startswith("'")

    def test_normal_negative_number_untouched(self):
        assert csv_safe_cell("-18.15") == "-18.15"


class TestExportBundle:
    def test_three_formats_share_revision_and_versions(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        bundle = build_export_bundle(
            task_id="task_web", expected_revision=7, current_revision=7,
            records=_records(), score=_score(), checks=[],
            comparisons=[], evidence=[], rule_version="disclosure-v0.1",
            report_meta=[{"report_id": "rpt_1", "report_year": 2024,
                          "sha256": "abc"}],
            store=store,
        )
        assert bundle["revision"] == 7
        assert bundle["rule_version"] == "disclosure-v0.1"
        assert len(bundle["artifacts"]) == 3
        assert {a["format"] for a in bundle["artifacts"]} == {
            "markdown", "csv", "json"}
        assert {a["revision"] for a in bundle["artifacts"]} == {7}
        for a in bundle["artifacts"]:
            assert a["artifact_id"].startswith("art_")
            assert a["download_url"] == f"/api/artifacts/{a['artifact_id']}"
            assert len(a["sha256"]) == 64
            assert (tmp_path / "artifacts" / a["filename"]).exists()

    def test_csv_contains_raw_placeholder_not_zero(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        bundle = build_export_bundle(
            task_id="task_web", expected_revision=1, current_revision=1,
            records=_records(), score=_score(), checks=[], comparisons=[],
            evidence=[], rule_version="disclosure-v0.1", report_meta=[],
            store=store,
        )
        csv_art = next(a for a in bundle["artifacts"] if a["format"] == "csv")
        text = store.get_content(csv_art["artifact_id"]).decode("utf-8")
        assert "/" in text
        assert "10101826.20" in text
        # E03 缺失值不可写成 0
        rows = [line.split(",") for line in text.splitlines()]
        e03 = [r for r in rows if "E03" in r]
        assert e03 and all("0" != c for c in e03[0])

    def test_json_is_parseable_and_bound_to_revision(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        bundle = build_export_bundle(
            task_id="task_web", expected_revision=9, current_revision=9,
            records=_records(), score=_score(), checks=[], comparisons=[],
            evidence=[], rule_version="disclosure-v0.1", report_meta=[],
            store=store,
        )
        art = next(a for a in bundle["artifacts"] if a["format"] == "json")
        payload = json.loads(store.get_content(art["artifact_id"]).decode("utf-8"))
        assert payload["revision"] == 9
        assert payload["task_id"] == "task_web"
        assert payload["metrics"]

    def test_stale_bundle_flagged_and_old_artifacts_kept(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        b1 = build_export_bundle(
            "task_web", 7, 7, _records(), _score(), [], [], [],
            "disclosure-v0.1", [], store)
        store.invalidate_task("task_web")
        b2 = build_export_bundle(
            "task_web", 8, 8, _records(), _score(), [], [], [],
            "disclosure-v0.1", [], store)
        assert all(store.is_current(a["artifact_id"]) is False for a in b1["artifacts"])
        assert all(store.is_current(a["artifact_id"]) is True for a in b2["artifacts"])
        assert store.get_content(b1["artifacts"][0]["artifact_id"])

    def test_revision_conflict_rejected(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(ExportError) as exc:
            build_export_bundle(
                "task_web", 7, 8, _records(), _score(), [], [], [],
                "disclosure-v0.1", [], store)
        assert exc.value.code == "REVISION_CONFLICT"

    def test_path_traversal_id_refused(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        assert store.get_content("../secret") is None
        assert store.get_content("..\\secret") is None
        assert store.get_content("") is None
