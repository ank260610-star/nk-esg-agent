"""Deployment preflight rejects interpolation damage without exposing credentials."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from esg_app.config import Settings
from esg_app.main import create_app


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    spec=importlib.util.spec_from_file_location('beta_preflight', 'scripts/preflight_beta.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    app=create_app(Settings(database_path=str(tmp_path/'business.sqlite3')))
    app.state.tasks.register_task('synthetic', 0, {'owner':'alice'})
    app.state.tasks.db.close()
    (tmp_path/'manifest.json').write_text(json.dumps({'owner':'alice'}))
    values=dict(ESG_PUBLIC_HOST='beta.example.test', ESG_PROXY_SECRET='synthetic-proxy-secret-of-32-characters',
                ESG_REVIEWER_USER='alice', ESG_REVIEWER_PASSWORD_HASH='$2b$12$'+'a'*53,
                ESG_BETA_DATA_DIR=str(tmp_path))
    path=tmp_path/'beta.env'
    def write(quote="'"):
        path.write_text('\n'.join(k+'='+(quote+v+quote if k=='ESG_REVIEWER_PASSWORD_HASH' else v)
                                  for k,v in values.items())+'\n')
        path.chmod(0o600)
    write()
    return module,path,values,write


@pytest.mark.parametrize('quote',['','"'])
def test_hash_requires_single_quoted_literal(deployment,quote):
    module,path,_,write=deployment;write(quote)
    with pytest.raises(ValueError,match='single-quoted'):
        module.check(path)


def test_compose_effective_environment_is_checked_without_daemon(deployment,monkeypatch):
    module,path,values,_=deployment
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0,stdout='\n'.join(k+'='+v for k,v in values.items()),stderr='')
    monkeypatch.setattr(module.subprocess,'run',run)
    result=module.check(path)
    assert result['status']=='configuration_ready'
    assert calls[0][-2:]==['config','--environment']
    assert str(path.resolve()) in calls[0]
    assert not any(secret in json.dumps(result) for secret in [values['ESG_PROXY_SECRET'],values['ESG_REVIEWER_PASSWORD_HASH']])


@pytest.mark.parametrize('key',['ESG_REVIEWER_PASSWORD_HASH','ESG_PROXY_SECRET','ESG_PUBLIC_HOST','ESG_REVIEWER_USER','ESG_BETA_DATA_DIR'])
def test_shell_override_or_interpolation_mismatch_rejected(deployment,monkeypatch,key):
    module,path,values,_=deployment
    effective=dict(values,**{key:'synthetic-mismatch'})
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0,
        stdout='\n'.join(k+'='+v for k,v in effective.items()),stderr=''))
    with pytest.raises(ValueError,match='Compose environment differs')as exc:
        module.check(path)
    assert values['ESG_REVIEWER_PASSWORD_HASH'] not in str(exc.value)


def test_compose_failure_does_not_echo_environment(deployment,monkeypatch):
    module,path,values,_=deployment
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1,stdout='',
        stderr=values['ESG_REVIEWER_PASSWORD_HASH']))
    with pytest.raises(ValueError,match='Compose environment validation failed')as exc:
        module.check(path)
    assert values['ESG_REVIEWER_PASSWORD_HASH'] not in str(exc.value)
