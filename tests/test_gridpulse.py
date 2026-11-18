import json
from pathlib import Path
from datetime import datetime,timezone,timedelta
import pytest
from gridpulse.ingest.energy_charts import parse_power,parse_prices
from gridpulse.ingest.eia930 import parse as parse_eia
from gridpulse.ingest.weather import parse as parse_weather
from gridpulse.ingest.smard import parse_series
from gridpulse.ingest.validate import validate
from gridpulse.ingest.common import iso,utc,record
from gridpulse.carbon.intensity import hourly
from gridpulse.store.parquet_writer import write,read
from gridpulse.forecast.baselines import forecast,backtest
from gridpulse.scheduler.optimizer import optimize
from gridpulse.savings.ledger import reconcile
from gridpulse.serve.mcp_server import handle
FIXTURES=Path(__file__).parent/'fixtures'
NOW=datetime(2026,11,18,tzinfo=timezone.utc)


def fixture(name):return json.loads((FIXTURES/name).read_text())
def slots(values,prices=None):
    return [dict(timestamp=iso(NOW+timedelta(hours=i)),carbon=v,low=max(0,v-10),high=v+10,renewable=50,price=prices[i] if prices else None) for i,v in enumerate(values)]
def job(**kwargs):
    return dict(duration_h=2,energy_kWh=10,earliest_start=iso(NOW),deadline=iso(NOW+timedelta(hours=6)),allowed_regions=['DE'],**kwargs)


def test_recorded_germany_contract_and_price_units():
    rows=parse_power(fixture('energy_charts.json'),NOW)+parse_prices(fixture('price.json'),NOW)
    validate(rows)
    hours=hourly(rows)
    assert len(hours)==2
    assert 0<hours[0]['carbon']<1200
    assert 0<=hours[0]['renewable']<=100
    assert hours[0]['price']==pytest.approx(sum(fixture('price.json')['price'][:4])/4000)


def test_recorded_eia_hour_ending_and_geothermal():
    rows=parse_eia(fixture('eia.json'),NOW);validate(rows)
    assert rows[0]['timestamp_utc']=='2026-11-17T23:00:00Z'
    assert any(r['fuel']=='geothermal' for r in rows)
    assert len(hourly(rows))==1


def test_carbon_known_weighted_mix():
    rows=[record(NOW,'DE','generation','coal',100,'MW','test',NOW),record(NOW,'DE','generation','wind',100,'MW','test',NOW)]
    result=hourly(rows)[0]
    assert result['carbon']==415.5
    assert result['renewable']==50


def test_contract_rejects_duplicates_gaps_and_nonfinite():
    row=record(NOW,'DE','generation','gas',10,'MW','test',NOW)
    with pytest.raises(ValueError,match='Duplicate'):validate([row,row])
    with pytest.raises(ValueError,match='gap'):validate([row,dict(row,timestamp_utc=iso(NOW+timedelta(hours=4)))])
    with pytest.raises(ValueError,match='Nonfinite'):validate([dict(row,value=float('nan'))])


def test_partial_hour_and_missing_fuel_omitted():
    rows=parse_power(fixture('energy_charts.json'),NOW)
    first=rows[0]
    rows=[r for r in rows if not(r['timestamp_utc']==first['timestamp_utc'] and r['fuel']==first['fuel'])]
    assert len(hourly(rows))==1


def test_store_merge_and_partition_roundtrip(tmp_path):
    rows=parse_power(fixture('energy_charts.json'),NOW)
    write(rows,tmp_path);write(rows,tmp_path)
    loaded=read(tmp_path)
    assert len(loaded)==len(rows)
    assert isinstance(loaded[0]['timestamp_utc'],str)
    assert len(list(tmp_path.rglob('*.parquet')))==1


def test_exact_continuous_and_interruptible():
    data={'DE':slots([500,10,400,10,200,300])}
    continuous=optimize(data,job())
    assert continuous['slots']==[data['DE'][3]['timestamp'],data['DE'][4]['timestamp']]
    split=optimize(data,job(interruptible=True))
    assert split['slots']==[data['DE'][1]['timestamp'],data['DE'][3]['timestamp']]
    assert split['expected_kg']==.1


def test_deadlines_gaps_unknown_prices_and_negative_prices():
    data={'DE':slots([100,50,5],[.1,-.1,.2])}
    p=optimize(data,dict(job(objective='cost'),duration_h=1,deadline=iso(NOW+timedelta(hours=2))))
    assert p['start']==data['DE'][1]['timestamp']
    assert p['expected_cost_eur']==-1
    with pytest.raises(ValueError):optimize({'DE':slots([10,20])},job(objective='cost'))
    with pytest.raises(ValueError):optimize({'DE':[data['DE'][0],data['DE'][2]]},job())


def test_energy_duration_and_risk_constraints():
    with pytest.raises(ValueError):optimize({'DE':slots([10,20])},dict(job(),duration_h=1.5))
    with pytest.raises(ValueError):optimize({'DE':slots([10,20])},dict(job(),power_kW=99))
    series=slots([10,20]);series[0]['high']=1000
    result=optimize({'DE':series},dict(job(risk_averse=True),duration_h=1))
    assert result['start']==series[1]['timestamp']


def test_ledger_requires_execution_and_complete_measurements():
    data={'DE':slots([500,400,100,50])};p=optimize(data,job())
    assert reconcile(p,data)['verifiedSavingsKg'] is None
    assert reconcile(p,{},True)['status']=='awaiting-measurements'
    verified=reconcile(p,data,True)
    assert verified['verifiedSavingsKg']==pytest.approx(3.75)
    # An actually dirtier chosen interval must report a negative result.
    measured={'DE':[dict(r,carbon=1000 if r['timestamp'] in p['slots'] else 1) for r in data['DE']]}
    assert reconcile(p,measured,True)['verifiedSavingsKg']<0


def test_forecast_no_future_leakage_and_baseline_scorecard():
    history=[]
    for i in range(240):
        t=NOW-timedelta(hours=240-i)
        history.append(dict(timestamp=iso(t),carbon=100+t.hour,renewable=50,price=None))
    outlook=forecast(history,NOW)
    assert len(outlook)==48
    assert outlook[0]['carbon']==100
    assert outlook[0]['price'] is None
    poison=history+[dict(timestamp=iso(NOW+timedelta(hours=1)),carbon=9999,renewable=0,price=None)]
    assert forecast(poison,NOW)==outlook
    score=backtest(history)
    assert score['improvementClaim'] is False
    assert all(row['mae']==0 for row in score['horizons'])


def test_utc_dst_and_adapter_boundaries():
    assert utc('2026-10-25T02:00:00+02:00')!=utc('2026-10-25T02:00:00+01:00')
    with pytest.raises(ValueError):utc('2026-10-25T02:00:00')
    assert parse_series({'series':[[1794960000000,10]]},'gas')[0]['value']==10
    payload={'hourly':{'time':['2026-11-18T00:00'],'temperature_2m':[5],'wind_speed_100m':[20],'shortwave_radiation':[0]}}
    assert len(parse_weather(payload,'DE',NOW))==3


def test_mcp_protocol_initialize_tools_and_errors():
    reply=handle({'jsonrpc':'2.0','id':1,'method':'initialize'},lambda:None)
    assert reply['result']['serverInfo']['name']=='gridpulse'
    assert len(handle({'id':2,'method':'tools/list'},lambda:None)['result']['tools'])==5
    assert handle({'method':'notifications/initialized'},lambda:None) is None
    assert handle({'id':3,'method':'unknown'},lambda:None)['error']['code']==-32601
