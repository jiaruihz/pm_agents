"""Offline maker continue/cancel research. No execution authority or venue imports.

Run ``python -m weather_model_evaluation.maker_risk --help``. Inputs and labels
are physically separate. Model artifacts are plain JSON, never executable pickle.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

from weather_model_evaluation.maker_risk_data import dump, rows, epoch, utc, sha

M0 = ('p_logit','edge','hour_sin','hour_cos','order_age_sec','weather_age_sec')
M1_EXTRA = ('spread_ticks','top_imbalance','log_bid3','log_ask3','bid_change30_ticks','ask_change30_ticks')
AUTHORITY = dict(mode='offline_or_zero_notional_shadow',execution_notional=0,
                 emit_trade_intent=False,allow_place_order=False,allow_cancel_order=False,
                 allow_signed_venue_calls=False)
TERMINAL = {'FILLED','MATCHED','CANCELLED','CANCELED','EXPIRED','REJECTED'}


class ExecutionDenied(RuntimeError): pass


class OfflineBoundary:
    """No injected client and no dispatch path. All side-effect verbs reject."""
    def __init__(self, authority=None):
        if (authority or AUTHORITY) != AUTHORITY:raise ExecutionDenied('zero-authority contract required')
    def dispatch(self, *args, **kwargs):raise ExecutionDenied('offline research cannot execute')
    place_order=cancel_order=private_call=heartbeat=emit_trade_intent=dispatch


@dataclass(frozen=True)
class OrderState:
    opportunity_id: str
    quantity: float
    matched: float = 0
    terminal: bool = False
    safety_cancel: bool = False
    absorbed: str | None = None


def apply_order_event(state, event):
    """Private cumulative match and terminal state are monotone; request != ack."""
    matched=event.get('matched')
    matched=state.matched if matched is None else float(matched)
    if not math.isfinite(matched) or not 0<=matched<=state.quantity:raise ValueError('bad cumulative quantity')
    q=max(state.matched,matched)
    terminal=state.terminal or q>=state.quantity or (event.get('authoritative') is True and
        str(event.get('status','')).upper() in TERMINAL and matched>=state.matched)
    return replace(state,matched=q,terminal=terminal,
                   safety_cancel=state.safety_cancel or event.get('safety_cancel_requested') is True)


def checked_features(snapshot, names):
    t=epoch(snapshot['decision_at'])
    if not snapshot.get('pit_valid'):raise ValueError('PIT unproven')
    clocks=snapshot.get('input_available_at',{})
    if not clocks or any(v is None or epoch(v)>t for v in clocks.values()):raise ValueError('late or unknown input')
    values=snapshot['features']
    result=[]
    for name in names:
        v=values.get(name)
        if v is None or not math.isfinite(float(v)):raise ValueError('missing feature '+name)
        result.append(float(v))
    return result


def predict(model, snapshot):
    if snapshot['identity'] != model['identity']:raise ValueError('model / quote identity mismatch')
    n=len(model['features'])
    if any(len(model[k])!=n for k in ('coef','mean','scale')) or any(not math.isfinite(float(x)) or x<=0 for x in model['scale']):
        raise ValueError('corrupt frozen model dimensions or scale')
    x=checked_features(snapshot,model['features'])
    z=model['intercept']+sum(c*(v-m)/s for c,v,m,s in zip(model['coef'],x,model['mean'],model['scale']))
    return 1/(1+math.exp(-max(-700,min(700,z))))


def evaluate_maker_risk(snapshot, order_state, frozen_model, frozen_policy):
    """Return research decision and next immutable state, never a TradeIntent."""
    OfflineBoundary(frozen_policy.get('authority'))
    result={'opportunity_id':order_state.opportunity_id,'decision_at':snapshot['decision_at'],
            'identity':snapshot.get('identity'),'authority':AUTHORITY.copy(),'risk':None,'edge':None,
            'risk_model_hash':hashlib.sha256(json.dumps(frozen_model,sort_keys=True).encode()).hexdigest() if frozen_model else None,
            'policy_hash':hashlib.sha256(json.dumps(frozen_policy,sort_keys=True).encode()).hexdigest(),
            'input_available_at':snapshot.get('input_available_at',{}),'remaining':max(0,order_state.quantity-order_state.matched)}
    def done(action,reason,state=order_state):
        return {**result,'action':action,'reason':reason},state
    if order_state.terminal or order_state.matched>=order_state.quantity:return done('KEEP','original_order_terminal')
    if order_state.safety_cancel:return done('KEEP','original_safety_rule_priority')
    if order_state.absorbed:return done('KEEP','absorbing_cancel_already_recorded')
    if snapshot.get('resting_confirmed') is not True:return done('ABSTAIN','resting_lifecycle_unconfirmed')
    try:
        if snapshot.get('identity') != frozen_policy['identity']:raise ValueError('identity mismatch')
        checked_features(snapshot,M0)
        if snapshot.get('probability_reliable') is not True or snapshot.get('fee_confirmed') is not True:
            raise ValueError('probability or fee contract unknown')
        result['edge']=snapshot['features']['edge']
    except (ValueError,KeyError) as exc:return done('ABSTAIN',str(exc))
    if result['edge']<=0:
        return done('WOULD_CANCEL_ECONOMIC','non_positive_fee_adjusted_edge',replace(order_state,absorbed='B1'))
    if frozen_policy.get('arm')=='B1':return done('KEEP','positive_edge')
    try:
        if frozen_model is None or frozen_policy.get('threshold') is None:raise ValueError('THRESHOLD_NOT_IDENTIFIABLE')
        if snapshot.get('book_valid') is not True:raise ValueError('book invalid or gap')
        result['risk']=predict(frozen_model,snapshot)
    except (ValueError,KeyError) as exc:return done('ABSTAIN',str(exc))
    if result['risk']>frozen_policy['threshold']:
        return done('WOULD_CANCEL_RISK','strictly_above_frozen_validation_quantile',replace(order_state,absorbed='B2'))
    return done('KEEP','below_or_equal_threshold')


def opportunity_weights(data):
    n=Counter(r['opportunity_id'] for r in data)
    return [1/n[r['opportunity_id']] for r in data]


def split_dates(data, purge_sec=300, requested=None):
    """Complete target-date groups, then remove overlapping boundary dates."""
    dates=sorted({r['target_date'] for r in data})
    if len(dates)<5:raise ValueError('INSUFFICIENT_COMPLETE_DATES')
    a=max(1,int(.6*len(dates)));b=max(a+1,int(.8*len(dates)))
    groups=[list(requested[k]) for k in ('train','validation','evaluation')] if requested else [dates[:a],dates[a:b],dates[b:]]
    flat=[d for g in groups for d in g]
    if len(set(flat))!=len(flat) or not set(dates)<=set(flat) or flat!=sorted(flat):raise ValueError('invalid frozen date split')
    groups=[[d for d in g if d in dates] for g in groups]
    bydate={d:[r for r in data if r['target_date']==d] for d in dates}
    purged=[]
    for left,right in [(1,2),(0,1)]:
        if not groups[right]:raise ValueError('EMPTY_SPLIT_AFTER_PURGE')
        boundary=min(epoch(r['decision_at']) for d in groups[right] for r in bydate[d])
        bad=[d for d in groups[left] if any(epoch(r['decision_at'])+purge_sec+float(r.get('clock_uncertainty_sec') or 0)>=boundary for r in bydate[d])]
        purged.extend(bad);groups[left]=[d for d in groups[left] if d not in bad]
    if any(not g for g in groups):raise ValueError('EMPTY_SPLIT_AFTER_PURGE')
    return dict(zip(('train','validation','evaluation'),groups))|{'purged_dates':purged}


def date_bootstrap(records, key='delta', repetitions=20000):
    """Paired target-date blocks: all cities on a sampled date travel together."""
    import numpy as np
    groups=defaultdict(list)
    for r in records:groups[r['target_date']].append(float(r[key]))
    if not groups:return {'point':None,'ci95':None,'dates':0}
    ds=sorted(groups);sums=np.array([sum(groups[d]) for d in ds]);n=np.array([len(groups[d]) for d in ds])
    point=float(sums.sum()/n.sum());rng=np.random.default_rng(20260916)
    ix=rng.integers(0,len(ds),size=(repetitions,len(ds)));samples=sums[ix].sum(1)/n[ix].sum(1)
    best=int(np.argmin(sums/n));mask=np.arange(len(ds))!=best
    sensitivity=[]
    for i in range(len(ds)-1):
        keep=[k for k in range(len(ds)) if k not in (i,i+1)]
        sensitivity.append({'removed':ds[i:i+2],'point':float(sums[keep].sum()/n[keep].sum()) if keep else None})
    return {'point':point,'ci95':[float(x) for x in np.quantile(samples,[.025,.975])],
            'dates':len(ds),'date_equal_point':float((sums/n).mean()),'seed':20260916,'replicates':repetitions,
            'drop_best_date':ds[best],'drop_best_point':float(sums[mask].sum()/n[mask].sum()) if mask.any() else None,
            'drop_adjacent_two':sensitivity}


def fit_models(snapshots, labels, spec, out):
    """Two real fits or an explicit zero-fit blocker. No synthetic substitution."""
    import numpy as np
    import sklearn
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.exceptions import ConvergenceWarning
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    ledger={'actual_fit_calls':0,'successful_fits':0,'status':'BLOCKED','models':[],
            'sklearn_version':sklearn.__version__,'numpy_version':np.__version__,'excluded':{},
            'loss':'sum_i w_i logloss_i + ||coef||^2/(2*C); unpenalized intercept; sum weights = number of opportunities',
            'sample_weight':'1 / training snapshots in that opportunity; weighted train-only StandardScaler',
            'synthetic':bool(spec.get('synthetic',False))}
    snapshots=list(snapshots)
    b1_end={}
    for s in snapshots:
        if s.get('safety_cancel') or (s.get('probability_reliable') and s.get('fee_confirmed') and s.get('features',{}).get('edge') is not None and s['features']['edge']<=0):
            key=s['root_order_id'];b1_end[key]=min(b1_end.get(key,float('inf')),epoch(s['decision_at']))
    names=list(M0)+list(spec['m1_extra']);lab={r['snapshot_id']:r for r in labels};common=[];exclude=Counter()
    for s in snapshots:
        try:
            if s.get('identity')!=spec['identity']:raise ValueError('identity_not_primary')
            if not s.get('resting_confirmed'):raise ValueError('not_confirmed_resting')
            if not s.get('book_valid'):raise ValueError('book_gap_or_unconfirmed')
            checked_features(s,names)
            l=lab.get(s['snapshot_id'],{})
            if l.get('label_known_at') is not None and epoch(l['label_known_at']) < epoch(s['decision_at'])+30:raise ValueError('invalid_label_clock')
            common.append({**s,'y':l.get('y30'),'label_known_at':l.get('label_known_at')})
        except (ValueError,KeyError) as e:exclude[str(e)]+=1
    ledger.update(common_rows=len(common),common_opportunities=len({s['opportunity_id'] for s in common}),labelled_common_rows=sum(r['y'] in (0,1) for r in common),excluded=dict(exclude))
    predictions=[];models={}
    try:
        split=split_dates(common,spec.get('purge_sec',300),spec.get('date_split'));ledger['split']=split
        sets={k:[r for r in common if r['target_date'] in split[k]] for k in ('train','validation','evaluation')}
        cutoff=min(epoch(r['decision_at']) for r in sets['validation'])-spec.get('purge_sec',300)
        before=len(sets['train']);sets['train']=[r for r in sets['train'] if r['y'] in (0,1) and r['label_known_at'] and epoch(r['label_known_at'])<=cutoff]
        ledger['late_training_labels_removed']=before-len(sets['train']);ledger['fit_cutoff']=utc(cutoff)
        if {r['y'] for r in sets['train']}!={0,1}:raise ValueError('TRAIN_LABELS_LACK_TWO_CLASSES')
        weights=np.array(opportunity_weights(sets['train']))
        for name,features in [('M0',list(M0)),('M1',names)]:
            x=np.array([checked_features(r,features) for r in sets['train']]);y=np.array([r['y'] for r in sets['train']])
            scaler=StandardScaler().fit(x,sample_weight=weights)
            model=LogisticRegression(C=1.0,penalty='l2',solver='lbfgs',class_weight=None,max_iter=1000,tol=1e-4)
            ledger['actual_fit_calls']+=1
            with warnings.catch_warnings(record=True) as ws:
                warnings.simplefilter('always');model.fit(scaler.transform(x),y,sample_weight=weights)
            converged=not any(issubclass(w.category,ConvergenceWarning) for w in ws)
            artifact={'name':name,'identity':spec['identity'],'features':features,'mean':scaler.mean_.tolist(),
                      'scale':scaler.scale_.tolist(),'coef':model.coef_[0].tolist(),'intercept':float(model.intercept_[0]),
                      'fit_cutoff':utc(cutoff),'training_dates':split['train'],'training_rows':len(x),'converged':converged,
                      'n_iter':model.n_iter_.tolist(),'synthetic':ledger['synthetic']}
            dump(out/(name+'.json'),artifact);models[name]=artifact
            ledger['models'].append({'name':name,'sha256':sha(out/(name+'.json')),'converged':converged,'warnings':[str(w.message) for w in ws]})
            if not converged:raise ValueError('FIT_NOT_CONVERGED')
            ledger['successful_fits']+=1
        for split_name in ('validation','evaluation'):
            for r in sets[split_name]:
                predictions.append({k:r[k] for k in ('snapshot_id','opportunity_id','root_order_id','target_date','decision_at','y')}|
                                   {'split':split_name,'M0':predict(models['M0'],r),'M1':predict(models['M1'],r),
                                    'b1_valid':epoch(r['decision_at'])<b1_end.get(r['root_order_id'],float('inf'))})
        ledger['status']='FITTED'
    except ValueError as e:ledger['reason']=str(e)
    dump(out/'FIT_LEDGER.json',ledger)
    with (out/'predictions.jsonl').open('w') as f:
        for r in predictions:f.write(json.dumps(r)+'\n')
    thresholds={}
    for name in ('M0','M1'):
        maxima={}
        for r in predictions:
            if r['split']=='validation' and r['b1_valid']:maxima[r['root_order_id']]=max(maxima.get(r['root_order_id'],-1),r[name])
        identifiable=len(maxima)>=10 and len(set(maxima.values()))>=2
        thresholds[name]={'threshold':float(np.quantile(list(maxima.values()),.9)) if identifiable else None,
                          'roots':len(maxima),'status':'FROZEN' if identifiable else 'THRESHOLD_NOT_IDENTIFIABLE',
                          'rule':'linear empirical 90th percentile of validation B1-valid root maxima; strict >; >=10 roots and >=2 scores'}
    dump(out/'THRESHOLDS.json',thresholds)
    metrics={'status':'UNIDENTIFIABLE','synthetic':ledger['synthetic']}
    if any(r['split']=='evaluation' and r['y'] in (0,1) for r in predictions):
        ev=[r for r in predictions if r['split']=='evaluation' and r['y'] in (0,1)];byopp=defaultdict(list)
        for r in ev:byopp[r['opportunity_id']].append(r)
        score_rows=[]
        for oid,rs in byopp.items():
            losses={m:[-(r['y']*math.log(max(1e-15,r[m]))+(1-r['y'])*math.log(max(1e-15,1-r[m]))) for r in rs] for m in ('M0','M1')}
            score_rows.append({'opportunity_id':oid,'target_date':rs[0]['target_date'],'delta':sum(losses['M1'])/len(rs)-sum(losses['M0'])/len(rs)})
        metrics={'status':'development_time_forward','synthetic':ledger['synthetic'],'primary':date_bootstrap(score_rows),
                 'positive_event_dates':len({r['target_date'] for r in ev if r['y']==1}),
                 'negative_event_dates':len({r['target_date'] for r in ev if r['y']==0}),'models':{}}
        w=np.array(opportunity_weights(ev))
        for m in ('M0','M1'):
            p=np.array([r[m] for r in ev]);y=np.array([r['y'] for r in ev]);pc=np.clip(p,1e-15,1-1e-15)
            calibration=[]
            for lo in np.linspace(0,.9,10):
                ix=(p>=lo)&(p<lo+.1+1e-14)
                if ix.any():calibration.append({'lower':float(lo),'rows':int(ix.sum()),'weight':float(w[ix].sum()),'forecast':float(np.average(p[ix],weights=w[ix])),'observed':float(np.average(y[ix],weights=w[ix]))})
            from sklearn.metrics import roc_auc_score
            metrics['models'][m]={'logloss':float(np.average(-y*np.log(pc)-(1-y)*np.log(1-pc),weights=w)),
                                   'brier':float(np.average((p-y)**2,weights=w)),'calibration':calibration,
                                   'auc':float(roc_auc_score(y,p,sample_weight=w)) if len(set(y))==2 else None}
    dump(out/'METRICS.json',metrics)
    return ledger,models,thresholds,predictions


def counterfactual(fills, alert_at, delay_sec, clock_uncertainty_sec, *, added_cost=None):
    """Delete only a suffix of real fills; unknowns remain unknown, never zero."""
    seen=set();avoided_loss=forgone_profit=avoided_qty=retained_qty=0.;unknown=[]
    if alert_at is not None and (delay_sec is None or clock_uncertainty_sec is None):
        return {'kind':'counterfactual_diagnostic','delta':None,'reason':'latency_or_clock_uncertainty_unknown','unknown_fills':len(fills)}
    cutoff=epoch(alert_at)+delay_sec+clock_uncertainty_sec if alert_at else None
    for f in fills:
        fid=f['fill_id']
        if fid in seen:raise ValueError('duplicate physical fill')
        seen.add(fid)
        if f.get('synthetic'):raise ValueError('synthetic fills cannot be economic evidence')
        q=float(f['fill_qty'])
        if q<=0 or not math.isfinite(q):raise ValueError('bad fill quantity')
        if cutoff is None:retained_qty+=q;continue
        if f.get('fill_clock_verified') is not True or f.get('fill_ts_utc') is None:unknown.append(fid);continue
        if epoch(f['fill_ts_utc'])<=cutoff:retained_qty+=q;continue
        if f.get('contract_won') not in (0,1) or f.get('fees_usd') is None:unknown.append(fid);continue
        pi=q*(float(f['contract_won'])-float(f['fill_price']))-float(f['fees_usd'])
        if f.get('pnl_usd_at_fill') is not None and not math.isclose(pi,float(f['pnl_usd_at_fill']),abs_tol=1e-7):raise ValueError('fill economic identity mismatch')
        avoided_qty+=q;avoided_loss+=max(0,-pi);forgone_profit+=max(0,pi)
    known=avoided_loss-forgone_profit
    return {'kind':'counterfactual_diagnostic','delta':None if unknown or (alert_at and added_cost is None) else known-(added_cost or 0),
            'known_fill_delta_before_additional_cost':known,'avoided_loss':avoided_loss,'forgone_winner_profit':forgone_profit,
            'avoided_qty':avoided_qty,'retained_qty':retained_qty,'unknown_fills':len(unknown),'unknown_fill_ids':unknown,
            'added_cost':added_cost,'assumptions':['cancel succeeds at stated delay','no own market impact','no reinvestment','no downstream eligibility change']}


def evidence_verdict(*, synthetic, predictive_support, actionable, economic_support):
    if synthetic:return 'SOFTWARE_ONLY_NO_STRATEGY_CERTIFICATION'
    if not actionable:return 'NO_PRE_FILL_EXECUTION_VALUE'
    return 'ELIGIBLE_FOR_SEPARATE_REVIEW' if predictive_support and economic_support else 'INCONCLUSIVE'


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('capture');c.add_argument('--runtime',required=True);c.add_argument('--ws',required=True);c.add_argument('--db',required=True);c.add_argument('--out',required=True)
    c=sub.add_parser('run');c.add_argument('--inputs',required=True);c.add_argument('--out',required=True)
    c=sub.add_parser('summarize');c.add_argument('--run',required=True);c.add_argument('--settlements')
    c=sub.add_parser('train');c.add_argument('--snapshots',required=True);c.add_argument('--labels',required=True);c.add_argument('--spec',required=True);c.add_argument('--out',required=True)
    a=p.parse_args()
    if a.command=='capture':
        from weather_model_evaluation.maker_risk_data import capture
        capture(a.runtime,a.ws,a.db,a.out)
    elif a.command=='summarize':
        from weather_model_evaluation.maker_risk_replay import summarize
        summarize(Path(a.run),Path(a.settlements) if a.settlements else None)
    elif a.command=='train':fit_models(list(rows(a.snapshots)),list(rows(a.labels)),json.load(open(a.spec)),a.out)
    else:
        from weather_model_evaluation.maker_risk_replay import run
        run(Path(a.inputs),Path(a.out))

if __name__=='__main__':main()
