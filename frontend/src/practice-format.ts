export function practiceReason(code:string):string{
 const reasons:Record<string,string>={
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
 };
 return reasons[code]||code.replaceAll('_',' ');
}

export function replayClock(seconds:number):string{
 const safe=Math.max(0,Math.floor(seconds||0));
 return `${Math.floor(safe/60)}:${String(safe%60).padStart(2,'0')}`;
}
