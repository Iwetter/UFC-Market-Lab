import {describe,it,expect} from 'vitest';
import {practiceReason,replayClock} from './practice-format';

describe('practice explanations',()=>{
 it('explains idle decisions and unfilled orders in user language',()=>{
  expect(practiceReason('no_net_edge_after_costs_and_buffer')).toContain('after fees');
  expect(practiceReason('ioc_unfilled_remainder')).toContain('complete fill');
  expect(practiceReason('new_safety_reason')).toBe('new safety reason');
 });
 it('formats virtual time without negative or fractional seconds',()=>{
  expect(replayClock(65.9)).toBe('1:05');expect(replayClock(-4)).toBe('0:00');
 });
});
