import test from 'node:test';import assert from 'node:assert/strict';
import {optimize,reconcile} from '../web/scheduler.js';
const base=Date.parse('2026-11-18T00:00:00Z');
const series=[500,10,400,10,200,300].map((carbon,i)=>({timestamp:new Date(base+i*3600000).toISOString().replace('.000Z','Z'),carbon,high:carbon+10,price:i===1?-.1:.1}));
const job={duration_h:2,energy_kWh:10,earliest_start:'2026-11-18T00:00:00Z',deadline:'2026-11-18T06:00:00Z',allowed_regions:['DE']};
test('continuous and interruptible windows match Python reference',()=>{assert.deepEqual(optimize({DE:series},job).slots,[series[3].timestamp,series[4].timestamp]);const split=optimize({DE:series},{...job,interruptible:true});assert.deepEqual(split.slots,[series[1].timestamp,series[3].timestamp]);assert.equal(split.expected_kg,.1);});
test('deadline and negative price',()=>{const p=optimize({DE:series},{...job,duration_h:1,objective:'cost',deadline:'2026-11-18T02:00:00Z'});assert.equal(p.start,series[1].timestamp);assert.equal(p.expected_cost_eur,-1);});
test('cannot claim savings from plans or absent measurements',()=>{const p=optimize({DE:series},job);assert.equal(reconcile(p,{DE:series}).verifiedSavingsKg,null);assert.equal(reconcile(p,{},true).status,'awaiting-measurements');assert.ok(Math.abs(reconcile(p,{DE:series},true).verifiedSavingsKg-1.5)<1e-9);});
test('missing prices and broken continuous windows are infeasible',()=>{assert.throws(()=>optimize({DE:series.map(r=>({...r,price:null}))},{...job,objective:'cost'}));assert.throws(()=>optimize({DE:[series[0],series[2]]},job));});
