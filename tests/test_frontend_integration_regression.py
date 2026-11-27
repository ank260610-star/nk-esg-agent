"""前端 8b6985c 与 PR #10 人工处置的同页集成回归（离线，额度 0）。

验证模板冲突合并后两组表单共存、跨接口状态独立、终态文案不被弱化为事实确认。
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_review_dispositions import setup, state, submit  # noqa: E402
from test_review_integration import metric_request  # noqa: E402


def test_metric_and_disposition_forms_coexist_on_same_page(tmp_path):
    app, c = setup(tmp_path)
    # 先对指标做人工处置（公开资料不足），保留使用限制
    assert submit(c, target_type='metric', target_id='m1_2023').status_code == 200
    html = c.get('/?task_id=t').text
    # 两组表单必须在同一页，不能互相覆盖
    assert 'data-operation="metric"' in html
    assert 'data-operation="disposition"' in html
    assert 'data-operation="review"' in html
    assert 'name="c_value"' in html
    assert 'name="resolution_evidence_ids"' in html
    assert 'data-audit-events' in html

    # 指标数值修正：只走指标接口，不改变人工处置流程
    response = metric_request(c, 'm1_2023', 1, 'correct', {
        'status': 'extracted', 'value': '100', 'raw_value': '100',
        'raw_unit': 'tCO2e', 'canonical_unit': 'tCO2e',
        'conversion_rule': None, 'denominator': None,
        'extraction_note': '补充数值来源，边界仍待企业确认',
    })
    assert response.status_code == 200, response.text
    new_id = response.json()['data']['record']['record_id']

    html = c.get('/?task_id=t').text
    # 后继记录的处置状态必须显示 needs_recheck，旧限制仍保留
    assert '原处置已失效，限制仍保留，需重新核查' in html
    assert '不得将反推比值' in html
    assert f'data-draft="metric-{new_id}"' in html
    assert 'data-draft="metric-m1_2023"' not in html
    s = state(app)
    assert any(r.get('needs_recheck') for r in s.get('review_dispositions', [])) or \
        '原处置已失效，限制仍保留，需重新核查' in html


def test_limited_completion_is_a_terminal_status_not_fact_confirmation(tmp_path):
    app, c = setup(tmp_path)
    run = app.state.runs.start('t', 'offline-session')
    app.state.runs.complete(run.run_id, '离线结束', 1, with_limitations=True)
    html = c.get('/?task_id=t').text
    assert '核查流程已完成' in html
    assert '存在使用限制' in html
    assert '不能当作数据已全部确认' in html
    assert 'Task 7已验收' not in html or '不能当作数据已全部确认或Task 7已验收' in html
