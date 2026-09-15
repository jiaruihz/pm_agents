"""Frozen, offline readiness diagnostics. No fitter, order client or live switch.

Top-of-book is sufficient depth only when its recorded quantity covers the
whole order. Missing lower levels remain unknown, including in stress runs.
Available-book policy outputs are conditional diagnostics, not a production
counterfactual. Labels are joined only after entry and daily-budget decisions.
"""
from pathlib import Path
import gzip
import hashlib
import json
import sys

import joblib
import numpy as np
import pandas as pd
from scipy.special import expit

from core_carry_model_development import BASE, dump, sha, utc

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.strategies.weather_edge_v1.tools.current_yes_core_carry import walk_ask_ladder, evaluate_entry

O = 'O_pooled_hgb_medium'
W = 'W_hgb_7leaf_strong'
EXPECTED_O = '5a54e8aaff31ba475431d7bc9fe5e47bd1c790198ea0bcc1e0df356f0eb9f75c'


def top_quote(ask, size, qty=5., consumed=0., tick=None):
    """Exact recorded first level; no inferred deeper level or half-cost shortcut."""
    if not all(np.isfinite(v) for v in [ask, size, qty, consumed]) or qty <= 0 or consumed < 0:
        return dict(ready=False, reason='invalid_quantity_or_level')
    if not 0 < ask < 1 or size <= 0:
        return dict(ready=False, reason='invalid_level')
    if tick is None or not np.isfinite(tick) or tick <= 0 or abs(ask / tick - round(ask / tick)) > 1e-6:
        return dict(ready=False, reason='tick_invalid')
    if size - consumed < qty - 1e-9:
        return dict(ready=False, reason='deeper_ladder_unavailable')
    q = walk_ask_ladder([[ask, size - consumed]], qty)
    return dict(ready=True, reason='recorded_top_covers_quantity', principal=q['principal'],
                fee=q['fee'], cost=q['effective_cost'], vwap=q['principal_vwap'], levels_consumed=1)


def replay(p, models=('B', 'O', 'W', 'market', 'buy_all_market_carry', 'cash'), budget=25., friction=0., buffer=0.,
           strict=False, timezone='Asia/Shanghai', qty=5., joint=False, order=('B','O'),
           global_cap=None, strategy_total_cap=None):
    """Decisions read probabilities/quotes, never outcome. Joint depth is per snapshot.

    Separate subsequent snapshots cannot prove replenishment in a counterfactual;
    joint output is still an available-book instantaneous execution diagnostic.
    ``strict`` retains the legacy same-second metadata-exclusion stress. It is
    not a lookahead detector: raw request/response/parse clocks and the producer
    call sequence must be audited before interpreting excluded rows as invalid.
    """
    q = p.copy()
    q['budget_date'] = utc(q.created_at_utc).dt.tz_convert(timezone).dt.strftime('%Y-%m-%d')
    q = q.sort_values(['created_at_utc', 'captured_line_index', 'checkpoint_key'])
    used, entered, records, total = {}, set(), [], {}
    chosen_models = order if joint else models
    for r in q.itertuples():
        consumed = 0.
        for model in chosen_models:
            key = (model, r.city, r.target_date)
            daykey = (model, r.budget_date)
            balance = budget - used.get(daykey, 0.)
            quote = top_quote(r.current_yes_ask, r.current_yes_ask_size, qty,
                              consumed=consumed if joint else 0., tick=r.tick_size)
            cost = quote.get('cost', np.nan) + qty * friction
            prob = r.market if model=='buy_all_market_carry' else (getattr(r, model) if model != 'cash' else np.nan)
            edge = prob - cost / qty
            reason = 'selected'
            if not r.research_eligible:
                reason = 'model_independent_capture_gate'
            elif strict and r.clock_status == 'same_second_compatible_ordering_unknown':
                reason = 'timestamp_order_ambiguous'
            elif not quote['ready']:
                reason = quote['reason']
            elif key in entered:
                reason = 'already_entered_cityday'
            elif model == 'cash' or not np.isfinite(prob) or (model!='buy_all_market_carry' and (edge <= 0 or edge < buffer)):
                reason = 'non_positive_or_buffer_edge'
            elif cost > balance + 1e-10:
                reason = 'daily_budget_exhausted'
            elif global_cap is not None and sum(total.values()) + cost > global_cap + 1e-10:
                reason = 'global_no_release_cap'
            elif strategy_total_cap is not None and total.get(model,0.) + cost > strategy_total_cap + 1e-10:
                reason = 'strategy_no_release_cap'
            selected = reason == 'selected'
            if selected:
                entered.add(key)
                used[daykey] = used.get(daykey, 0.) + cost
                total[model] = total.get(model,0.) + cost
                consumed += qty
            records.append(dict(checkpoint_key=r.checkpoint_key, model=model, selected=selected, reason=reason,
                budget_date=r.budget_date, budget_before=balance, budget_after=budget-used.get(daykey, 0.),
                probability=prob, edge=edge, quantity=qty, cost=cost, principal=quote.get('principal', np.nan),
                fee=quote.get('fee', np.nan), vwap=quote.get('vwap', np.nan), friction=friction,
                buffer=buffer, deployed_cost=cost if selected else 0., expected_pnl=qty*edge if selected else 0.,
                clock_status=r.clock_status, current_yes_bid=r.current_yes_bid, current_yes_ask=r.current_yes_ask,
                book_depth_consumed=qty if selected else 0., levels_consumed=quote.get('levels_consumed'),
                breakeven_probability=cost/qty))
    result = pd.DataFrame(records).merge(q[['checkpoint_key','city','target_date','token_id','condition_id',
        'created_at_utc','known_at_utc','label','stage','source_line_sha256']], on='checkpoint_key', validate='many_to_one')
    result['simulated_pnl'] = np.where(result.selected, result.quantity*result.label-result.deployed_cost, 0.)
    result['pnl_low'] = np.where(result.selected, result.quantity*result.label.fillna(0)-result.deployed_cost, 0.)
    result['pnl_high'] = np.where(result.selected, result.quantity*result.label.fillna(1)-result.deployed_cost, 0.)
    result['evidence_class'] = 'available_book_captured_taker_diagnostic_not_full_production_replay'
    return result


def summary(g, budget=25.):
    s = g[g.selected]
    capital = s.deployed_cost.sum()
    days = g.budget_date.nunique() if 'budget_date' in g else 0
    return dict(tickets=len(s), wins=int(s.label.eq(1).sum()), losses=int(s.label.eq(0).sum()),
        pending=int(s.label.isna().sum()), capital=capital, fees=s.fee.sum(), expected_pnl=s.expected_pnl.sum(),
        settled_simulated_pnl=s.loc[s.label.notna(),'simulated_pnl'].sum(), pnl_low=s.pnl_low.sum(),
        pnl_high=s.pnl_high.sum(), roi_low=s.pnl_low.sum()/capital if capital else np.nan,
        roi_high=s.pnl_high.sum()/capital if capital else np.nan,
        allocated_capital_return_low=s.pnl_low.sum()/(budget*days) if days else np.nan,
        unused_daily_risk_budget=budget*days-capital,
        budget_blocked=int(g.reason.isin(['daily_budget_exhausted','global_no_release_cap','strategy_no_release_cap']).sum()),
        missing_depth_slots=int(g.reason.eq('deeper_ladder_unavailable').sum()))


def probability_tables(p):
    metrics, calibration = [], []
    for stage, full in p.groupby('stage'):
        carry = full[full.market.between(.8,.9895)]
        scopes = [('all',full),('carry',carry),('carry_O_gt90',carry[carry.O.gt(.9)]),
                  ('carry_O_gt95',carry[carry.O.gt(.95)]),('carry_O_positive_EV',carry[carry.O.gt(carry.cost5/5)])]
        for scope, q in scopes:
            for model in ['market','B','O','W']:
                a = q[q.label.notna() & q[model].notna()].copy()
                if a.empty:
                    continue
                a['p'] = a[model].clip(1e-12,1-1e-12)
                a['brier']=(a.p-a.label)**2
                a['logloss']=-(a.label*np.log(a.p)+(1-a.label)*np.log1p(-a.p))
                cd=a.groupby(['target_date','city'])[['brier','logloss']].mean()
                date=cd.groupby('target_date').mean()
                for weighting, data in [('checkpoint',a),('cityday',cd),('target_date',date)]:
                    metrics.append(dict(stage=stage,scope=scope,model=model,weighting=weighting,rows=len(q),
                        labeled=len(a),pending=int(q.label.isna().sum()),dates=len(date),
                        brier=data.brier.mean(),logloss=data.logloss.mean()))
                axes={'probability':pd.cut(a.p,[0,.5,.8,.9,.95,.975,1],include_lowest=True).astype(str),
                      'edge':pd.cut(a.p-a.cost5/5,[-np.inf,0,.001,.0025,.005,.01,.02,np.inf]).astype(str),
                      'local_hour':a.decision_hour_local.astype(str)}
                for axis, bucket in axes.items():
                    for name, b in a.assign(bucket=bucket).groupby('bucket'):
                        calibration.append(dict(stage=stage,scope=scope,model=model,axis=axis,bucket=name,
                            rows=len(b),dates=b.target_date.nunique(),mean_p=b.p.mean(),observed=b.label.mean(),
                            brier=b.brier.mean(),logloss=b.logloss.mean()))
    return pd.DataFrame(metrics),pd.DataFrame(calibration)


def tail_tables(ledger):
    rows, uncertainty = [], []
    for stage,g in ledger.groupby('stage'):
        for axis in ['target_date','budget_date']:
            dates=sorted(g[axis].unique())
            for size in [1,2,3]:
                for i in range(len(dates)-size+1):
                    omit=dates[i:i+size]
                    vals={}
                    for model in ['B','O']:
                        s=g[g.model.eq(model) & ~g[axis].isin(omit)]
                        lo,hi=s.pnl_low.sum(),s.pnl_high.sum()
                        vals[model]=(lo,hi)
                        rows.append(dict(stage=stage,axis=axis,removed='|'.join(omit),model=model,block=size,pnl_low=lo,pnl_high=hi))
                    rows.append(dict(stage=stage,axis=axis,removed='|'.join(omit),model='O-B',block=size,
                        pnl_low=vals['O'][0]-vals['B'][1],pnl_high=vals['O'][1]-vals['B'][0]))
            if axis=='target_date':
                daily=g.groupby(['target_date','model']).pnl_low.sum().unstack().reindex(dates).fillna(0)
                delta=daily.O-daily.B
                rng=np.random.default_rng(20260915)
                means=np.mean(rng.choice(delta.to_numpy(),(10000,len(delta))),axis=1)
                uncertainty.append(dict(stage=stage,independent_dates=len(delta),daily_delta=delta.mean(),
                    ci_low=np.quantile(means,.025),ci_high=np.quantile(means,.975),
                    caveat='date bootstrap on available-book diagnostic; pending assigned loss; no alpha proof'))
    return pd.DataFrame(rows),pd.DataFrame(uncertainty)


def portfolio(g):
    """Receipt timestamp is an exposure-release PROXY, never cash settlement time."""
    s=g[g.selected].copy()
    events=[]
    for r in s.itertuples():
        t=pd.Timestamp(r.created_at_utc)
        events.append((t,1,r.principal,(r.city,r.target_date)))
        release=pd.to_datetime(r.known_at_utc,utc=True,errors='coerce')
        if r.label in (0,1) and pd.notna(release) and release>t:
            events.append((release,-1,-r.principal,(r.city,r.target_date)))
    outstanding=peak=0.; counts={};max_count=0
    for t,kind,cost,key in sorted(events,key=lambda x:(x[0],x[1])):
        outstanding+=cost;counts[key]=counts.get(key,0)+kind
        peak=max(peak,outstanding);max_count=max(max_count,sum(v>0 for v in counts.values()))
    daily=g.groupby('budget_date').pnl_low.sum()
    equity=np.r_[0.,daily.cumsum()]
    return dict(deployed_capital=s.deployed_cost.sum(),peak_unresolved_principal_receipt_proxy=peak,
        no_release_principal_upper=s.principal.sum(),peak_pending_order_notional=np.nan,
        actual_peak_gross_exposure=np.nan,max_unresolved_citydays_receipt_proxy=max_count,
        max_drawdown_settled_or_pending_loss_scenario=float((np.maximum.accumulate(equity)-equity).max()),
        worst_ticket=s.pnl_low.min() if len(s) else 0.,
        best_target_date=g.groupby('target_date').pnl_low.sum().max(),
        worst_target_date=g.groupby('target_date').pnl_low.sum().min(),
        caveat='instant quote diagnostic; unknown queue/open-order/cash-release times; no actual B10 risk equivalence')


def validate_plan(plan):
    """Accept supported configuration or fail; never just relabel a calculation."""
    expected={'quantity':5,'primary_buffer':0,'primary_budget_timezone':'Asia/Shanghai',
              'market_domain':[.8,.9895],'strict_clock':False,'no_fit':True,'no_live':True}
    for key,value in expected.items():
        if plan.get(key)!=value:
            raise ValueError(f'Unsupported frozen contract {key}: {plan.get(key)}')
    for key in ['primary_daily_budget','combined_daily_cap','proposed_combined_unresolved_cap']:
        if not np.isfinite(plan[key]) or plan[key]<=0:raise ValueError(f'Invalid {key}')
    if 2*plan['primary_daily_budget']>plan['combined_daily_cap']:
        raise ValueError('Per-strategy caps exceed combined daily cap')


def run(output):
    output=Path(output)
    if (output/'RUN_STARTED.json').exists():
        raise FileExistsError('Append-only run: choose a new output directory')
    plan=json.loads((output/'PLAN.json').read_text())
    validate_plan(plan)
    dump(output/'RUN_STARTED.json',{'observed_at_utc':pd.Timestamp.now(tz='UTC').isoformat(),'plan_sha256':sha(output/'PLAN.json')})
    src,ext=Path(plan['source']),Path(plan['extended'])
    qty=float(plan['quantity']);budget=float(plan['primary_daily_budget'])
    inputs=[]
    def verified(path,sealroot):
        seal=json.loads((sealroot/'DELIVERY_SEAL.json').read_text())
        expected={r['path']:r['sha256'] for r in seal['files']}
        relative=str(path.relative_to(sealroot))
        if sha(path)!=expected[relative]: raise ValueError(f'Seal mismatch {path}')
        inputs.append(dict(path=str(path),sha256=sha(path)))
        return path
    pred={s:pd.read_parquet(verified(src/s/'predictions.parquet',src)) for s in ['validation','test']}
    parent_plan=json.loads(verified(src/'PLAN.json',src).read_text())
    bpath=Path(parent_plan['source'])/'inputs/B_original_v3.json'
    if sha(bpath) not in (bpath.parent.parent/'INPUT_MANIFEST.json').read_text():
        raise ValueError('B frozen identity mismatch')
    b_artifact=json.loads(bpath.read_text())
    b_artifact['entry_policy']['taker_shares']=qty
    inputs.append(dict(path=str(bpath),sha256=sha(bpath)))
    members=pd.read_parquet(verified(src/'test/training_memberships.parquet',src))
    objpath=verified(src/f'test/models/2026-09-08_{O}.joblib',src)
    if sha(objpath)!=EXPECTED_O: raise ValueError('O identity mismatch')
    obj=joblib.load(objpath)
    if obj['columns']!=BASE: raise ValueError('Feature order mismatch')
    restored=obj['model'].predict_proba(pred['test'][BASE])[:,1]
    if not np.array_equal(restored,pred['test'][O]): raise ValueError('Frozen prediction parity mismatch')
    if not np.array_equal(restored,obj['model'].predict_proba(pred['test'][BASE])[:,1]):raise ValueError('Nondeterminism')
    if not members.target_date.lt('2026-09-08').all(): raise ValueError('Future training member')
    if not utc(members.known_at_utc).le(pd.Timestamp('2026-09-08',tz='UTC')).all():raise ValueError('Late training label')
    dump(output/'O_ARTIFACT_VERIFICATION.json',dict(sha256=sha(objpath),path=str(objpath),prediction_parity=True,
        repeated_prediction_bitwise_equal=True,training_rows=len(members),citydays=len(members[['city','target_date']].drop_duplicates()),
        max_training_target_date=members.target_date.max(),max_label_receipt=str(utc(members.known_at_utc).max()),
        feature_order=BASE,preprocessing=str(obj['model'].steps[:-1]),no_fit=True,original4061_receipt_PIT_unproven=True))
    wpath=verified(ext/f'models/2026-09-08_{W}.joblib',ext)
    wm=pd.read_parquet(verified(ext/'training_memberships.parquet',ext))
    wm=wm[wm.model.eq(W)&wm.fold.eq('2026-09-08')]
    cols=['checkpoint_key','target_date','known_at_utc','sample_pool']
    a=members[cols].sort_values('checkpoint_key').reset_index(drop=True)
    b=wm[cols].sort_values('checkpoint_key').reset_index(drop=True)
    pd.testing.assert_frame_equal(a,b)
    wobj=joblib.load(wpath)
    if wobj['columns']!=BASE:raise ValueError('W feature order mismatch')
    pred['test'][W]=wobj['model'].predict_proba(pred['test'][BASE])[:,1]
    dump(output/'W_SHADOW_IDENTITY.json',dict(path=str(wpath),sha256=sha(wpath),same_training_memberships=True,
        training_rows=len(wm),cutoff='2026-09-08',parameters=str(wobj['model'].steps[-1][1]),
        preprocessing=str(wobj['model'].steps[:-1]),no_fit=True,shadow_only=True))
    # Use only the raw lines whose hashes were frozen into the parent predictions.
    rawpath=ext/'inputs/raw_pre_live_scores.jsonl.gz'
    manifest=json.loads((ext/'inputs/INPUT_MANIFEST.json').read_text())
    if sha(rawpath) not in json.dumps(manifest):raise ValueError('Raw input seal mismatch')
    inputs.append(dict(path=str(rawpath),sha256=sha(rawpath)))
    wanted=set(pd.concat(pred.values()).source_line_sha256)
    raw={}
    with gzip.open(rawpath,'rb') as f:
        for line in f:
            digest=hashlib.sha256(line).hexdigest()
            if digest in wanted:raw[digest]=json.loads(line)
    if wanted-set(raw):raise ValueError('Frozen raw lines missing')
    frames=[]; raw_rows=[];gate_audit=[]
    for stage,p in pred.items():
        p=p.copy();p['stage']=stage
        p['B'],p['O'],p['W'],p['market']=p.B_original,p[O],p[W],p.A_market
        p['tick_size']=[raw[k].get('current_yes_tick_size',np.nan) for k in p.source_line_sha256]
        # Missing tick prevents trading; this does not erase the probability denominator.
        for r in p.itertuples():
            rr=raw[r.source_line_sha256]
            if rr.get('current_yes_ask')!=r.current_yes_ask or rr.get('current_yes_ask_size')!=r.current_yes_ask_size:
                raise ValueError('Raw quote parity mismatch')
            quote=top_quote(r.current_yes_ask,r.current_yes_ask_size,tick=r.tick_size)
            raw_rows.append(dict(stage=stage,checkpoint_key=r.checkpoint_key,**quote))
            production=evaluate_entry(rr,[[r.current_yes_ask,r.current_yes_ask_size]],b_artifact)
            independent_reasons=[reason for reason in production['reasons']
                if reason not in ['non_positive_taker_ev','insufficient_ask_ladder_depth']]
            gate_audit.append(dict(checkpoint_key=r.checkpoint_key,
                common_gate=not independent_reasons and r.recorded_clock_ok,
                reasons='|'.join(independent_reasons),
                probability_error=production['model_probability_hold']-r.B))
        costs=pd.DataFrame(raw_rows);costs=costs[costs.stage.eq(stage)].set_index('checkpoint_key')
        p['cost5']=p.checkpoint_key.map(costs.cost)
        p['research_eligible']=(p.recorded_clock_ok & p.scored & p.market.between(*plan['market_domain'])
            & p.current_yes_bid.gt(0) & p.current_yes_bid.le(p.current_yes_ask)
            & p[BASE].notna().all(axis=1))
        checks=pd.DataFrame(gate_audit).set_index('checkpoint_key')
        if not p.research_eligible.equals(p.checkpoint_key.map(checks.common_gate).astype(bool)):
            raise ValueError('Common production feature/clock/bracket gate mismatch')
        frames.append(p)
    p=pd.concat(frames,ignore_index=True)
    pd.DataFrame(gate_audit).to_parquet(output/'B_SCORER_GATE_PARITY.parquet',index=False)
    pd.DataFrame(raw_rows).to_parquet(output/'FIVE_SHARE_QUOTES.parquet',index=False)
    p.to_parquet(output/'FROZEN_SCORES.parquet',index=False)
    pm,cal=probability_tables(p)
    pm.to_parquet(output/'PROBABILITY_METRICS.parquet',index=False)
    cal.to_parquet(output/'CALIBRATION.parquet',index=False)
    ledgers=[];stress=[];risk=[];joint_ledgers=[]
    scenarios=[('base',0.,0.,False,'Asia/Shanghai'),('cost_0.25cent',.0025,0.,False,'Asia/Shanghai'),
        ('cost_0.5cent',.005,0.,False,'Asia/Shanghai'),('cost_1cent',.01,0.,False,'Asia/Shanghai'),
        ('buffer_0.25pp',0.,.0025,False,'Asia/Shanghai'),('buffer_0.5pp',0.,.005,False,'Asia/Shanghai'),
        ('buffer_1pp',0.,.01,False,'Asia/Shanghai'),('strict_order',0.,0.,True,'Asia/Shanghai'),
        ('UTC_bridge',0.,0.,False,'UTC')]
    for stage,frame in p.groupby('stage'):
        for name,friction,buffer,strict,tz in scenarios:
            if name=='base': tz=plan['primary_budget_timezone']
            ledger=replay(frame,budget=budget,friction=friction,buffer=buffer,strict=strict,timezone=tz,qty=qty)
            ledger['scenario']=name
            for model,g in ledger.groupby('model'):
                stress.append(dict(stage=stage,scenario=name,model=model,**summary(g,budget)))
            if name=='base':
                ledgers.append(ledger)
                for model,g in ledger.groupby('model'):risk.append(dict(stage=stage,model=model,scenario='independent',**portfolio(g)))
        # Same snapshot cannot sell its first five shares to both strategies twice.
        for order in [('B','O'),('O','B')]:
            j=replay(frame,joint=True,order=order,budget=budget,timezone=plan['primary_budget_timezone']);j['scenario']='joint_'+'_'.join(order)
            joint_ledgers.append(j)
            risk.append(dict(stage=stage,model='B+O',scenario=j.scenario.iloc[0],**portfolio(j)))
        capped=replay(frame,joint=True,budget=budget,timezone=plan['primary_budget_timezone'],
            global_cap=plan['proposed_combined_unresolved_cap'],strategy_total_cap=budget)
        capped['scenario']='joint_no_release_capped'
        joint_ledgers.append(capped)
        risk.append(dict(stage=stage,model='B+O',scenario=capped.scenario.iloc[0],**portfolio(capped)))
        b10=replay(frame,models=('B',),budget=100.,qty=10.)
        risk.append(dict(stage=stage,model='B10_taker_only',scenario='captured_100_BJ_not_actual_production',**portfolio(b10)))
    ledger=pd.concat(ledgers,ignore_index=True)
    ledger.to_parquet(output/'FIVE_SHARE_REPLAY.parquet',index=False)
    pd.concat(joint_ledgers).to_parquet(output/'JOINT_DEPTH_REPLAY.parquet',index=False)
    pd.DataFrame(stress).to_parquet(output/'COST_STRESS.parquet',index=False)
    pd.DataFrame(risk).to_parquet(output/'PORTFOLIO_RISK_REPLAY.parquet',index=False)
    daily=[]
    for axis in ['target_date','budget_date']:
        for (stage,model,date),g in ledger.groupby(['stage','model',axis]):
            daily.append(dict(stage=stage,model=model,axis=axis,date=date,**summary(g,budget)))
    pd.DataFrame(daily).to_parquet(output/'FIVE_SHARE_DAILY.parquet',index=False)
    edges=[]
    for stage,g in ledger[ledger.model.eq('O') & ledger.selected].groupby('stage'):
        for name,mask in [('lt0.1pp',g.edge.lt(.001)),('lt0.25pp',g.edge.lt(.0025)),('lt0.5pp',g.edge.lt(.005)),
            ('lt1pp',g.edge.lt(.01)),('1to2pp',g.edge.ge(.01)&g.edge.lt(.02)),('ge2pp',g.edge.ge(.02))]:
            edges.append(dict(stage=stage,bucket=name,**summary(g[mask]),
                expected_profit_fraction=g.loc[mask,'expected_pnl'].sum()/g.expected_pnl.sum(),
                pnl_fraction=g.loc[mask,'pnl_low'].sum()/g.pnl_low.sum() if g.pnl_low.sum() else np.nan))
    pd.DataFrame(edges).to_parquet(output/'EDGE_BUCKET_ANALYSIS.parquet',index=False)
    tails,uncertainty=tail_tables(ledger)
    tails.to_parquet(output/'DATE_BLOCK_SENSITIVITY.parquet',index=False)
    uncertainty.to_parquet(output/'PAIRED_UNCERTAINTY.parquet',index=False)
    lock=json.loads(verified(src/'SELECTION_LOCK.json',src).read_text())
    # Keep ALL checkpoint reasons, including non-quote rows, on BOTH date axes.
    v=pred['validation'].copy()
    v['entered_formal_UTC_day']=v.budget_day.isin(lock['selection_days'])
    v['label_known_at_cutoff']=v.label.notna() & utc(v.known_at_utc).le(pd.Timestamp('2026-09-08',tz='UTC'))
    v['blocker']=np.select([v.budget_day.lt('2026-08-26'),~v.recorded_clock_ok,~v.quote_ready,
        ~v.label_known_at_cutoff,~v.entered_formal_UTC_day],['partial_UTC_boundary','clock','quote_or_domain',
        'label_receipt_immature','another_opportunity_on_same_UTC_day_blocks_admission'],default='admitted')
    oldledger=pd.read_parquet(verified(src/'validation/ledger.parquet',src))
    for model in [O,W]:
        losses=set(oldledger.loc[oldledger.model.eq(model)&oldledger.selected&oldledger.label.eq(0),'checkpoint_key'])
        v[model+'_loss_ticket']=v.checkpoint_key.isin(losses)
    v.to_parquet(output/'VALIDATION_DATE_ADMISSION_AUDIT.parquet',index=False)
    pairs=[]
    for stage,g in ledger.groupby('stage'):
        s=g[g.selected & g.model.isin(['B','O'])]
        for (city,date),a in s.groupby(['city','target_date']):
            present=set(a.model)
            pairs.append(dict(stage=stage,city=city,target_date=date,group='common' if len(present)==2 else ('O_added' if 'O' in present else 'B_only'),
                same_token=a.token_id.nunique()==1 if len(present)==2 else False,
                B_pnl=a.loc[a.model.eq('B'),'pnl_low'].sum(),O_pnl=a.loc[a.model.eq('O'),'pnl_low'].sum(),
                both_wrong=len(present)==2 and a.label.eq(0).all()))
    pd.DataFrame(pairs).to_parquet(output/'INCREMENTAL_TICKETS.parquet',index=False)
    coverage=[]
    for stage,g in p.groupby('stage'):
        coverage.append(dict(stage=stage,rows=len(g),target_dates=g.target_date.nunique(),pending=int(g.label.isna().sum()),
            independent_citydays=len(g[['city','target_date']].drop_duplicates()),eligible=int(g.research_eligible.sum()),
            eligible_exact5=int((g.research_eligible & g.cost5.notna()).sum()),missing5=int((g.research_eligible & g.cost5.isna()).sum()),
            original_B_truncated_sampling=True,full_production_policy_recoverable=False))
    dump(output/'COVERAGE.json',coverage)
    dump(output/'INPUT_MANIFEST.json',{'files':inputs,'code_sha256':sha(Path(__file__))})
    dump(output/'COMPUTE_CHECKS.json',dict(O_sha_unchanged=sha(objpath)==EXPECTED_O,no_fit=True,
        bitwise_prediction_parity=True,same_W_memberships=True,raw_quote_hash_join=True,
        total_slots=len(p),recorded_raw_matches=len(raw),quantity=5,all_primary_daily_budgets_valid=bool(ledger.budget_after.ge(-1e-9).all()),
        selected_cityday_unique=not ledger[ledger.selected].duplicated(['stage','model','city','target_date']).any(),
        missing_depth_is_not_zero_cost=True,full_policy_economics_identified=False))


def evaluate_fresh(output):
    """Post-freeze official outcomes; no old label/prediction files are overwritten."""
    output=Path(output)
    if (output/'FRESH_LABEL_EVALUATION.parquet').exists():raise FileExistsError('Fresh evaluation already sealed')
    p=pd.read_parquet(output/'FROZEN_SCORES.parquet')
    plan=json.loads((output/'PLAN.json').read_text());validate_plan(plan)
    evidence=[];mapping={}
    for line in (output/'FRESH_LABEL_RESPONSES.jsonl').read_text().splitlines():
        r=json.loads(line);body=r.get('body',{});tokens=body.get('tokens',[])
        if r.get('status')!=200 or body.get('condition_id')!=r['condition_id']:
            raise ValueError('Official response identity mismatch')
        if body.get('closed') is not True or sum(t.get('winner') is True for t in tokens)!=1:continue
        for t in tokens:
            key=(r['condition_id'],t['token_id'])
            value=int(t.get('winner') is True)
            if key in mapping and mapping[key][0]!=value:raise ValueError('Conflicting terminal label')
            mapping[key]=(value,r['received_at_utc'])
    for i,row in p[p.label.isna()].iterrows():
        key=(row.condition_id,row.token_id)
        if key not in mapping:continue
        label,receipt=mapping[key]
        evidence.append(dict(checkpoint_key=row.checkpoint_key,city=row.city,target_date=row.target_date,
            condition_id=key[0],token_id=key[1],old_label=None,label=label,label_received_at_utc=receipt))
        p.loc[i,'label']=label
        p.loc[i,'known_at_utc']=pd.Timestamp(receipt)
    pd.DataFrame(evidence).to_parquet(output/'FRESH_LABEL_EVALUATION.parquet',index=False)
    ledgers=[];stress=[];risk=[]
    scenarios=[('base',0.,0.,False),('cost_0.25cent',.0025,0.,False),('cost_0.5cent',.005,0.,False),
        ('cost_1cent',.01,0.,False),('buffer_0.25pp',0.,.0025,False),('buffer_0.5pp',0.,.005,False),
        ('buffer_1pp',0.,.01,False),('strict_order',0.,0.,True)]
    for stage,q in p.groupby('stage'):
        for name,friction,buffer,strict in scenarios:
            ledger=replay(q,budget=plan['primary_daily_budget'],friction=friction,buffer=buffer,strict=strict,
                timezone=plan['primary_budget_timezone'])
            for model,g in ledger.groupby('model'):
                stress.append(dict(stage=stage,scenario=name,model=model,**summary(g,plan['primary_daily_budget'])))
            if name=='base':
                ledgers.append(ledger)
                for model,g in ledger.groupby('model'):risk.append(dict(stage=stage,model=model,**portfolio(g)))
        joint=replay(q,joint=True,budget=plan['primary_daily_budget'],timezone=plan['primary_budget_timezone'])
        risk.append(dict(stage=stage,model='B+O',**portfolio(joint)))
    fresh=pd.concat(ledgers,ignore_index=True)
    before=pd.read_parquet(output/'FIVE_SHARE_REPLAY.parquet')
    keys=['stage','checkpoint_key','model'];compare=keys+['selected','reason','cost','budget_before','budget_after']
    pd.testing.assert_frame_equal(before[compare].sort_values(keys).reset_index(drop=True),
        fresh[compare].sort_values(keys).reset_index(drop=True))
    fresh.to_parquet(output/'FRESH_FIVE_SHARE_REPLAY.parquet',index=False)
    pd.DataFrame(stress).to_parquet(output/'FRESH_COST_STRESS.parquet',index=False)
    pd.DataFrame(risk).to_parquet(output/'FRESH_PORTFOLIO_RISK.parquet',index=False)
    metrics,cal=probability_tables(p)
    metrics.to_parquet(output/'FRESH_PROBABILITY_METRICS.parquet',index=False)
    cal.to_parquet(output/'FRESH_CALIBRATION.parquet',index=False)
    tails,unc=tail_tables(fresh)
    tails.to_parquet(output/'FRESH_DATE_BLOCK_SENSITIVITY.parquet',index=False)
    unc.to_parquet(output/'FRESH_PAIRED_UNCERTAINTY.parquet',index=False)
    dump(output/'FRESH_LABEL_CHECKS.json',dict(filled_checkpoints=len(evidence),remaining_pending=int(p.label.isna().sum()),
        latest_received_at_utc=max(r['label_received_at_utc'] for r in evidence) if evidence else None,
        response_sha256=sha(output/'FRESH_LABEL_RESPONSES.jsonl'),policy_decisions_and_budgets_unchanged=True,
        no_fit=True,original_label_files_unchanged=True,received_time_is_not_historical_first_known=True))
