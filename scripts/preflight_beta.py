"""Read-only deployment preflight; no network calls and no secret values in output."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import stat
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
    return dict(status='configuration_ready',tasks=len(tasks),model_budget=0,
                remaining=['Container build and gateway validation','Real DNS/TLS and login smoke test'])


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',required=True)
    args=parser.parse_args()
    try:print(json.dumps(check(args.env_file),ensure_ascii=False,indent=2))
    except (ValueError,OSError,KeyError,sqlite3.Error)as exc:parser.exit(2,f'Preflight failed: {exc}\n')
