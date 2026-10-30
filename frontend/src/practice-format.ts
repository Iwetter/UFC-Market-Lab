export function practiceReason(code:string):string{
 const reasons:Record<string,string>={
  net_profit_target_not_reached:'Executable sale proceeds do not yet cover entry cost, exit costs and the configured net profit target.',
  partial_profit_step_not_met:'Waiting for an additional net price improvement after the last partial sale.',
  order_pending:'Waiting for the previous order to reach the simulated market.',
  cooldown:'Waiting between trades to avoid unnecessary churn.',
  practice_run_order_cap:'The practice session has reached its order limit.',
  no_net_edge_after_costs_and_buffer:'The price does not offer enough advantage after fees, slippage and the uncertainty buffer.',
  net_execution_edge_exceeds_policy_threshold:'The executable price offers enough advantage after estimated costs and the policy buffer.',
  replay_finished_recorded_result_revealed:'Replay complete. The historical fight result is now revealed.',
  ioc_unfilled_remainder:'Available depth or the arrival price prevented a complete fill.',
  round_blackout:'Waiting through the simulated end-of-round blackout.',
  kill_switch:'The practice risk stop is active.',
  daily_spend_cap:'The simulated daily spending limit has been reached.',
  model_not_approved:'This account is blocked by its model configuration.',
  net_profit_target_reached:'The executable sale clears the profit target after entry and exit fees. Taking profit can sell while the model still favors this fighter.',
  holding_edge_below_policy_threshold:'The executable net sale value or reduced holding edge meets the exit policy.',
  holding_position_scale_in_disabled:'Holding the position until an exit condition. Adding contracts is disabled.',
  reentry_pullback_not_met:'Waiting for the executable entry price to fall below the last actual sale by the configured pullback.',
  reentry_pullback_and_current_net_edge:'The price has pulled back from the last actual sale and again clears the entry edge after costs.',
  scale_in_pullback_not_met:'Waiting for a separate pullback below the last actual entry before adding contracts.',
  scale_in_pullback_and_current_net_edge:'Adding within the position cap after a pullback and a fresh entry edge after costs.',
  current_net_edge_exceeds_policy_threshold:'The executable entry clears the required edge after fees, slippage and the probability buffer.',
  position_contract_cap:'Holding the maximum permitted contracts. An exit can still reduce the position.',
  daily_market_order_cap:'The market has reached its entry order cap. Reducing exits remain available.',
 };
 return reasons[code]||code.replaceAll('_',' ');
}

export function practiceFillMarkers(fills:Record<string,any>[],virtualTimeMs:number,elapsedS:number){
 const startMs=Number(virtualTimeMs)-Number(elapsedS)*1000;
 if(!Number.isFinite(startMs))return [];
 return fills.flatMap((fill,index)=>{
  const price=Number(fill.price),count=Number(fill.count),stamp=Number(fill.ts_ms),action=String(fill.action).toLowerCase();
  if(fill.price==null||fill.ts_ms==null||!Number.isFinite(price)||price<=0||price>=1||!Number.isFinite(count)||count<=0||!Number.isFinite(stamp)||!['buy','sell'].includes(action)||!['yes','no'].includes(fill.outcome)||stamp<startMs)return [];
  return [{id:String(fill.id??index),price,yesPrice:fill.outcome==='no'?1-price:price,elapsedS:(stamp-startMs)/1000,count,action,outcome:String(fill.outcome)}];
 });
}

export function replayClock(seconds:number):string{
 const safe=Math.max(0,Math.floor(seconds||0));
 return `${Math.floor(safe/60)}:${String(safe%60).padStart(2,'0')}`;
}
