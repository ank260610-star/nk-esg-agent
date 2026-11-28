"""No real model requests: uses a new synthetic session and a local fake provider."""
import os,tempfile,json
from pathlib import Path
from unittest.mock import patch
from esg_app.config import Settings
from esg_app import budget_provider
from esg_app.harness_runner import run_agent
from esg_app.model_budget import ModelBudget
import argparse
parser=argparse.ArgumentParser(description='Native SDK budget interception using a synthetic provider only')
parser.add_argument('--dsh-home',type=Path,required=True)
args=parser.parse_args()
requests=[]
def fake(payload,key,timeout):
 requests.append({'model':payload.get('model'),'stream':payload.get('stream')})
 if payload.get('stream'):
  events=[('message_start',{'type':'message_start','message':{'id':'synthetic','type':'message','role':'assistant','model':'deepseek-flash','content':[],'stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}}),
          ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}}),
          ('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'Synthetic budget gateway check.'}}),
          ('content_block_stop',{'type':'content_block_stop','index':0}),
          ('message_delta',{'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':5}}),
          ('message_stop',{'type':'message_stop'})]
  return ''.join('event: '+name+'\ndata: '+json.dumps(data)+'\n\n'for name,data in events).encode(),'text/event-stream'
 return json.dumps({'id':'synthetic','type':'message','role':'assistant','content':[{'type':'text','text':'Synthetic budget gateway check.'}],'stop_reason':'end_turn','model':'deepseek-flash','usage':{'input_tokens':10,'output_tokens':5}}).encode(),'application/json'

original=budget_provider.sdk_gateway
with tempfile.TemporaryDirectory(prefix='esg-budget-sdk-')as d:
 os.environ['ESG_BUDGET_PATH']=str(Path(d)/'budget.sqlite3');os.environ['ESG_BUDGET_CAP_MICRO']='10000000'
 os.environ['DEEPSEEK_API_KEY']='offline-probe-not-a-valid-provider-key';os.environ.pop('DSH_API_KEY',None)
 settings=Settings(dsh_home=str(args.dsh_home.resolve()),workspace=d,model='deepseek-flash')
 with patch.object(budget_provider,'sdk_gateway',lambda key:original(key,transport=fake)):
  result=run_agent(settings,'Return one short synthetic test response. No tools.','budget_probe_'+__import__('uuid').uuid4().hex)
 assert result.finish_reason=='completed' and requests, 'Native SDK interception not verified'
 print(json.dumps({'finish_reason':result.finish_reason,'intercepted_requests':requests,'ledger':ModelBudget(os.environ['ESG_BUDGET_PATH'],cap_micro=10000000).snapshot(),'real_model_calls':0}))
