"""Artificial timing fixtures validate software; none are measured fight data."""
import json
from decimal import Decimal

import pytest

from backend.latency import StudyEvent,QuoteObservation,StudyConfig,analyze_latency,read_journal,latency_stress_scenarios
from backend.store import Store


def event(index=0,**updates):
    base=dict(id=f"e{index}",bout_id=f"bout{index}",card_id=f"card{index}",ticker=f"KXUFCFIGHT-TEST-{index}",event_kind="knockdown",source="licensed-test-fixture",received_ms=100500,
              provider_generated_ms=100200,physical_start_ms=100000,physical_end_ms=100000,physical_time_source="synthetic unit-test occurrence",physical_clock_verified=True,
              mapping_confirmed=True,direction="up",direction_basis="fixture: YES fighter causes knockdown")
    return StudyEvent(**(base|updates))


def quotes(e,delay_ms=12000,step=500,**updates):
    rows=[]
    for t in range(75000,130001,step):
        middle=Decimal('.55') if t>=100000+delay_ms else Decimal('.50')
        rows.append(QuoteObservation(**(dict(id=f"{e.id}:{t}",ticker=e.ticker,received_ms=t,yes_bid=middle-Decimal('.01'),yes_ask=middle+Decimal('.01'),bid_depth='100',ask_depth='100',market_status='active')|updates)))
    return rows


def test_interval_censoring_clocks_and_independent_twelve_second_hypothesis():
    events=[event(i) for i in range(20)]
    report=analyze_latency(events,[q for e in events for q in quotes(e)])
    assert report['hypothesis']['status']=='supported_within_tolerance'
    assert report['summaries']['physical_delay_ms']['median_interval_ms']==[11500,12000]
    one=report['observations'][0]
    assert one['available_lead_ms']==[11000,11500]
    assert one['provider_generation_delay_ms']==[11300,11800]
    assert one['association_only']


def test_one_card_is_not_twenty_independent_samples():
    events=[event(i,card_id='same-card') for i in range(20)]
    report=analyze_latency(events,[q for e in events for q in quotes(e)])
    assert report['summaries']['physical_delay_ms']['independent_clusters']==1
    assert report['hypothesis']['status']=='insufficient_physical_timing'


def test_negative_market_lead_is_not_clamped_to_zero():
    e=event(received_ms=104000)
    report=analyze_latency([e],quotes(e,-2000))
    row=report['observations'][0]
    assert row['physical_delay_ms']==[-2500,-2000]
    assert row['available_lead_ms']==[-6500,-6000]


def test_generated_packet_and_broadcast_are_not_physical_time():
    e=event(physical_start_ms=None,physical_end_ms=None,physical_time_source=None,physical_clock_verified=False,broadcast_observed_ms=100000)
    report=analyze_latency([e],quotes(e))
    row=report['observations'][0]
    assert row['physical_delay_ms'] is None
    assert row['broadcast_relative_delay_ms']==[11500,12000]
    assert report['hypothesis']['status']=='insufficient_physical_timing'


def test_overlap_unconfirmed_mapping_and_empty_capture_fail_honestly():
    a=event();b=event(1,bout_id=a.bout_id,ticker=a.ticker,physical_start_ms=101000,physical_end_ms=101000,received_ms=101500)
    assert analyze_latency([a,b],quotes(a))['reason_counts']=={'overlapping_event_window':2}
    report=analyze_latency([],[])
    assert report['hypothesis']['status']=='insufficient_physical_timing'
    assert report['summaries']['physical_delay_ms']['median_interval_ms'] is None
    assert analyze_latency([event(mapping_confirmed=False)],quotes(a))['reason_counts']=={'mapping_unconfirmed':1}


@pytest.mark.parametrize('bad,reason',[('stale','unhealthy_quote_in_search_window'),('sparse','quote_sampling_gap'),('spread','unhealthy_quote_in_search_window'),('depth','unhealthy_quote_in_search_window')])
def test_stale_sparse_and_unexecutable_books_cannot_establish_first_reaction(bad,reason):
    e=event();q=quotes(e)
    target=next(i for i,row in enumerate(q) if row.received_ms==105000)
    if bad=='stale':q[target]=q[target].model_copy(update={'stale':True})
    if bad=='depth':q[target]=q[target].model_copy(update={'ask_depth':Decimal('0')})
    if bad=='spread':q[target]=q[target].model_copy(update={'yes_bid':Decimal('.1'),'yes_ask':Decimal('.9')})
    if bad=='sparse':q=[row for row in q if not 104000<=row.received_ms<=110000]
    result=analyze_latency([e],q)
    assert result['reason_counts']=={reason:1} and result['status_counts']=={'excluded':1}


def test_mocks_never_support_physical_hypothesis_even_in_demo_mode():
    events=[event(i,is_mock=True) for i in range(20)]
    q=[v for e in events for v in quotes(e,is_mock=True)]
    assert analyze_latency(events,q)['reason_counts']=={'mock_excluded':20}
    report=analyze_latency(events,q,StudyConfig(include_mocks=True))
    assert report['status_counts']=={'detected':20}
    assert report['hypothesis']['status']=='insufficient_physical_timing'
    assert report['broadcast_hypothesis']['status']=='insufficient_broadcast_observations'


def test_no_reaction_is_censored_not_a_zero_or_fabricated_lag():
    e=event()
    result=analyze_latency([e],quotes(e,100000))
    assert result['status_counts']=={'window_censored':1}
    assert result['summaries']['available_lead_ms']['n']==0


def test_invalid_physical_clock_and_direction_require_provenance():
    with pytest.raises(ValueError):event(physical_end_ms=99999)
    with pytest.raises(ValueError):event(physical_time_source=None)
    with pytest.raises(ValueError):event(direction_basis=None)
    assert [s['additional_feed_delay_ms'] for s in latency_stress_scenarios()]==[0,5000,12000,20000]
    assert all(not s['measured'] for s in latency_stress_scenarios())


def test_journal_reader_preserves_clock_distinctions_and_does_not_relabel_packets(tmp_path):
    store=Store(str(tmp_path/'journal.db'))
    mapping={'ticker':'KXUFCFIGHT-TEST-0','bout_id':'bout0','card_id':'card0','confirmed':True}
    store.append('operator','mapping_review',mapping,recv_utc_ns=99000000000)
    store.append('licensed','fight',{'bout_id':'bout0','kind':'stats','source_ts_ms':100200},recv_utc_ns=100500000000)
    e=event();q=quotes(e)[0]
    store.append('kalshi','book',{'ticker':q.ticker,'yes_bid':str(q.yes_bid),'yes_ask':str(q.yes_ask),'yes':[[str(q.yes_bid),'100']],'no':[[str(1-q.yes_ask),'100']],'stale':False,'market_status':'active','exchange_ts_ms':74900,'source_protocol':'websocket'},recv_utc_ns=q.received_ms*1000000)
    events,observations,warnings=read_journal(store.db)
    assert events[0].physical_start_ms is None and events[0].provider_generated_ms==100200
    assert observations[0].received_ms==75000 and observations[0].exchange_ts_ms==74900
    assert warnings
    store.append('operator','latency_annotation',e.model_dump(mode='json'),recv_utc_ns=e.received_ms*1000000)
    annotated,_,_=read_journal(store.db)
    assert len(annotated)==1 and annotated[0].physical_clock_verified
    store.close()


@pytest.mark.parametrize('clock', ['provider_generated_ms','broadcast_observed_ms','physical_end_ms'])
def test_future_event_clocks_rejected_unless_within_explicit_uncertainty(clock):
    with pytest.raises(ValueError):event(**{clock:101000})
    assert event(clock_uncertainty_ms=500,**{clock:101000})


def test_inactive_or_unknown_lifecycle_quotes_cannot_create_reaction():
    e=event()
    q=quotes(e)
    q=[row.model_copy(update={'market_status':'finalized'}) if row.received_ms>=111000 else row for row in q]
    assert analyze_latency([e],q)['reason_counts']=={'unhealthy_quote_in_search_window':1}
    assert analyze_latency([e],quotes(e,market_status=None))['reason_counts']=={'insufficient_pre_event_baseline':1}


def test_explicit_annotations_keep_discrete_finish_as_confound(tmp_path):
    store=Store(str(tmp_path/'events.db'));e=event()
    store.append('operator','mapping_review',{'ticker':e.ticker,'bout_id':e.bout_id,'card_id':e.card_id,'confirmed':True})
    store.append('operator','latency_annotation',e.model_dump(mode='json'))
    store.append('licensed','fight',{'bout_id':e.bout_id,'kind':'stats','source_ts_ms':101000},recv_utc_ns=101500000000)
    store.append('licensed','fight',{'bout_id':e.bout_id,'kind':'finish','source_ts_ms':102000},recv_utc_ns=102500000000)
    events,_,_=read_journal(store.db)
    assert len(events)==2 and any(row.event_kind=='packet:finish' for row in events)
    report=analyze_latency(events,quotes(e))
    assert report['observations'][0]['reason']=='overlapping_event_window'


def test_cross_session_journal_requires_explicit_selection(tmp_path):
    store=Store(str(tmp_path/'sessions.db'));e=event()
    store.session='first';store.append('operator','latency_annotation',e.model_dump(mode='json'))
    store.session='second';store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.54','yes_ask':'.56','yes':[['.54','100']],'no':[['.44','100']],'stale':False,'market_status':'active'},recv_utc_ns=112000000000)
    with pytest.raises(ValueError,match='explicit session'):read_journal(store.db)
    events,quotes_read,_=read_journal(store.db,'first')
    assert len(events)==1 and not quotes_read
    events,quotes_read,_=read_journal(store.db,'second')
    assert not events and len(quotes_read)==1


def test_raw_lifecycle_finalization_overrides_a_nonstale_book(tmp_path):
    store=Store(str(tmp_path/'final.db'));e=event()
    store.append('core','market',{'ticker':e.ticker,'status':'active'})
    store.append('kalshi_ws','market_lifecycle_v2',{'msg':{'market_ticker':e.ticker,'event_type':'settled'}})
    store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.54','yes_ask':'.56','yes':[['.54','100']],'no':[['.44','100']],'stale':False,'market_status':'active'},recv_utc_ns=112000000000)
    _,quotes_read,_=read_journal(store.db)
    assert quotes_read[0].market_status=='finalized'


def test_broadcast_hypothesis_tests_visible_delay_without_claiming_physical_delay():
    events=[event(i,physical_start_ms=None,physical_end_ms=None,physical_time_source=None,physical_clock_verified=False,broadcast_observed_ms=100000) for i in range(20)]
    report=analyze_latency(events,[q for e in events for q in quotes(e)])
    assert report['hypothesis']['status']=='insufficient_physical_timing'
    assert report['broadcast_hypothesis']['status']=='supported_within_tolerance'
    assert report['broadcast_hypothesis']['confidence_interval_ms']==[11500,12000]
    assert report['broadcast_hypothesis']['observed_independent_cards']==20
    mock_events=[e.model_copy(update={'is_mock':True}) for e in events]
    mock_quotes=[q.model_copy(update={'is_mock':True}) for e in events for q in quotes(e)]
    assert analyze_latency(mock_events,mock_quotes,StudyConfig(include_mocks=True))['broadcast_hypothesis']['status']=='insufficient_broadcast_observations'


def test_reader_hard_limit_never_silently_discards_baseline(tmp_path):
    path=str(tmp_path/'bounded.db');store=Store(path);e=event()
    for i in range(5):store.append('operator','latency_annotation',e.model_copy(update={'id':str(i)}))
    with pytest.raises(ValueError,match='exceeds max_records=3'):read_journal(path,max_records=3)
    assert not store.db.in_transaction
    events,_,_=read_journal(path,max_records=5)
    assert len(events)==5


def test_reader_does_not_load_raw_frames_and_retains_only_latest_prior_metadata(tmp_path):
    path=str(tmp_path/'bounded-metadata.db');store=Store(path);e=event()
    store.session='old'
    for i in range(10):
        store.append('core','market',{'ticker':e.ticker,'status':'active' if i==9 else 'inactive'})
        store.append('operator','mapping_review',{'ticker':e.ticker,'bout_id':e.bout_id,'card_id':e.card_id,'confirmed':i==9})
    for _ in range(5):store.append('kalshi_ws','frame',{'irrelevant':'x'*10000})
    store.session='current'
    store.append('licensed','fight',{'bout_id':e.bout_id,'kind':'stats','source_ts_ms':100200},recv_utc_ns=100500000000)
    store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.49','yes_ask':'.51','yes':[['.49','100']],'no':[['.49','100']],'stale':False},recv_utc_ns=100500000000)
    events,quotes_read,_=read_journal(path,'current',max_records=4)
    assert len(events)==1 and events[0].mapping_confirmed
    assert len(quotes_read)==1 and quotes_read[0].market_status=='active'
    with pytest.raises(ValueError,match='plus required prior metadata'):read_journal(path,'current',max_records=3)


def test_reader_respects_borrowed_transaction_and_releases_own_snapshot(tmp_path):
    store=Store(str(tmp_path/'transaction.db'))
    with store.transaction():
        store.append('core','market',{'ticker':'KXUFCFIGHT-TEST','status':'active'})
        read_journal(store.db)
        assert store.db.in_transaction
    read_journal(store.db)
    assert not store.db.in_transaction


def test_receive_time_window_filters_episodes_but_retains_metadata_without_session(tmp_path):
    store=Store(str(tmp_path/'window.db'));e=event()
    store.session='old'
    store.append('core','market',{'ticker':e.ticker,'status':'active'},recv_utc_ns=1000000000)
    store.append('operator','mapping_review',{'ticker':e.ticker,'bout_id':e.bout_id,'card_id':e.card_id,'confirmed':True},recv_utc_ns=1001000000)
    for i in range(6):
        store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.49','yes_ask':'.51','yes':[['.49','100']],'no':[['.49','100']],'stale':False},recv_utc_ns=(80000+i)*1000000)
    store.append('operator','latency_annotation',e.model_dump(mode='json'),recv_utc_ns=e.received_ms*1000000)
    store.session='new'
    newer=event(1,ticker=e.ticker,bout_id=e.bout_id,card_id=e.card_id,received_ms=200500,provider_generated_ms=200200,physical_start_ms=200000,physical_end_ms=200000)
    store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.49','yes_ask':'.51','yes':[['.49','100']],'no':[['.49','100']],'stale':False},recv_utc_ns=200000000001)
    store.append('operator','latency_annotation',newer.model_dump(mode='json'),recv_utc_ns=200500000999)
    events,quotes_read,_=read_journal(store.db,max_records=4,start_ms=200000,end_ms=200500)
    assert [v.id for v in events]==[newer.id]
    assert len(quotes_read)==1 and quotes_read[0].received_ms==200000 and quotes_read[0].market_status=='active'
    with pytest.raises(ValueError,match='plus required prior metadata'):read_journal(store.db,max_records=3,start_ms=200000,end_ms=200500)
    with pytest.raises(ValueError,match='explicit session'):read_journal(store.db,start_ms=80000,end_ms=200500)


@pytest.mark.parametrize('start,end',[(10,9),(-1,10),(10,-1),(True,10),(1.2,10)])
def test_invalid_receive_windows_fail_before_reading(start,end,tmp_path):
    with pytest.raises(ValueError):read_journal(tmp_path/'missing.db',start_ms=start,end_ms=end)


def test_fresh_normalized_inactive_book_overrides_prior_active_discovery(tmp_path):
    store=Store(str(tmp_path/'normalized-final.db'));e=event()
    store.append('core','market',{'ticker':e.ticker,'status':'active'},recv_utc_ns=1000000000)
    store.append('kalshi','book',{'ticker':e.ticker,'yes_bid':'.54','yes_ask':'.56','yes':[['.54','100']],'no':[['.44','100']],'stale':False,'market_status':'finalized'},recv_utc_ns=112000000000)
    _,quotes_read,_=read_journal(store.db,start_ms=111000)
    assert quotes_read[0].market_status=='finalized'
