import copy

import pytest

from weather_model_evaluation.d1_shadow_ab import evaluate, prepare_rows, resolve_label
from scripts.analysis.market_structure_edge import research_europe_d1_distance2_forward_v8 as legacy


def snapshot():
    baskets, settlements = [], []
    for day, outcomes in [('2026-09-01',[0,1]),('2026-09-02',[0,0])]:
        legs=[]
        for i,ask in enumerate([.8,.99]):
            condition=day+str(i)
            legs.append({'condition_id':condition,'allocation_weight':.5,'cost_per_share':ask+.05*ask*(1-ask),
                         'no_best_ask':ask,'market_p_exact':[.2,.01][i],'bracket':str(20+i)})
            settlements.append({'condition_id':condition,'target_date':day,'settlement_status':'settled','final_price':outcomes[i]})
        baskets.append({'city':'Test','target_date':day,'repo_sha':'frozen','legs':legs,
                        'orders_submitted':0,'actual_notional_usd':0,'decision_status':'would_shadow_entry',
                        'decision_asof_utc':'2026-08-31T12:00:00Z','paired_book_executable':True,
                        'weather_features':{'feature_status':'available','model_rows':[
                            {'available_at_utc':'2026-08-31T11:00:00Z','distance2_exact_probabilities':[.01,.3]}
                            for _ in range(3)]}})
    return {'source_repo_sha':'frozen','baskets':baskets,'settlements':settlements,
            'expected_city_dates':[[r['city'],r['target_date']] for r in baskets]}


def test_exact_identity_conflict_does_not_choose_favorable_label():
    s=snapshot();leg=s['baskets'][0]['legs'][0]
    s['settlements'].append({**s['settlements'][0],'final_price':1})
    assert resolve_label(leg,'2026-09-01',s['settlements']) == (None,'conflicting_exact_settlement')
    assert resolve_label({**leg,'condition_id':'other'},'2026-09-01',s['settlements'])[0] is None
    s['settlements'][0]['final_price']=.5
    assert resolve_label(leg,'2026-09-01',s['settlements'])[1]=='invalid_exact_settlement'


def test_same_denominator_allocation_and_stress():
    r=evaluate(snapshot(),draws=1000)
    for policy in r['policy_metrics'].values():
        assert policy['baskets']==2
        assert policy['stress_roi']['0.01']==pytest.approx(policy['payout']/(policy['cost']+.02)-1)
    assert r['policy_rows']['weather_single_no'][0]['weights']==[1,0]
    assert r['policy_rows']['market_single_no'][0]['weights']==[0,1]
    assert r['probability']['legs']==4


def test_missing_or_late_weather_falls_back_without_dropping_row():
    s=snapshot()
    for m in s['baskets'][0]['weather_features']['model_rows']:
        m['available_at_utc']='2026-09-01T15:00:00Z'
    r=evaluate(s,draws=100)
    assert r['policy_metrics']['weather_single_no']['weather_fallback_baskets']==1
    assert r['policy_rows']['weather_single_no'][0]['weights']==[.5,.5]
    assert r['policy_metrics']['weather_single_no']['baskets']==2
    assert r['probability']['baskets']==1


def test_unsettled_preserved_and_duplicate_rejected():
    s=snapshot();s['settlements']=s['settlements'][:2]
    r=evaluate(s,draws=100)
    assert r['signal_funnel']['selected']==2
    assert r['evidence_funnel']['selected_settled']==1
    assert r['evidence_funnel']['selected_unsettled']==1
    s['baskets'].append(copy.deepcopy(s['baskets'][0]))
    with pytest.raises(ValueError,match='duplicate'):
        prepare_rows(s)


def test_residual_units_and_accounting_identity():
    rows=[{'city':'Test','target_date':'2026-09-01','cost':.9,'payout':.5,'pnl':-.4,
           'joint_market_probability':.05,'joint_tail_hit':1,'market_expected_payout':.95}]
    r=legacy._summarize_rows(rows,draws=100,seed=7)
    assert r['market_probability_overestimate']==pytest.approx(-.45)
    assert r['market_probability_overestimate_ci95']==pytest.approx([-.45,-.45])
    assert r['roi']-r['market_expected_roi_after_ask_fee']==pytest.approx(-.45/.9)
    assert r['execution_price_stress_roi']['plus_1_0_cent']==pytest.approx(-.41/.91)
    assert legacy._point_policy_metrics(rows)['plus_1_0_cent_roi']==pytest.approx(-.41/.91)


def test_residual_point_and_ci_share_market_evidence():
    rows=[{'city':'Test','target_date':'2026-09-01','cost':.9,'payout':.5,'pnl':-.4,
           'joint_market_probability':None,'joint_tail_hit':1,'market_expected_payout':.95}]
    r=legacy._summarize_rows(rows,draws=100,seed=7)
    assert r['market_evidence_candidates']==1
    assert r['market_probability_overestimate']==pytest.approx(-.45)
    assert r['market_probability_overestimate_ci95']==pytest.approx([-.45,-.45])


def test_incomplete_declared_universe_rejected():
    s=snapshot();s['baskets'].pop()
    with pytest.raises(ValueError,match='universe'):
        prepare_rows(s)
