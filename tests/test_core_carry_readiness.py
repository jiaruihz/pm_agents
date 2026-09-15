"""Economic boundary tests, not fixtures that certify a live implementation."""
from pathlib import Path
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/analysis/reheat_risk'))
from core_carry_readiness import top_quote, replay, summary, portfolio, validate_plan


def fixture():
    return pd.DataFrame([dict(checkpoint_key=str(i),captured_line_index=i,city=c,target_date='2026-09-01',
        created_at_utc=t,current_yes_ask=.9,current_yes_ask_size=5.,current_yes_bid=.89,tick_size=.01,
        research_eligible=True,clock_status='strict_recorded_order',B=.96,O=.97,W=.95,market=.895,
        label=y,token_id=c,condition_id=c,known_at_utc='2026-09-03T12:00:00Z',stage='test',source_line_sha256=str(i))
        for i,(c,t,y) in enumerate([('a','2026-09-01T15:59:00Z',1),('a','2026-09-01T16:01:00Z',1),
                                   ('b','2026-09-01T16:02:00Z',np.nan)])])


def test_five_shares_rewalk_not_half_ten():
    q=top_quote(.9,5,tick=.01)
    assert q['principal']==4.5
    assert q['fee']==pytest.approx(.0225)
    assert not top_quote(.9,4.9,tick=.01)['ready']
    assert not top_quote(.9,5,consumed=5,tick=.01)['ready']
    assert not top_quote(.905,5,tick=.01)['ready']
    assert not top_quote(.9,5,qty=-1,tick=.01)['ready']


def test_joint_orders_cannot_reuse_same_depth_and_reversal_changes_attribution():
    p=fixture().iloc[:1]
    j=replay(p,joint=True)
    assert j[j.selected].model.tolist()==['B']
    assert j[j.model.eq('O')].reason.iloc[0]=='deeper_ladder_unavailable'
    assert replay(p,joint=True,order=('O','B')).query('selected').model.tolist()==['O']
    assert replay(p,models=('B','O')).selected.sum()==2


def test_outcomes_do_not_change_choices_and_pending_is_retained():
    p=fixture();a=replay(p,models=('B','O'))
    p.label=1-p.label;b=replay(p,models=('B','O'))
    pd.testing.assert_frame_equal(a[['selected','reason','budget_after']],b[['selected','reason','budget_after']])
    s=summary(a[a.model.eq('O')])
    assert s['pending']==1
    assert s['pnl_high']-s['pnl_low']==5
    assert a[a.selected].groupby(['model','city','target_date']).size().max()==1


def test_beijing_budget_rollover_and_cost_stress_reselect():
    p=fixture();p.city=['a','b','c']
    bj=replay(p,models=('B',),budget=5)
    assert bj.selected.sum()==2
    assert replay(p,models=('B',),budget=5,timezone='UTC').selected.sum()==1
    p.B=.906
    assert replay(p,models=('B',)).selected.any()
    assert not replay(p,models=('B',),friction=.005).selected.any()


def test_portfolio_combined_cityday_counts_once_and_does_not_fake_pending_orders():
    p=fixture().iloc[:1].copy();p.current_yes_ask_size=10
    j=replay(p,joint=True)
    risk=portfolio(j)
    assert risk['max_unresolved_citydays_receipt_proxy']==1
    assert risk['peak_unresolved_principal_receipt_proxy']==9
    assert np.isnan(risk['peak_pending_order_notional'])


def test_pending_receipt_does_not_release_exposure():
    p=fixture().iloc[:2].copy();p.city=['a','b'];p.label=[np.nan,1]
    p.loc[0,'known_at_utc']='2026-09-01T16:00:00Z'
    result=portfolio(replay(p,models=('B',)))
    assert result['peak_unresolved_principal_receipt_proxy']==9
    assert result['max_unresolved_citydays_receipt_proxy']==2


def test_no_release_global_cap_cannot_reset_at_midnight():
    p=fixture();p.city=['a','b','c'];p.current_yes_ask_size=10
    j=replay(p,joint=True,global_cap=10,strategy_total_cap=5)
    assert j.deployed_cost.sum() <= 10
    assert j.selected.sum()==2
    assert j[j.selected].groupby('model').deployed_cost.sum().le(5).all()


def test_missing_tick_fails_closed_and_plan_does_not_silently_ignore_parameters():
    for tick in [None,np.nan]:
        assert not top_quote(.9,5,tick=tick)['ready']
        p=fixture();p['tick_size']=tick
        assert not replay(p,models=('B',)).selected.any()
    plan=dict(quantity=5,primary_buffer=0,primary_budget_timezone='Asia/Shanghai',market_domain=[.8,.9895],
              strict_clock=False,no_fit=True,no_live=True,primary_daily_budget=10,combined_daily_cap=20,
              proposed_combined_unresolved_cap=20)
    validate_plan(plan)
    for key,value in [('primary_budget_timezone','UTC'),('quantity',10),('strict_clock',True),('combined_daily_cap',19)]:
        with pytest.raises(ValueError):validate_plan(dict(plan,**{key:value}))


def test_buy_all_baseline_keeps_negative_market_expectation():
    p=fixture().iloc[:1]
    r=replay(p,models=('market','buy_all_market_carry','cash'))
    assert r.query("model=='buy_all_market_carry'").selected.all()
    assert r.query("model=='buy_all_market_carry'").expected_pnl.iloc[0]<0
    assert not r.query("model in ['market','cash']").selected.any()
