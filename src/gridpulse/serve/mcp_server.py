"""Minimal MCP JSON-RPC stdio server; stdout is protocol-only."""
import json
import sys
from urllib.request import urlopen
from datetime import datetime,timezone
from ..sdk import DEFAULT_URL
from ..scheduler.optimizer import optimize
TOOLS=[{'name':'get_grid_now','description':'Get measured average grid intensity with attribution.',
        'inputSchema':{'type':'object','properties':{'region':{'type':'string'}},'required':['region']}},
       {'name':'forecast','description':'Get up to 48 hours of baseline forecasts and empirical uncertainty.',
        'inputSchema':{'type':'object','properties':{'region':{'type':'string'},'hours':{'type':'integer','minimum':1,'maximum':48}},'required':['region']}},
       {'name':'schedule_job','description':'Find a feasible carbon/cost window; does not execute anything.',
        'inputSchema':{'type':'object','properties':{'job':{'type':'object','properties':{'duration_h':{'type':'integer','minimum':1,'maximum':48},'energy_kWh':{'type':'number','exclusiveMinimum':0},'power_kW':{'type':'number','exclusiveMinimum':0},'deadline':{'type':'string','format':'date-time'},'earliest_start':{'type':'string','format':'date-time'},'allowed_regions':{'type':'array','minItems':1,'items':{'type':'string','enum':['DE','US-CAISO','US-ERCOT','US-PJM']}},'objective':{'type':'string','enum':['co2','cost','balanced']},'interruptible':{'type':'boolean'},'risk_averse':{'type':'boolean'}},'required':['duration_h','deadline','allowed_regions'],'anyOf':[{'required':['energy_kWh']},{'required':['power_kW']}]}},'required':['job']}}]

TOOLS.extend([
    {'name':'get_flex_signal','description':'Get heuristic GridFlex proxy levels, inputs and limits; not official alerts.',
     'inputSchema':{'type':'object','properties':{'region':{'type':'string'}},'required':['region']}},
    {'name':'simulate_fleet','description':'Replay an illustrative GPU fleet against measured grid history. Does not control infrastructure.',
     'inputSchema':{'type':'object','properties':{'region':{'type':'string'},'gpus':{'type':'integer','minimum':1,'maximum':10000},
       'hours':{'type':'integer','minimum':1,'maximum':336},'mode':{'type':'string','enum':['hybrid','power-cap','checkpoint']}},'required':['region']}}
])


def call(name,args,data):
    now=datetime.now(timezone.utc)
    if (now-datetime.fromisoformat(data['generatedAt'].replace('Z','+00:00'))).total_seconds()>21600:
        raise ValueError('Stale snapshot; fetch a fresh source before scheduling')
    regions={r['id']:r for r in data['regions'] if r['status']=='available' and (now-datetime.fromisoformat(r['now']['timestamp'].replace('Z','+00:00'))).total_seconds()<=24*3600}
    if name in ('get_flex_signal','simulate_fleet'):
        key=args['region']; signals=data['gridflex']['regions'][key]
        if name=='get_flex_signal':
            return {'region':key,'now':signals['now'],'forecast':signals['forecast'],'limits':signals['limits'],'attribution':data['attribution']}
        from ..gridflex.simulator import simulate,Fleet
        hours=args.get('hours',168)
        if type(hours) is not int or not 1<=hours<=min(336,len(signals['history'])): raise ValueError('Invalid or unavailable replay horizon')
        result=simulate(signals['history'][-hours:],Fleet(gpus=args.get('gpus',1000),mode=args.get('mode','hybrid')))
        for replay in ('baseline','controlled'): result[replay].pop('trace',None)
        result.pop('gridInputs',None);result['region']=key;result['attribution']=data['attribution']
        return result
    if name=='schedule_job':
        job=args['job'];job.setdefault('earliest_start',now.isoformat())
        return optimize({key:r['forecast'] for key,r in regions.items()},job)
    region=regions[args['region']]
    if name=='get_grid_now':
        return {'now':region['now'],'attribution':region['attribution'],'basis':data['basis']}
    if name=='forecast':
        hours=args.get('hours',48)
        if not isinstance(hours,int) or not 1<=hours<=48:
            raise ValueError('hours must be between 1 and 48')
        return {'forecast':region['forecast'][:hours],'attribution':region['attribution'],'model':region['scorecard']}
    raise ValueError('Unknown tool')


def handle(request,loader):
    ident=request.get('id');method=request.get('method')
    if ident is None:return None
    if method=='initialize':
        result={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'gridpulse','version':'0.2.0'}}
    elif method=='tools/list':result={'tools':TOOLS}
    elif method=='ping':result={}
    elif method=='tools/call':
        try:
            params=request['params'];value=call(params['name'],params.get('arguments',{}),loader())
            result={'content':[{'type':'text','text':json.dumps(value)}]}
        except Exception as exc:
            result={'isError':True,'content':[{'type':'text','text':str(exc)}]}
    else:return {'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Method not found'}}
    return {'jsonrpc':'2.0','id':ident,'result':result}


def main():
    def loader():
        with urlopen(DEFAULT_URL,timeout=30) as response:return json.load(response)
    for line in sys.stdin:
        try:
            response=handle(json.loads(line),loader)
            if response is not None:print(json.dumps(response),flush=True)
        except (ValueError,KeyError,TypeError):
            print(json.dumps({'jsonrpc':'2.0','id':None,'error':{'code':-32700,'message':'Invalid JSON-RPC request'}}),flush=True)


if __name__=='__main__':main()
