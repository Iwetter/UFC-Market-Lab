import {describe,it,expect} from 'vitest';
import {cents,pct,priceForLimit} from './format';
describe('quotes and order limits',()=>{
 it('keeps missing quotes distinct from zero',()=>{expect(cents(null)).toBe('—');expect(pct(0)).toBe('0.0%');});
 it('rounds YES and NO conservative limits on the YES grid',()=>{
  const b=[{start:'0',end:'1',step:'.01'}];
  expect(priceForLimit('.50',.01,b,'yes')).toBe('0.510000');
  expect(priceForLimit('.505',.01,b,'no')).toBe('0.520000');
  expect(priceForLimit('.50',.01,b,'yes',false)).toBe('0.490000');
 });
});
