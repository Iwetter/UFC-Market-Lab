import {describe,it,expect} from 'vitest';
import {practiceReason,replayClock,practiceFillMarkers} from './practice-format';

describe('practice explanations',()=>{
 it('explains idle decisions and unfilled orders in user language',()=>{
  expect(practiceReason('no_net_edge_after_costs_and_buffer')).toContain('after fees');
  expect(practiceReason('ioc_unfilled_remainder')).toContain('complete fill');
  expect(practiceReason('new_safety_reason')).toBe('new safety reason');
 });
 it('formats virtual time without negative or fractional seconds',()=>{
  expect(replayClock(65.9)).toBe('1:05');expect(replayClock(-4)).toBe('0:00');
 });
 it('explains profit taking and the actual pullback required before re-entry',()=>{
  expect(practiceReason('net_profit_target_reached')).toContain('entry and exit fees');
  expect(practiceReason('reentry_pullback_not_met')).toContain('last actual sale');
  expect(practiceReason('scale_in_pullback_not_met')).toContain('last actual entry');
 });
 it('plots execution price and time, including NO sales on the YES axis',()=>{
  const points=practiceFillMarkers([{id:'buy',action:'buy',outcome:'yes',count:'2',price:'.4',ts_ms:101500},{id:'sell',action:'sell',outcome:'no',count:'1',price:'.7',ts_ms:115250}],130000,30);
  expect(points.map(p=>p.elapsedS)).toEqual([1.5,15.25]);
  expect(points[0].yesPrice).toBe(.4);expect(points[1].yesPrice).toBeCloseTo(.3);
  expect(points[1]).toMatchObject({action:'sell',count:1,price:.7});
 });
 it('does not draw rejected order intents, invalid fills or fills from before the run',()=>{
  expect(practiceFillMarkers([{order_id:'rejected',action:'BUY',yes_bid:'.4',elapsed_s:15},{action:'buy',outcome:'yes',count:'0',price:'.4',ts_ms:110000},{action:'buy',outcome:'yes',count:'1',price:'.4',ts_ms:90000}],130000,30)).toEqual([]);
  expect(practiceFillMarkers([],NaN,0)).toEqual([]);
 });
});
