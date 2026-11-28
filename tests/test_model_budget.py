"""No model calls. Shared reservations must survive errors, races and restarts."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from esg_app.model_budget import ModelBudget,BudgetBlocked


def test_zero_default_and_shared_checkpoint(tmp_path):
    p=tmp_path/'budget.sqlite3'
    with pytest.raises(BudgetBlocked):ModelBudget(p).reserve('sdk',1)
    b=ModelBudget(p,cap_micro=10_000_000)
    sdk=b.reserve('sdk',3_000_000)
    extractor=b.reserve('extractor',2_000_000)
    with pytest.raises(BudgetBlocked):b.reserve('sdk',1)
    b.settle(sdk,1_000_000)
    assert ModelBudget(p,cap_micro=10_000_000).snapshot()['reserved_micro']==2_000_000
    # Network failures or missing usage keep the extractor's reservation charged against admission.
    assert b.snapshot()['charged_micro']==1_000_000
    b.settle(sdk,1_000_000)
    with pytest.raises(ValueError):b.settle(sdk,2_000_000)


def test_atomic_concurrent_admission(tmp_path):
    b=ModelBudget(tmp_path/'budget.sqlite3',cap_micro=10_000_000)
    def attempt(i):
        try:return b.reserve('sdk'if i%2 else'extractor',1_000_000)
        except BudgetBlocked:return None
    with ThreadPoolExecutor(max_workers=8)as pool:results=list(pool.map(attempt,range(12)))
    assert sum(x is not None for x in results)==5
    assert b.snapshot()['reserved_micro']==5_000_000


def test_unknown_usage_never_releases_reservation(tmp_path):
    b=ModelBudget(tmp_path/'budget.sqlite3',cap_micro=10_000_000)
    rid=b.reserve('sdk',5_000_000)
    with pytest.raises(BudgetBlocked):b.settle(rid,5_000_001)
    assert b.snapshot()['charged_micro']==5_000_001
    with pytest.raises(BudgetBlocked):b.reserve('extractor',1)
    with pytest.raises(ValueError):b.reserve('sdk',True)


def test_host_pauses_original_session_on_budget_block(tmp_path,monkeypatch):
    from test_review_dispositions import setup
    from esg_app.agent_host import make_agent_starter
    from esg_app.run_jobs import RunStatus
    monkeypatch.setenv('DEEPSEEK_API_KEY','offline-key')
    app,_=setup(tmp_path);run=app.state.runs.start('t','same_original_session')
    def blocked(*args,**kwargs):raise BudgetBlocked('checkpoint')
    make_agent_starter(app,blocked)('t',run.run_id,'resume','')
    paused=app.state.runs.get(run.run_id)
    assert paused.status==RunStatus.WAITING_BUDGET and paused.session_id=='same_original_session'
    with app.state.tasks.transaction()as db:
        assert db.execute('SELECT COUNT(*) FROM agent_run_claims').fetchone()[0]==0
