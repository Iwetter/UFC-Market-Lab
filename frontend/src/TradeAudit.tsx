import React from 'react';
import {cents,money,pct} from './format';
import {practiceReason,replayClock} from './practice-format';
type Obj=Record<string,any>;

export function TradeAudit({run}:{run:Obj}){
 const s=run.strategy||{},ledger=s.trade_ledger||[],start=run.virtual_time_ms-run.elapsed_s*1000;
 const value=(n:any)=>n==null?'—':money(n);
 return <>
  <div className="practice-stats">{[
   ['Realized trading profit',s.realized_trading_pnl,'Sales minus allocated entry cost and fees'],
   ['Open position value',s.unrealized_position_value,`Estimated net liquidation · unrealized P&L ${value(s.unrealized_pnl)}`],
   ['Settlement profit',s.settlement_pnl,'Winner payout minus remaining cost basis'],
   ['Total net liquidation P&L',s.total_net_pnl,'Trading + settlement + estimated unrealized P&L'],
  ].map(([label,n,note])=><div key={label}><span>{label}</span><strong>{value(n)}</strong><small>{note}</small></div>)}</div>
  <section className="panel"><div className="panel-heading"><h2>Executed trade ledger</h2><span className="tiny-label">ACTUAL PAPER FILLS · FEES INCLUDED</span></div><div className="table-scroll"><table><thead><tr><th>TIME</th><th>CONTRACT</th><th>ACTION</th><th>QUANTITY</th><th>PRICE</th><th>FEES</th><th>REALIZED PROFIT</th><th>REMAINING</th></tr></thead><tbody>{ledger.map((f:Obj,i:number)=><tr key={f.id??i}><td title={String(f.ts_ms)}>{replayClock((f.ts_ms-start)/1000)}</td><td>{f.outcome==='yes'?run.fight.fighter_a:run.fight.fighter_b} · {f.outcome.toUpperCase()}</td><td className={f.action==='buy'?'green':'practice-sell'}>{f.action.toUpperCase()}</td><td>{f.count}</td><td>{cents(f.price)}</td><td>{value(f.fee)}</td><td>{value(f.realized_pnl)}</td><td>{f.remaining_position}</td></tr>)}</tbody></table></div>{!ledger.length&&<p className="practice-explain">No recorded fills in this run.</p>}</section>
  {!!Object.keys(run.decision_summary||{}).length&&<details className="practice-details"><summary>Skipped decisions by reason</summary>{Object.entries(run.decision_summary).map(([reason,count])=><p key={reason}><b>{String(count)}×</b> {practiceReason(reason)}</p>)}</details>}
  <details className="practice-details"><summary>Decision audit · probability, quotes, holdings and eligibility</summary><p>Each row captures information available at that decision. Executions appear separately above.</p><div className="table-scroll"><table><thead><tr><th>TIME</th><th>MODEL / YES BID / ASK</th><th>HOLDINGS / AVERAGE COST</th><th>LIQUIDATION / UNREALIZED</th><th>ENTRY / RE-ENTRY</th><th>EXIT EVALUATION</th><th>DECISION</th></tr></thead><tbody>{(run.history||[]).map((d:Obj,i:number)=><tr key={i}><td title={String(d.virtual_time_ms)}>{replayClock(d.elapsed_s)}</td><td>{pct(d.probability_a)} / {cents(d.yes_bid)} / {cents(d.yes_ask)}</td><td>{d.strategy?.positions?.map((p:Obj)=>`${p.outcome.toUpperCase()} ${p.quantity} @ ${cents(p.avg_entry_cost)}`).join('; ')||'Flat'}</td><td>{value(d.strategy?.unrealized_position_value)} / {value(d.strategy?.unrealized_pnl)}</td><td>{Object.entries(d.strategy?.entry_comparisons||{}).map(([side,v]:[string,any])=><div key={side}>{side.toUpperCase()}: edge {v.net_edge_after_buffer==null?'—':cents(v.net_edge_after_buffer)} · {v.economic_eligibility?'Economic edge met; subject to decision/risk gates':(v.reasons||[]).map(practiceReason).join(' ')}</div>)}</td><td>{Object.entries(d.strategy?.exit_comparisons||{}).map(([side,v]:[string,any])=><div key={side}>{side.toUpperCase()}: net gain {cents(v.estimated_exit_profit_per_contract)} · {v.profit_taking_eligible?'Profit target eligible':(v.reasons||[]).map(practiceReason).join(' ')}</div>)}</td><td>{d.action} · {d.reasons?.map(practiceReason).join(' ')}</td></tr>)}</tbody></table></div></details>
 </>;
}
