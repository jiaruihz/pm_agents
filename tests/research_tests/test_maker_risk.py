"""Four acceptance groups: PIT, lifecycle, economics and execution isolation."""
import copy
import json
import math
from dataclasses import replace
import pytest
from weather_model_evaluation.maker_risk import (
    M0,M1_EXTRA,AUTHORITY,OrderState,OfflineBoundary,ExecutionDenied,
    apply_order_event,evaluate_maker_risk,checked_features,predict,split_dates,
    fit_models,counterfactual,evidence_verdict,date_bootstrap,opportunity_weights)
from weather_model_evaluation.maker_risk_data import epoch,utc,clean

IDENTITY={'weather_model':'frozen-weather','quote_mode':'fixed','model_version':'v3','execution_policy':'near'}


def snapshot(t='2026-09-10T12:00:00Z',oid='o'):
    return {'snapshot_id':oid+t,'opportunity_id':oid,'root_order_id':oid,'target_date':t[:10],
            'decision_at':t,'identity':IDENTITY.copy(),'features':dict(zip(M0+M1_EXTRA,[2,.03,.2,.98,30,600,2,0,3,3,0,0])),
            'pit_valid':True,'input_available_at':{'book':t,'probability':t},'probability_reliable':True,
            'fee_confirmed':True,'resting_confirmed':True,'book_valid':True,'clock_uncertainty_sec':0}


def policy(arm='B2',threshold=.9):return {'identity':IDENTITY.copy(),'authority':AUTHORITY.copy(),'threshold':threshold,'arm':arm}


def model():return {'identity':IDENTITY.copy(),'features':list(M0+M1_EXTRA),'coef':[0]*12,'mean':[0]*12,'scale':[1]*12,'intercept':3}


# 1. Time / data.
def test_source_early_receive_late_rejected():
    s=snapshot();s['source_at']='2026-09-01T00:00:00Z';s['input_available_at']['book']='2026-09-10T12:00:01Z'
    with pytest.raises(ValueError,match='late'):checked_features(s,M0)


def test_unknown_not_zero_and_nan_not_json_number():
    assert clean({'p':float('nan'),'owner':'secret'})=={'p':None}
    s=snapshot();s['features']['edge']=None
    with pytest.raises(ValueError):checked_features(s,M0)


def test_identity_not_silently_mixed():
    s=snapshot();s['identity']['quote_mode']='another'
    with pytest.raises(ValueError,match='identity'):predict(model(),s)


def test_order_weight_normalization():
    assert opportunity_weights([{'opportunity_id':'a'}]*3+[{'opportunity_id':'b'}])==[1/3]*3+[1]


def synthetic_data():
    import numpy as np
    rng=np.random.default_rng(17);ss=[];ls=[]
    for day in range(1,11):
        for k in range(20):
            oid=f'{day}-{k}';t=f'2026-09-{day:02d}T12:{k:02d}:00Z';s=snapshot(t,oid)
            # Synthetic independent book noise. Only tests software, never trading evidence.
            s['features']['top_imbalance']=float(rng.uniform(-1,1))
            s['features']['edge']=.02+.001*k;s['features']['p_logit']=float(rng.normal())
            ss.append(s);ls.append({'snapshot_id':s['snapshot_id'],'y30':int(k%2==0),'label_known_at':utc(epoch(t)+30)})
    return ss,ls


def test_real_fit_path_and_train_only_scaling(tmp_path):
    ss,ls=synthetic_data();ss[-1]['features']['p_logit']=1e9
    spec={'identity':IDENTITY,'m1_extra':list(M1_EXTRA),'purge_sec':300,'synthetic':True}
    ledger,models,thresholds,preds=fit_models(ss,ls,spec,tmp_path)
    assert ledger['actual_fit_calls']==2 and ledger['successful_fits']==2
    assert abs(models['M0']['mean'][0])<1
    assert {r['split'] for r in preds}=={'validation','evaluation'}
    assert max(epoch(x['label_known_at']) for x in ls[:120])<epoch(ledger['fit_cutoff'])
    assert thresholds['M1']['roots']==40
    assert all(r['target_date']>'2026-09-06' for r in preds)


def test_future_labels_cannot_train(tmp_path):
    ss,ls=synthetic_data()
    for x in ls:x['label_known_at']='2026-12-01T00:00:00Z'
    fit,*_=fit_models(ss,ls,{'identity':IDENTITY,'m1_extra':list(M1_EXTRA)},tmp_path)
    assert fit['actual_fit_calls']==0 and fit['reason']=='TRAIN_LABELS_LACK_TWO_CLASSES'


def test_gap_does_not_get_model_score():
    s=snapshot();s['book_valid']=False
    d,_=evaluate_maker_risk(s,OrderState('o',5),model(),policy())
    assert d['action']=='ABSTAIN'


def test_empty_real_data_records_zero_fit(tmp_path):
    fit,models,thresholds,_=fit_models([],[],{'identity':IDENTITY,'m1_extra':list(M1_EXTRA)},tmp_path)
    assert fit['actual_fit_calls']==0 and not models
    assert thresholds['M1']['threshold'] is None


def test_naive_clock_refused():
    with pytest.raises(ValueError):epoch('2026-09-10T12:00:00')


# 2. Lifecycle.
def test_absorption_and_safety_priority():
    s=snapshot();d,state=evaluate_maker_risk(s,OrderState('o',5),model(),policy())
    assert d['action']=='WOULD_CANCEL_RISK'
    d2,state2=evaluate_maker_risk(s,state,model(),policy());assert d2['action']=='KEEP' and state==state2
    d,_=evaluate_maker_risk(s,OrderState('o',5,safety_cancel=True),model(),policy());assert d['reason']=='original_safety_rule_priority'


def test_partial_fill_only_remaining():
    d,_=evaluate_maker_risk(snapshot(),OrderState('o',5,matched=2),model(),policy())
    assert d['remaining']==3


def test_cancel_request_not_success():
    state=apply_order_event(OrderState('o',5),{'kind':'cancel_request','status':'CANCELLED','authoritative':False})
    assert not state.terminal


def test_terminal_does_not_resurrect_on_delayed_live():
    s=apply_order_event(OrderState('o',5),{'matched':2,'status':'CANCELLED','authoritative':True})
    s=apply_order_event(s,{'matched':0,'status':'LIVE','authoritative':True})
    assert s.terminal and s.matched==2


def test_unconfirmed_resting_abstains():
    s=snapshot();s['resting_confirmed']=False
    assert evaluate_maker_risk(s,OrderState('o',5),model(),policy())[0]['action']=='ABSTAIN'


def test_economic_breakeven_and_risk_equal_threshold():
    s=snapshot();s['features']['edge']=0
    assert evaluate_maker_risk(s,OrderState('o',5),None,policy('B1'))[0]['action']=='WOULD_CANCEL_ECONOMIC'
    s=snapshot();m=model();p=predict(m,s)
    assert evaluate_maker_risk(s,OrderState('o',5),m,policy(threshold=p))[0]['action']=='KEEP'


# 3. Economic accounting.
def fill(fid='a',won=0,t='2026-09-10T12:01:00Z',qty=5):
    return {'fill_id':fid,'fill_qty':qty,'fill_price':.8,'fees_usd':.01,'contract_won':won,
            'pnl_usd_at_fill':qty*(won-.8)-.01,'fill_ts_utc':t,'fill_clock_verified':True}


def test_avoided_loss_and_forfeited_winner_both_count():
    r=counterfactual([fill(),fill('b',1)],'2026-09-10T12:00:00Z',1,1,added_cost=0)
    assert r['avoided_loss']==pytest.approx(4.01) and r['forgone_winner_profit']==pytest.approx(.99)
    assert r['delta']==pytest.approx(3.02)


def test_partial_fill_suffix_not_cherry_picked():
    fs=[fill(t='2026-09-10T12:00:00Z',qty=2),fill('b',1,qty=3)]
    r=counterfactual(fs,'2026-09-10T12:00:00Z',1,0,added_cost=0)
    assert r['retained_qty']==2 and r['avoided_qty']==3 and r['delta']<0


def test_unknown_settlement_and_cost_propagate():
    f=fill();f['contract_won']=None
    assert counterfactual([f],'2026-09-10T12:00:00Z',1,0,added_cost=0)['delta'] is None
    assert counterfactual([fill()],'2026-09-10T12:00:00Z',1,0)['delta'] is None


def test_unknown_delay_never_optimistic_zero():
    assert counterfactual([fill()],'2026-09-10T12:00:00Z',None,0,added_cost=0)['delta'] is None


def test_duplicate_fill_and_bad_identity_fail():
    with pytest.raises(ValueError,match='duplicate'):counterfactual([fill(),fill()],None,None,None)
    f=fill();f['pnl_usd_at_fill']=10
    with pytest.raises(ValueError,match='identity'):counterfactual([f],'2026-09-10T12:00:00Z',1,0,added_cost=0)


def test_date_bootstrap_keeps_city_correlation():
    r=date_bootstrap([{'target_date':'2026-09-01','delta':1},{'target_date':'2026-09-01','delta':2},{'target_date':'2026-09-02','delta':-1}])
    assert r['dates']==2 and r['replicates']==20000 and r['point']==pytest.approx(2/3)


# 4. Execution isolation and negative controls.
@pytest.mark.parametrize('verb',['place_order','cancel_order','private_call','heartbeat','emit_trade_intent','dispatch'])
def test_execution_boundary_denies_every_side_effect(verb):
    with pytest.raises(ExecutionDenied):getattr(OfflineBoundary(),verb)({'size':5})


def test_authority_cannot_be_escalated():
    with pytest.raises(ExecutionDenied):OfflineBoundary({**AUTHORITY,'execution_notional':1})


def test_pure_decision_does_not_mutate_inputs():
    s=snapshot();saved=copy.deepcopy(s);m=model();pol=policy();d,_=evaluate_maker_risk(s,OrderState('o',5),m,pol)
    assert s==saved and d['authority']==AUTHORITY and 'TradeIntent' not in json.dumps(d)


def test_synthetic_noise_cannot_certify_profit():
    assert evidence_verdict(synthetic=True,predictive_support=True,actionable=True,economic_support=True)=='SOFTWARE_ONLY_NO_STRATEGY_CERTIFICATION'
    f=fill();f['synthetic']=True
    with pytest.raises(ValueError,match='synthetic'):counterfactual([f],None,None,None)


def test_after_fill_classifier_has_no_action_value():
    d,_=evaluate_maker_risk(snapshot(),OrderState('o',5,matched=5,terminal=True),model(),policy())
    assert d['action']=='KEEP'
    assert evidence_verdict(synthetic=False,predictive_support=True,actionable=False,economic_support=True)=='NO_PRE_FILL_EXECUTION_VALUE'
    r=counterfactual([fill(t='2026-09-10T12:00:00Z')],'2026-09-10T12:01:00Z',1,0,added_cost=0)
    assert r['delta']==0 and r['avoided_qty']==0


def test_best_quote_before_delta_is_pending_not_permanent_gap(tmp_path):
    from weather_model_evaluation.maker_risk_replay import book_queries,write_rows
    base=epoch('2026-09-10T12:00:00Z');token='123'
    write_rows(tmp_path/'epochs_a.jsonl',[{'subscription_epoch_id':'e','previous_subscription_epoch_id':None,'started_at_utc':utc(base),
        'reason':'connect','producer_build_id':'fixture','selector_version':'test','capture_policy':{},'token_ids':[token],'token_rows':{}}])
    def frame(sec,message):
        return {'subscription_epoch_id':'e','received_at_utc':utc(base+sec),'received_at_ns':int((base+sec)*1e9),
                'message':message,'_source':{'path':'fixture','line':int(sec+1)}}
    data=[frame(0,{'event_type':'book','asset_id':token,'timestamp':int(base*1000),'bids':[{'price':'.8','size':'10'}],'asks':[{'price':'.84','size':'10'}]}),
          frame(20,{'event_type':'best_bid_ask','asset_id':token,'timestamp':int((base+20)*1000),'best_bid':'.78','best_ask':'.84'}),
          frame(20.001,{'event_type':'price_change','timestamp':int((base+20)*1000),'price_changes':[
              {'asset_id':token,'price':'.8','size':'0','side':'BUY','best_bid':'.78','best_ask':'.84'},
              {'asset_id':token,'price':'.78','size':'10','side':'BUY','best_bid':'.78','best_ask':'.84'}]}),
          frame(50,{'event_type':'last_trade_price','asset_id':token})]
    write_rows(tmp_path/'frames_a.jsonl',data)
    books,audit=book_queries(tmp_path,{'o':{'token_id':token,'created_at_utc':utc(base),'quote_tick_size':.01}},
                             {token:{base+10,base+20,base+40}})
    before=books[token,base+10];after=books[token,base+40]
    assert before['valid'] and after['valid'] and before['generation']==after['generation']
    assert not books[token,base+20]['valid']
    assert after['bid']-before['bid']==pytest.approx(-.02)


def test_label_source_early_receive_after_horizon_is_rejected():
    from weather_model_evaluation.maker_risk_replay import label_state_valid
    t=epoch('2026-09-10T12:00:00Z')
    current={'valid':True,'tick':.01,'generation':1,'available_at':utc(t)}
    late={'valid':True,'tick':.01,'generation':1,'source_at':utc(t+20),'available_at':utc(t+31)}
    assert not label_state_valid(current,late,t,30)
    late['available_at']=utc(t+30)
    assert label_state_valid(current,late,t,30)
    late['generation']=2
    assert not label_state_valid(current,late,t,30)


def test_frozen_nine_dates_are_five_two_two_and_never_reassigned():
    ss,_=synthetic_data();ss=[s for s in ss if s['target_date']<='2026-09-09']
    split=split_dates(ss)
    assert [len(split[k]) for k in ('train','validation','evaluation')]==[5,2,2]
    requested={k:split[k] for k in ('train','validation','evaluation')}
    assert split_dates(ss,requested=requested)==split
    requested['evaluation']=['2026-09-07','2026-09-09']
    with pytest.raises(ValueError):split_dates(ss,requested=requested)


def test_corrupt_frozen_model_is_not_silently_zipped():
    m=model();m['coef']=[]
    with pytest.raises(ValueError,match='dimensions'):predict(m,snapshot())
