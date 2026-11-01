import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {expect,it} from 'vitest';
import {TradeAudit} from './TradeAudit';
import {PracticeChart,ModelTimeline} from './PracticePane';

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

it('plots stepwise trained updates and labels every quote side',()=>{
 const html=renderToStaticMarkup(<PracticeChart rows={[{elapsed_s:0,probability_a:.7,yes_bid:.4,yes_ask:.44,no_bid:.56,no_ask:.6},{elapsed_s:300,probability_a:.4,yes_bid:.45,yes_ask:.49,no_bid:.51,no_ask:.55}]} probability={.4} fills={[]} virtualTimeMs={300000} elapsedS={300}/>);
 expect(html).toContain('35,75 645,75 645,120');
 for(const label of ['Fighter A win probability','Fighter B win probability','Synthetic YES bid','Synthetic yes_ask','Synthetic no_bid','Synthetic no_ask'])expect(html).toContain(label);
 expect(html).not.toContain('Fixed pre-fight');
});

it('renders clickable observed-round timeline with model provenance and no invented event time',()=>{
 const html=renderToStaticMarkup(<ModelTimeline run={{probability_a:.4,fight:{fighter_a:'Alpha',fighter_b:'Beta'},prefight_prediction:{probability_a:.7},model_updates:[{round:1,elapsed_s:300,updated:true,probability_before:.7,probability_after:.4,reason:'Completed round inference',availability_basis:'retrospective boundary',model_version:'trained-hash',features:{round_kd:-1},logit_contributions:{round_kd:-.3}}]}}/>);
 for(const label of ['<button','5:00','Round 1','Completed round inference','trained-hash','retrospective boundary','No verified strike or knockdown timestamps','round_kd'])expect(html).toContain(label);
});
