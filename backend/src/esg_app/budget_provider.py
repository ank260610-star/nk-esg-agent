"""Loopback-only SDK gateway and extractor admission share one conservative CNY ledger."""
import hmac
import json
import os
import secrets
import threading
from contextlib import contextmanager
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .model_budget import ModelBudget, BudgetBlocked

# Official CNY peak/cache-miss rates, checked 2026-11-28. Never assume cache discounts.
FLASH_MODELS = {'deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp'}
INPUT_BOUND = 1_000_000
MAX_OUTPUT = 49_152
PRICE_EXPIRY = date(2026, 12, 5)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def upstream(payload, key, timeout):
    req=Request('https://api.deepseek.com/chat/completions',data=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    with build_opener(NoRedirect()).open(req,timeout=timeout)as response:
        body=response.read(16*1024*1024+1)
        if len(body)>16*1024*1024:raise BudgetBlocked('Response too large; reservation retained')
        return body,response.headers.get('Content-Type','application/json')


def upstream_messages(payload, key, timeout):
    req=Request('https://api.deepseek.com/anthropic/v1/messages',data=json.dumps(payload).encode(),
                headers={'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'},method='POST')
    with build_opener(NoRedirect()).open(req,timeout=timeout)as response:
        body=response.read(16*1024*1024+1)
        if len(body)>16*1024*1024:raise BudgetBlocked('Response too large; reservation retained')
        return body,response.headers.get('Content-Type','application/json')


def configured_budget():
    path=os.environ.get('ESG_BUDGET_PATH','')
    if not path:raise BudgetBlocked('Explicit shared budget database required; no model request sent')
    return ModelBudget(path,cap_micro=int(os.environ.get('ESG_BUDGET_CAP_MICRO','0')),
                       checkpoint_micro=int(os.environ.get('ESG_BUDGET_CHECKPOINT_MICRO','5000000')))


def completion(payload,key,timeout,role, *, budget=None, transport=upstream, protocol="openai"):
    if date.today()>PRICE_EXPIRY:raise BudgetBlocked('Price policy expired; recheck official pricing')
    if payload.get('model')not in FLASH_MODELS:raise BudgetBlocked('Model has no verified CNY policy')
    output=payload.get('max_tokens')
    if type(output)is not int or not 0<output<=MAX_OUTPUT:raise BudgetBlocked('Explicit bounded max_tokens required')
    # Refuse multimodal, non-function hosted tools and alternate API formats in this policy.
    allowed_tool_types = (None, 'custom') if protocol == 'anthropic' else ('function',)
    if any(t.get('type') not in allowed_tool_types for t in payload.get('tools',[])):raise BudgetBlocked('Unpriced hosted tool')
    for message in payload.get('messages',[]):
        content = message.get('content')
        if content is not None and not isinstance(content,str):
            if protocol != 'anthropic' or not isinstance(content,list) or any(b.get('type') not in ('text','tool_use','tool_result','thinking','redacted_thinking') for b in content):
                raise BudgetBlocked('Multimodal pricing unsupported')
            if any(isinstance(b.get('content'),list) and any(x.get('type') != 'text' for x in b['content']) for b in content):
                raise BudgetBlocked('Multimodal tool result unsupported')
    if len(json.dumps(payload,ensure_ascii=False).encode())>400_000:raise BudgetBlocked('Payload exceeds audited prompt limit')
    budget=budget or configured_budget()
    rid=budget.reserve(role,INPUT_BOUND*2+output*8)
    # A timeout or invalid response retains reservation: the provider may already have billed it.
    body,ctype=transport(payload,key,timeout)
    usage=None
    if 'event-stream'in ctype:
        for line in body.splitlines():
            if line.startswith(b'data:')and line[5:].strip()!=b'[DONE]':
                event=json.loads(line[5:])
                if protocol == 'anthropic':
                    usage = {**(usage or {}), **event.get('message',{}).get('usage',{}), **event.get('usage',{})}
                else:usage=event.get('usage')or usage
    else:usage=json.loads(body).get('usage')
    if isinstance(usage,dict):
        if protocol == 'anthropic':
            values=[usage.get('input_tokens'), usage.get('cache_creation_input_tokens',0), usage.get('cache_read_input_tokens',0)]
            inp=sum(values) if all(type(v)is int and v>=0 for v in values) else None
            out=usage.get('output_tokens')
        else:inp,out=usage.get('prompt_tokens'),usage.get('completion_tokens')
        if type(inp)is int and type(out)is int and 0<=inp<=INPUT_BOUND and 0<=out<=output:
            budget.settle(rid,inp*2+out*8)
        else:
            budget.pause()
            raise BudgetBlocked('Usage outside reserved bounds; request held for reconciliation')
    return body,ctype


@contextmanager
def sdk_gateway(key, *, transport=upstream):
    budget=configured_budget();token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            messages = self.path in ('/messages','/v1/messages','/anthropic/v1/messages')
            authenticated = hmac.compare_digest(self.headers.get('Authorization',''), 'Bearer '+token) or hmac.compare_digest(self.headers.get('x-api-key',''),token)
            if not (messages or self.path in ('/chat/completions','/v1/chat/completions')) or not authenticated:
                self.send_error(403);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=400_000:raise BudgetBlocked('Invalid request size')
                payload=json.loads(self.rfile.read(length))
                if payload.get('stream') and not messages:
                    payload['stream_options']={'include_usage':True}
                forward = upstream_messages if messages and transport is upstream else transport
                body,ctype=completion(payload,key,600,'sdk',budget=budget,transport=forward,protocol='anthropic' if messages else 'openai')
                self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except Exception as exc:
                if isinstance(exc,BudgetBlocked):
                    self.server.budget_blocked = True
                body=json.dumps({'error':{'message':'Budget/provider request rejected; inspect shared ledger before retry','type':'budget_guard'}}).encode()
                self.send_response(429);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    server.budget_blocked=False
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',token
    finally:
        server.shutdown();server.server_close();thread.join(2)
        if server.budget_blocked:
            raise BudgetBlocked('Shared request budget blocked this SDK turn')
