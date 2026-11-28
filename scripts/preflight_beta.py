"""Read-only deployment preflight; no network calls and no secret values in output."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from urllib.parse import urlsplit


def check(env_path):
    path=Path(env_path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Environment file must be private (chmod 600)')
    env={}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        key,sep,value=line.partition('=')
        if not sep:raise ValueError('Invalid environment file')
        value=value.strip()
        if key.strip()=='ESG_REVIEWER_PASSWORD_HASH' and not (len(value)>1 and value[0]==value[-1]=="'"):
            raise ValueError('Password hash must be a single-quoted literal in the environment file')
        if len(value)>1 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
        env[key.strip()]=value
    needed=['ESG_PUBLIC_HOST','ESG_PROXY_SECRET','ESG_REVIEWER_USER','ESG_REVIEWER_PASSWORD_HASH','ESG_BETA_DATA_DIR']
    if any(not env.get(k) for k in needed):raise ValueError('Deployment settings are incomplete')
    origin=urlsplit('https://'+env['ESG_PUBLIC_HOST'])
    if not origin.hostname or origin.path or origin.query or origin.fragment or origin.username or origin.password:
        raise ValueError('Invalid public hostname')
    if len(env['ESG_PROXY_SECRET'])<32:raise ValueError('Proxy secret must have at least 32 characters')
    if not re.fullmatch(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}',env['ESG_REVIEWER_PASSWORD_HASH']):
        raise ValueError('Gateway requires a bcrypt password hash')
    if any(env.get(k) for k in ['DEEPSEEK_API_KEY','DSH_API_KEY']):raise ValueError('Beta deployment must not contain model credentials')
    directory=Path(env['ESG_BETA_DATA_DIR']).resolve()
    manifest=json.loads((directory/'manifest.json').read_text())
    if manifest['owner']!=env['ESG_REVIEWER_USER']:raise ValueError('Gateway account must match the prepared task owner')
    database=(directory/'business.sqlite3').as_uri()+'?mode=ro'
    with sqlite3.connect(database,uri=True)as db:
        tasks=[json.loads(x[0])for x in db.execute('SELECT results FROM tasks')]
        if not tasks or any(t.get('owner')!=env['ESG_REVIEWER_USER'] for t in tasks):raise ValueError('Task ownership mismatch')
        for rid,raw in db.execute('SELECT report_id,path FROM reports'):
            local=directory/Path(raw).name
            if not local.is_file() or directory not in local.resolve().parents:raise ValueError('Missing or unsafe report file')
        for task_id,raw in db.execute('SELECT task_id,data FROM business_state'):
            source=json.loads(raw).get('source_import_observations',[])
            years={r['evidence_id']:int(r['year']) for r in source if re.fullmatch(r'20[0-9]{2}',r.get('year',''))}
            for (evidence,) in db.execute("SELECT evidence FROM task_parses WHERE task_id=? AND version='reference-package-v4'",(task_id,)):
                if any(e['evidence_id'] in years and e.get('column_year')!=years[e['evidence_id']]
                       for e in json.loads(evidence)):
                    raise ValueError('V4 evidence year is missing or inconsistent; prepare a new business copy before deployment')
    # Check the interpolation environment, not serialized config (which escapes $ again).
    # Shell variables can override --env-file; never print captured environment or stderr.
    compose=Path(__file__).resolve().parents[1]/'deploy'/'compose.yaml'
    try:
        result=subprocess.run(['docker','compose','--env-file',str(path.resolve()),'-f',str(compose),
                               'config','--environment'],capture_output=True,text=True,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        raise ValueError('Compose environment validation failed; check the local Compose installation') from None
    if result.returncode:
        raise ValueError('Compose environment validation failed; check deployment configuration privately')
    effective=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
    if any(effective.get(key)!=env[key] for key in needed):
        raise ValueError('Compose environment differs from the checked file; remove shell overrides or interpolation')
    return dict(status='configuration_ready',tasks=len(tasks),model_budget=0,
                remaining=['Container build and gateway validation','Real DNS/TLS and login smoke test'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',required=True)
    args=parser.parse_args()
    try:print(json.dumps(check(args.env_file),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,sqlite3.Error)as exc:parser.exit(2,f'Preflight failed: {exc}\n')
