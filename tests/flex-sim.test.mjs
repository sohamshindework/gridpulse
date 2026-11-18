import test from 'node:test';import assert from 'node:assert/strict';import fs from 'node:fs';
import {simulate} from '../web/flex-sim.js';
const cases=JSON.parse(fs.readFileSync(new URL('./fixtures/flex-replays.json',import.meta.url)));
function close(actual,expected,path=''){if(expected===null||typeof expected==='boolean')return assert.equal(actual,expected,path);if(typeof expected==='number'){assert.ok(Math.abs(actual-expected)<=Math.max(1e-7,Math.abs(expected)*1e-10),`${path}: ${actual} != ${expected}`);return;}for(const [key,value]of Object.entries(expected))close(actual[key],value,path+'.'+key);}
cases.forEach((fixture,index)=>test(`browser replay matches Python: scenario ${index+1} / ${fixture.config.mode}`,()=>{const actual=simulate(fixture.hours,fixture.config);close(actual,fixture.expected);}));
test('reject unknown signals, gaps and oversized fleets',()=>{const trace=cases[0].hours;assert.throws(()=>simulate([{...trace[0],level:null}]));assert.throws(()=>simulate([trace[0],trace[2]]));assert.throws(()=>simulate(trace,{gpus:10001}));});
