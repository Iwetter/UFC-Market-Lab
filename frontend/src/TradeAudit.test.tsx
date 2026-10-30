import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {TradeAudit} from './TradeAudit';
import {PracticeChart} from './PracticePane';

it('renders fill-derived inventory and separates sales from settlement profit',()=>{
 const html=renderToStaticMarkup(<TradeAudit run={{virtual_time_ms:115250,elapsed_s:15.25,fight:{fighter_a:'Alpha',fighter_b:'Beta'},strategy:{realized_trading_pnl:'.19',settlement_pnl:'.53',unrealized_pnl:'0',unrealized_position_value:'0',total_net_pnl:'.72',trade_ledger:[{id:'fill1',ts_ms:115250,outcome:'yes',action:'sell',count:'1',price:'.54',fee:'.02',realized_pnl:'.19',remaining_position:'0'}]},decision_summary:{reentry_pullback_not_met:3}}}/>);
 expect(html).toContain('Realized trading profit');expect(html).toContain('Settlement profit');
 for(const text of ['$0.19','$0.53','$0.72','54.0¢','$0.02','SELL','0:15','Waiting for the executable entry'])expect(html).toContain(text);
});

it('renders markers only from backend fills, not strategy decisions',()=>{
 const html=renderToStaticMarkup(<PracticeChart rows={[{elapsed_s:0,yes_bid:.3,action:'BUY',order_id:'unfilled'}]} probability={.85} fills={[{id:'actual-fill',ts_ms:100250,outcome:'yes',action:'buy',count:'2',price:'.31'}]} virtualTimeMs={115000} elapsedS={15}/>);
 expect(html.match(/<circle /g)).toHaveLength(1);
 expect(html).toContain('BUY 2 YES at 31.0¢');
 expect(html).not.toContain('unfilled');
});
