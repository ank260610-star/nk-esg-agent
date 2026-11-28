import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from esg_app.budget_provider import completion,sdk_gateway
from esg_app.model_budget import ModelBudget,BudgetBlocked


def payload(stream=False):return dict(model='deepseek-flash',messages=[{'role':'user','content':'synthetic'}],max_tokens=100,stream=stream)


def test_shared_roles_settle_and_default_reject(tmp_path,monkeypatch):
    monkeypatch.setenv('ESG_BUDGET_PATH',str(tmp_path/'cost.sqlite3'))
    called=[]
    def provider(p,key,t):
        called.append(p);return json.dumps({'usage':{'prompt_tokens':100,'completion_tokens':10},'choices':[]}).encode(),'application/json'
    with pytest.raises(BudgetBlocked):completion(payload(),'fake-key',1,'extractor',transport=provider)
    assert not called
    monkeypatch.setenv('ESG_BUDGET_CAP_MICRO','10000000')
    completion(payload(),'fake-key',1,'extractor',transport=provider)
    with sdk_gateway('fake-key',transport=provider)as(url,token):
        req=Request(url+'/chat/completions',data=json.dumps(payload()).encode(),headers={'Authorization':'Bearer '+token})
        assert urlopen(req).status==200
    assert len(called)==2
    assert ModelBudget(tmp_path/'cost.sqlite3').snapshot()['charged_micro']==560


def test_timeout_holds_reservation(tmp_path):
    b=ModelBudget(tmp_path/'cost.sqlite3',cap_micro=10000000)
    def fail(*args):raise TimeoutError()
    with pytest.raises(TimeoutError):completion(payload(),'fake',1,'sdk',budget=b,transport=fail)
    assert b.snapshot()['reserved_micro']==2_000_800


def test_sdk_stream_usage_and_unpriced_model(tmp_path):
    b=ModelBudget(tmp_path/'cost.sqlite3',cap_micro=10000000)
    def stream(*args):return b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5}}\n\ndata: [DONE]\n\n','text/event-stream'
    completion(payload(True),'fake',1,'sdk',budget=b,transport=stream)
    assert b.snapshot()['charged_micro']==60
    wrong=payload();wrong['model']='unpriced'
    with pytest.raises(BudgetBlocked):completion(wrong,'fake',1,'sdk',budget=b,transport=stream)


def test_messages_usage_counts_cached_input_and_delta_output(tmp_path):
    b=ModelBudget(tmp_path/'cost.sqlite3',cap_micro=10000000)
    p=payload(True);p['messages'][0]['content']=[{'type':'text','text':'synthetic'}]
    def stream(*args):
        return (b'data: {"type":"message_start","message":{"usage":{"input_tokens":10,"cache_read_input_tokens":20}}}\n\n'
                b'data: {"type":"message_delta","usage":{"output_tokens":5}}\n\n'),'text/event-stream'
    completion(p,'fake',1,'sdk',budget=b,transport=stream,protocol='anthropic')
    assert b.snapshot()['charged_micro']==100


def test_messages_gateway_auth_and_budget_rejection(tmp_path,monkeypatch):
    monkeypatch.setenv('ESG_BUDGET_PATH',str(tmp_path/'cost.sqlite3'))
    called=[]
    def provider(*args):called.append(True);return b'{}','application/json'
    with pytest.raises(BudgetBlocked), sdk_gateway('fake-key',transport=provider)as(url,token):
        p=payload();p['messages'][0]['content']=[{'type':'text','text':'synthetic'}]
        req=Request(url+'/v1/messages',data=json.dumps(p).encode(),headers={'x-api-key':token})
        with pytest.raises(HTTPError)as error:urlopen(req)
        assert error.value.code==429
    assert not called
