"""Frozen raw adapters, deterministic shared-book reconstruction and finite replay."""
from __future__ import annotations
import bisect
import hashlib
import heapq
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from weather_model_evaluation.maker_risk_data import rows,dump,sha,epoch,utc,NEAR,CORE,CURRENT_QUOTE
from weather_model_evaluation.maker_risk import (M0,M1_EXTRA,AUTHORITY,OrderState,apply_order_event,
    evaluate_maker_risk,fit_models,counterfactual,date_bootstrap,TERMINAL)
from src.platform.market_data.ws_incremental_book import IncrementalBookReconstructor,BookReconstructionError


def write_rows(path, records):
    with Path(path).open('w') as f:
        for r in records:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')


def ident(order):
    return {'weather_model':order.get('artifact_hash'), 'model_version':order.get('model_version'),
            'quote_mode':order.get('quote_mode'),'execution_policy':order.get('execution_policy')}


def build_lifecycle(inputs):
    raw=list(rows(inputs/'live_orders.jsonl'));post={};events=defaultdict(list)
    for o in raw:
        if o.get('status')=='submitted' and o.get('venue_order_id') and o.get('maker_only'):
            oid=o['venue_order_id']
            if oid in post and (post[oid]['token_id'],post[oid]['size'])!=(o['token_id'],o['size']):raise ValueError('conflicting order identity')
            post.setdefault(oid,o)
    for oid,o in post.items():
        events[oid].append({'kind':'ack','at':o['created_at_utc'],'source_time':None,'available_at':o['created_at_utc'],
                            'status':'LIVE','authoritative':True,'matched':0,'source':o['_source']})
    trades=defaultdict(dict)
    for r in rows(inputs/'near_core_private_order_updates.jsonl'):
        d=r['payload'];oid=d.get('id')
        if d.get('event_type')=='trade':
            for leg in d.get('maker_orders',[]):
                if leg.get('order_id') in post:
                    trades[leg['order_id']].setdefault(d['id'],(d,leg,r['received_at_utc'],r['_source']))
        if oid not in post or d.get('event_type')!='order':continue
        source=float(d['timestamp'])/1000 if d.get('timestamp') else None
        events[oid].append({'kind':'private_order','at':r['received_at_utc'],'source_time':utc(source) if source else None,
                            'available_at':r['received_at_utc'],'status':d.get('status'),
                            'matched':float(d['size_matched']) if d.get('size_matched') is not None else None,
                            'authoritative':True,'source':r['_source']})
    attempts={};latencies=[]
    for r in rows(inputs/'execution_journal.jsonl'):
        d=r.get('payload',{});key=d.get('identity_key');t=r['recorded_at_utc']
        if d.get('kind')=='cancel' and r['event_type']=='attempt_before_side_effect':
            oid=d.get('source_order_id');attempts[key]=(oid,t)
            if oid in post:events[oid].append({'kind':'cancel_request','at':t,'available_at':t,'source_time':None,'safety_cancel_requested':True,'authoritative':False,'source':r['_source']})
        if d.get('kind')=='cancel' and r['event_type']=='outcome' and key in attempts:
            oid,before=attempts[key];response=d.get('raw_response',{});confirmed=oid in response.get('canceled',[])
            if oid in post:
                events[oid].append({'kind':'cancel_result','at':t,'available_at':t,'source_time':None,'status':'CANCELLED' if confirmed else 'UNKNOWN',
                                    'authoritative':confirmed,'response_status':d.get('status'),'failure_reason':response.get('not_canceled',{}).get(oid),'source':r['_source']})
                if confirmed:latencies.append({'order_id':oid,'target_date':post[oid]['target_date'],'request_at':before,'confirmed_at':t,'delay_sec':epoch(t)-epoch(before)})
    facts=defaultdict(list)
    for f in rows(inputs/'facts.jsonl'):
        if f['order_id'] not in post:continue
        o=post[f['order_id']]
        for k in ('token_id','city','target_date','bracket'):
            if str(f[k])!=str(o[k]):raise ValueError('canonical/raw key mismatch '+k)
        f=dict(f);f['observed_strategy_instance']=o['strategy_instance'];f['fill_clock_verified']=False
        matches=[(d,leg,received,ref) for d,leg,received,ref in trades[f['order_id']].values()
                 if d.get('transaction_hash') and d.get('transaction_hash')==f.get('fee_transaction_hash')
                 and d.get('market')==f['condition_id'] and leg.get('asset_id')==f['token_id']
                 and float(leg.get('matched_amount',-1))==f['fill_qty'] and float(leg.get('price',-1))==f['fill_price']]
        f['canonical_fill_ts_utc']=f['fill_ts_utc']
        if len(matches)==1 and matches[0][0].get('match_time'):
            d,leg,received,ref=matches[0];f['fill_clock_verified']=True
            f['fill_ts_utc']=utc(float(d['match_time']));f['fill_received_at']=received
            f['private_trade_id']=d['id'];f['private_trade_source']=ref
            f['fill_clock_resolution_sec']=1.;f['clock_offset_unknown']=True
        # Exact transaction / token / condition / price / quantity match; no canonical UPDATE.
        facts[f['order_id']].append(f)
        events[f['order_id']].append({'kind':'fill_label','at':f['fill_ts_utc'],'fill_id':f['fill_id'],'fill_qty':f['fill_qty'],
            'fill_price':f['fill_price'],'fees_usd':f['fees_usd'],'contract_won':f['contract_won'],
            'label_known_at':f.get('fact_built_at_utc'),'fill_clock_verified':f['fill_clock_verified'],
            'canonical_fill_ts_utc':f['canonical_fill_ts_utc'],'fill_received_at':f.get('fill_received_at'),'private_trade_id':f.get('private_trade_id'),'source':'frozen canonical fact_trades'})
    for oid,fs in facts.items():
        if len({f['fill_id'] for f in fs})!=len(fs):raise ValueError('duplicate fill')
        if sum(f['fill_qty'] for f in fs)>float(post[oid]['size'])+1e-6:raise ValueError('fill cap exceeded')
    return raw,post,events,facts,latencies


def book_queries(inputs, post, query_by_token):
    """Apply exact raw deltas, epoch resets and shared parity checks before each query.

    A quiet book remains usable inside an evidenced epoch (no arbitrary age cap).
    Queries beyond its final archived frame have unknown transport continuity.
    Exchange sequence absence stays an explicit evidence limitation.
    """
    tokens=set(query_by_token);epochs={};frames_paths=sorted(inputs.glob('frames_*.jsonl'))
    for path in sorted(inputs.glob('epochs_*.jsonl')):
        for r in rows(path):
            k=r['subscription_epoch_id']
            v={key:r.get(key) for key in ['subscription_epoch_id','previous_subscription_epoch_id','started_at_utc','reason','producer_build_id','selector_version','capture_policy','token_ids','token_rows']}
            if k in epochs and epochs[k]!=v:raise ValueError('conflicting epoch')
            epochs[k]=v
    last=defaultdict(float);frame_count=0
    for path in frames_paths:
        for f in rows(path):
            last[f['subscription_epoch_id']]=max(last[f['subscription_epoch_id']],epoch(f['received_at_utc']));frame_count+=1
    def frame_events(path):
        for r in rows(path):yield (epoch(r['received_at_utc']),1,r['_source']['path']+str(r['_source']['line']),r)
    epoch_events=iter(sorted((epoch(r['started_at_utc']),0,k,r) for k,r in epochs.items()))
    query_events=iter(sorted((t,2,token,{'token_id':token}) for token,ts in query_by_token.items() for t in ts))
    merged=heapq.merge(epoch_events,query_events,*(frame_events(p) for p in frames_paths),key=lambda x:x[:3])
    engine=IncrementalBookReconstructor(strict_best_parity=True);generation=Counter();books={};errors=Counter();previous=None
    tick_events=defaultdict(list)
    for o in post.values():
        if o.get('quote_tick_size') and float(o['quote_tick_size'])>0:tick_events[o['token_id']].append((epoch(o['created_at_utc']),float(o['quote_tick_size'])))
    for t,kind,key,r in merged:
        if kind==0:
            carry=r['reason']=='selector_reconcile' and previous==r['previous_subscription_epoch_id'] and previous is not None
            next_tokens=set(r['token_ids'] or [])&tokens
            for token in tokens:
                if not carry or token not in next_tokens:generation[token]+=1
            engine.activate_epoch(r['subscription_epoch_id'],r['token_ids'] or [],carry_forward=carry,
                                  producer_build_id=r['producer_build_id'],selector_version=r['selector_version'],
                                  capture_policy=r['capture_policy'],token_rows=r['token_rows'])
            previous=r['subscription_epoch_id']
        elif kind==1:
            if r.get('subscription_epoch_id')!=engine.epoch_id:
                errors['undeclared_or_out_of_epoch_frame']+=1;continue
            r['raw_archive_path']=r['_source']['path'];r['raw_line_number']=r['_source']['line']
            before=dict(engine.blocked_tokens)
            try:engine.apply_envelope(r)
            except BookReconstructionError as e:errors[str(e).split(':')[0]]+=1
            for token,reason in engine.blocked_tokens.items():
                if before.get(token)!=reason and reason!='best_quote_parity_pending':generation[token]+=1
            messages=r.get('message');messages=messages if isinstance(messages,list) else [messages]
            for m in messages:
                if isinstance(m,dict) and m.get('event_type')=='tick_size_change' and m.get('asset_id') in tokens:
                    tick_events[m['asset_id']].append((t,float(m['new_tick_size'])))
        else:
            token=r['token_id'];base={'at':utc(t),'valid':False,'reason':None,'generation':generation[token]}
            try:
                if token not in engine.token_ids:raise ValueError('token_not_subscribed')
                if t>last[engine.epoch_id]:raise ValueError('transport_continuity_unproven_after_last_epoch_frame')
                b=engine.snapshot(token,observed_at_utc=utc(t),requested_shares=5)
                if b.best_bid is None or b.best_ask is None or b.best_bid>=b.best_ask:raise ValueError('invalid_two_sided_book')
                known_ticks=[x for x in tick_events[token] if x[0]<=t];tick=max(known_ticks,key=lambda x:x[0])[1] if known_ticks else None
                base.update(valid=True,bid=b.best_bid,ask=b.best_ask,bid_qty=b.best_bid_size,ask_qty=b.best_ask_size,
                            bids3=b.bids[:3],asks3=b.asks[:3],bid5_proceeds=b.sell_proceeds,tick=tick,
                            available_at=b.last_frame_received_at_utc,source_at=utc(b.exchange_ts_ms/1000) if b.exchange_ts_ms else None,
                            snapshot_id=b.book_snapshot_id,baseline_ref=b.baseline_raw_frame_ref.to_dict() if hasattr(b.baseline_raw_frame_ref,'to_dict') else vars(b.baseline_raw_frame_ref),
                            sequence_status=b.sequence_status,epoch_id=b.subscription_epoch_id,delta_chain_hash=b.delta_chain_hash)
            except (ValueError,BookReconstructionError) as e:base['reason']=str(e)
            books[(token,t)]=base
    return books,{'frames':frame_count,'epochs':len(epochs),'reconstruction_errors':dict(errors),
                  'sequence_boundary':'Exchange sequence unavailable; parity/epoch/receive ordering checked, not lossless exchange proof'}


def label_state_valid(current, future, decision_epoch, horizon):
    """Label is an as-of state, not the next future-arriving snapshot.

    The reconstructor consumes frames in receive order. This explicit boundary
    also rejects an accidentally supplied delayed-source/future-receive state.
    """
    if not current.get('valid') or not future.get('valid') or not current.get('tick'):
        return False
    if not current.get('available_at') or not future.get('available_at'):
        return False
    return (epoch(current['available_at'])<=decision_epoch and
            epoch(future['available_at'])<=decision_epoch+horizon and
            current['generation']==future['generation'] and
            current['tick']==future.get('tick'))


def run(inputs, out):
    out.mkdir(parents=True,exist_ok=False)
    raw,post,events,facts,latencies=build_lifecycle(inputs)
    ledger=list(rows(inputs/'near_core_maker_v2_ledger.jsonl'))
    resc=list(rows(inputs/'continuous_market_rescores.jsonl'));res_by_id={r.get('market_rescore_id'):r for r in resc}
    bytoken=defaultdict(list)
    # Recover omitted order model identity only through original plan -> ledger -> rescore.
    byplan=defaultdict(list)
    for record in ledger:
        if record.get('plan_id') and record.get('market_rescore_id') in res_by_id:
            byplan[record['plan_id']].append((record,res_by_id[record['market_rescore_id']]))
    model_repairs=[]
    for order in post.values():
        if order.get('artifact_hash'):continue
        matches=[]
        for record,score in byplan[order.get('plan_id')]:
            if (record.get('signal_id')==order.get('signal_id') and
                score.get('current_yes_token_id')==order.get('token_id') and
                score.get('model_version')==order.get('model_version') and
                epoch(score['created_at_utc'])<=epoch(order['created_at_utc']) and
                score.get('model_probability_hold') is not None and
                abs(float(score['model_probability_hold'])-float(order['model_token_probability']))<1e-11):
                matches.append(score)
        hashes={r.get('artifact_hash') for r in matches if r.get('artifact_hash')}
        if len(hashes)==1:
            order['artifact_hash']=next(iter(hashes));order['_model_identity_repaired']=True
            model_repairs.append({'order_id':order['venue_order_id'],'plan_id':order['plan_id'],'model_hash':order['artifact_hash'],
                                  'evidence':[r['_source'] for r in matches],'join':'plan_id + signal_id + token_id + model_version + exact rounded probability + prior clock'})
    dump(out/'MODEL_IDENTITY_REPAIRS.json',model_repairs)
    for r in resc:
        clock=[epoch(r.get(k)) for k in ('created_at_utc','current_yes_book_parsed_at_utc','snapshot_available_at_utc') if r.get(k)]
        if clock:
            r['_available']=max(clock);bytoken[r.get('current_yes_token_id')].append(r)
    for values in bytoken.values():values.sort(key=lambda r:r['_available'])
    grouped=defaultdict(list)
    for r in ledger:grouped[(NEAR,r['signal_id'])].append(r)
    # Ordinary maker is external diagnostic only; use original signal, never its future fills.
    for o in post.values():
        key=(o['strategy_instance'],o['signal_id'])
        if key not in grouped:grouped[key]=[]
    opportunities=[];opid={};post_by_key=defaultdict(list)
    for o in post.values():post_by_key[(o['strategy_instance'],o['signal_id'])].append(o)
    for key,ls in sorted(grouped.items()):
        os=sorted(post_by_key[key],key=lambda x:epoch(x['created_at_utc']));ls.sort(key=lambda x:epoch(x['created_at_utc']))
        first=ls[0] if ls else os[0];oid='op-'+hashlib.sha256('|'.join(key).encode()).hexdigest()[:24];opid[key]=oid
        rr=res_by_id.get(first.get('market_rescore_id'),{});o=os[0] if os else {}
        token=o.get('token_id') or rr.get('current_yes_token_id');condition=rr.get('current_condition_id') or next((f['condition_id'] for order in os for f in facts[order['venue_order_id']]),None)
        if not condition:condition=o.get('condition_id')
        quality=[]
        if not token:quality.append('token_unknown_at_candidate')
        if not condition:quality.append('condition_unknown')
        roots={x.get('root_order_id') or x['venue_order_id'] for x in os}
        if len(roots)>1:quality.append('multiple_roots_one_signal')
        opportunities.append({'opportunity_id':oid,'raw_signal_id':key[1],'strategy_instance':key[0],
            'city':first['city'],'target_date':first['target_date'],'registered_at':first['created_at_utc'],
            'checkpoint_id':first.get('checkpoint_key'),'token_id':token,'condition_id':condition,
            'bracket':o.get('bracket') or rr.get('current_bracket'),'rule_version':'exact_bracket_yes',
            'identity':ident(o) if o else {'weather_model':rr.get('artifact_hash'),'model_version':rr.get('model_version'),'quote_mode':None,'execution_policy':None},
            'initial_p':first.get('model_probability_hold',o.get('model_token_probability')),
            'initial_limit':o.get('posted_price'),'observed_shares':o.get('size',first.get('fixed_shares')),
            'decision_rows':len(ls),'qualification':first.get('eligible_checkpoint'),'risk_results':dict(Counter(str(x.get('reason') or 'unspecified') for x in ls)),
            'posted':bool(os),'physical_orders':[x['venue_order_id'] for x in os],'roots':sorted(roots),
            'dispatch_state':'posted' if os else 'unknown' if any(x.get('plan_id') for x in ls) else 'confirmed_no_generated_plan_in_registered_ledger',
            'quality':quality,'raw_refs':[first['_source']]})
    near_posts=[o for o in post.values() if o['strategy_instance']==NEAR and o['quote_mode']==CURRENT_QUOTE]
    primary=ident(sorted(near_posts,key=lambda o:epoch(o['created_at_utc']))[-1])
    all_dates=sorted({o['target_date'] for o in opportunities});cut1=int(len(all_dates)*.6);cut2=int(len(all_dates)*.8)
    spec={'date_split':{'train':all_dates[:cut1],'validation':all_dates[cut1:cut2],'evaluation':all_dates[cut2:]},
          'research_source_hashes':{p.name:sha(p) for p in Path(__file__).parent.glob('maker_risk*.py')},
          'shared_book_reconstructor_sha256':sha(Path(__file__).parents[1]/'src/platform/market_data/ws_incremental_book.py'),
          'task_id':'core-maker-risk-v1-20260916','identity':primary,'m0':list(M0),'m1_extra':list(M1_EXTRA),
          'excluded_feature':{'net_order_flow_30s':'quote-level updates do not identify executions versus cancellations; no net-flow attribution'},
          'step_sec':5,'horizons':[30,120,300],'primary_label':'mid(t+30)-mid(t) <= -tick(t)',
          'purge_sec':300,'C':1.0,'solver':'lbfgs','standardization':'weighted training only','class_weight':None,
          'threshold_quantile':.9,'threshold_min_roots':10,'bootstrap':{'replicates':20000,'seed':20260916,'unit':'target_date all cities together'},
          'authority':AUTHORITY,'synthetic':False,'own_book_effect':'not reliably subtractable; recorded boundary, no external-alpha certification',
          'denominator_rule':'strategy_instance + original near signal_id (city,target_date,checkpoint_key hash); repeated rescoring and fills do not add opportunity',
          'fee_basis':'historical posted exchange estimated maker fee / size with fee_identity; actual fill fees for economics',
          'history_status':'all history is development, no unseen final holdout'}
    dump(out/'FROZEN_SPEC.json',spec) # before fit or outcome-based feature changes
    queries=defaultdict(set);grids={};bounds={}
    for oid,o in post.items():
        start=epoch(o['created_at_utc']);ev=events[oid]
        ends=[epoch(e['at']) for e in ev if e['kind']=='cancel_request' or (e.get('authoritative') and str(e.get('status','')).upper() in TERMINAL)]
        deadline=epoch(o.get('maker_lifecycle_deadline_utc') or o.get('expires_at_utc'))
        # Unknown tails are retained as a coverage blocker, never invented resting samples.
        end=min(ends) if ends else deadline
        bounds[oid]={'start':start,'end':end,'terminal_or_safety_observed':bool(ends)}
        times=[] if end is None else list(range(math.ceil(start/5)*5,math.floor(end/5)*5+1,5))
        grids[oid]=times
        for t in times:
            queries[o['token_id']].update([t,t-30,t+30,t+120,t+300])
    books,book_audit=book_queries(inputs,post,queries);dump(out/'BOOK_AUDIT.json',book_audit)
    snaps=[];labels=[];event_rows=[];audit=[]
    for oid,o in sorted(post.items()):
        opportunity_id=opid[(o['strategy_instance'],o['signal_id'])];root=o.get('root_order_id') or oid;tok=o['token_id']
        ev=sorted([e for e in events[oid] if e['kind']!='fill_label'],key=lambda e:epoch(e['at']));state=OrderState(opportunity_id,float(o['size']));j=0
        scores=bytoken[tok];score_times=[r['_available'] for r in scores]
        exchange=o.get('exchange_response',{});estimated_fee=exchange.get('estimated_fee_usd')
        fee_ok=estimated_fee is not None and bool(exchange.get('fee_identity'))
        maker_fee=float(estimated_fee)/float(o['size']) if fee_ok else None
        for e in events[oid]:event_rows.append({'opportunity_id':opportunity_id,'root_order_id':root,'physical_order_id':oid,**e})
        fs=facts[oid]
        for t in grids[oid]:
            while j<len(ev) and epoch(ev[j]['at'])<=t:state=apply_order_event(state,ev[j]);j+=1
            i=bisect.bisect_right(score_times,t)-1;r=scores[i] if i>=0 else {}
            match_identity=r.get('artifact_hash')==o.get('artifact_hash') and r.get('model_version')==o.get('model_version') and r.get('target_date')==o['target_date']
            p=r.get('model_probability_hold');p_ok=match_identity and p is not None and 0<float(p)<1
            b=books.get((tok,t),{});past=books.get((tok,t-30),{});tick=b.get('tick');features={k:None for k in (*M0,*M1_EXTRA)}
            available={'probability':utc(r['_available']) if r else None,'posted_order':o['created_at_utc']}
            if p_ok:
                hour=datetime.fromtimestamp(t,ZoneInfo(r['timezone'])).hour+datetime.fromtimestamp(t,ZoneInfo(r['timezone'])).minute/60 if r.get('timezone') else None
                features.update(p_logit=math.log(p/(1-p)),edge=p-float(o['posted_price'])-maker_fee if fee_ok else None,
                                hour_sin=math.sin(2*math.pi*hour/24) if hour is not None else None,hour_cos=math.cos(2*math.pi*hour/24) if hour is not None else None,
                                order_age_sec=t-bounds[oid]['start'],weather_age_sec=t-epoch(r['source_report_ts_utc']) if r.get('source_report_ts_utc') else None)
            if b.get('valid') and tick:
                qty=b['bid_qty']+b['ask_qty'];available['book']=b['available_at']
                features.update(spread_ticks=(b['ask']-b['bid'])/tick,top_imbalance=(b['bid_qty']-b['ask_qty'])/qty if qty>0 else None,
                                log_bid3=math.log1p(sum(x[1] for x in b['bids3'])),log_ask3=math.log1p(sum(x[1] for x in b['asks3'])))
                if past.get('valid') and past['generation']==b['generation']:
                    features.update(bid_change30_ticks=(b['bid']-past['bid'])/tick,ask_change30_ticks=(b['ask']-past['ask'])/tick)
            sid=hashlib.sha256(f'{opportunity_id}|{oid}|{t}'.encode()).hexdigest()
            resting=not state.terminal and not state.safety_cancel and bounds[oid]['terminal_or_safety_observed']
            s={'snapshot_id':sid,'opportunity_id':opportunity_id,'root_order_id':root,'physical_order_id':oid,'target_date':o['target_date'],'city':o['city'],
                'decision_at':utc(t),'identity':ident(o),'features':features,'input_available_at':available,'pit_valid':p_ok,
                'probability_reliable':p_ok,'fee_confirmed':fee_ok,'book_valid':bool(b.get('valid')),'resting_confirmed':resting,
                'quantity':state.quantity,'matched':state.matched,'remaining':max(0,state.quantity-state.matched),'terminal':state.terminal,
                'safety_cancel':state.safety_cancel,'book_snapshot_id':b.get('snapshot_id'),'book_reason':b.get('reason'),
                'quote_tick':tick,'working_limit':o['posted_price'],'probability':p if p_ok else None,'probability_source':r.get('_source'),
                'clock_uncertainty_sec':None,'clock_uncertainty_status':'cross-system offset unmeasured; local ordering preserved',
                'four_clocks':{'source_book_event':b.get('source_at'),'local_book_received':b.get('available_at'),'strategy_score':utc(t),'original_ack_received':o['created_at_utc']}}
            snaps.append(s)
            label={'snapshot_id':sid,'label_known_at':None,'condition_id':next((f['condition_id'] for f in fs),o.get('condition_id')),
                   'token_id':tok,'city':o['city'],'target_date':o['target_date'],'bracket':o['bracket'],'rule_version':'exact_bracket_yes'}
            for h in (30,120,300):
                future=books.get((tok,t+h),{});valid=label_state_valid(b,future,t,h)
                label['y'+str(h)]=int(((future['bid']+future['ask'])-(b['bid']+b['ask']))/2<=-tick+1e-12) if valid else None
                label['bid_change'+str(h)]=future['bid']-b['bid'] if valid else None
                label['spread_change'+str(h)]=(future['ask']-future['bid'])-(b['ask']-b['bid']) if valid else None
                label['bid5_proceeds'+str(h)]=future.get('bid5_proceeds') if valid else None
                if h==30 and valid:label['label_known_at']=utc(max(t+30,epoch(future['available_at'])))
            labels.append(label)
        audit.append({'physical_order_id':oid,'opportunity_id':opportunity_id,'model_p_yes_used_placeholder':o.get('model_p_yes_used'),
                      'correct_raw_probability':o.get('model_token_probability'),'canonical_probabilities':[f.get('model_p_yes') for f in fs],
                      'requested_price':o.get('requested_price'),'final_posted_price':o.get('posted_price'),
                      'quote_mode':o['quote_mode'],'cancel_reason_on_ack':o.get('cancel_reason'),'ack_child_role':o.get('child_order_role'),
                      'interpretation':'probability uses model_token_probability/rescore, not zero aliases; cancel_reason on ack is planned safety trigger, not executed cancellation',
                      'terminal_known':bounds[oid]['terminal_or_safety_observed']})
    write_rows(out/'opportunities.jsonl',opportunities);write_rows(out/'decision_snapshots.jsonl',snaps)
    write_rows(out/'order_events_and_labels.jsonl',event_rows);write_rows(out/'market_labels.jsonl',labels);write_rows(out/'IDENTITY_DISPOSITION.jsonl',audit)
    dump(out/'LATENCY_OBSERVATIONS.json',latencies)
    write_rows(out/'fill_clock_linkage.jsonl',[f for fs in facts.values() for f in fs])
    coverage={'opportunities':len(opportunities),'near_opportunities':sum(o['strategy_instance']==NEAR for o in opportunities),
              'registered_candidate_rows':len(ledger),'posted_physical_orders':len(post),'near_posted':sum(o['strategy_instance']==NEAR for o in post.values()),
              'quote_modes':dict(Counter(o.get('quote_mode') for o in post.values())),'snapshots':len(snaps),
              'resting_confirmed':sum(s['resting_confirmed'] for s in snaps),'pit_probability':sum(s['pit_valid'] for s in snaps),
              'book_valid':sum(s['book_valid'] for s in snaps),'labels30':sum(l['y30'] is not None for l in labels),
              'filled_orders':len([k for k,v in facts.items() if v]),'fill_rows':sum(map(len,facts.values())),
              'confirmed_cancel_delays':len(latencies),'unknown_dispatch':sum(o['dispatch_state']=='unknown' for o in opportunities),
              'candidate_universe_boundary':'near ledger registered candidates only; upstream all-market eligible/not-eligible funnel not fully persisted here',
              'by_date':{d:{'opportunities':sum(o['target_date']==d for o in opportunities),'snapshots':sum(s['target_date']==d for s in snaps),'labels30':sum(l['target_date']==d and l['y30'] is not None for l in labels)} for d in sorted({o['target_date'] for o in opportunities})}}
    dump(out/'COVERAGE.json',coverage)
    fit,models,thresholds,predictions=fit_models(snaps,labels,spec,out/'training')
    training_dates=fit.get('split',{}).get('train',[])
    import numpy as np
    delays=[r['delay_sec'] for r in latencies if r['target_date'] in training_dates and r['delay_sec']>=0]
    frozen_latency={'median':float(np.median(delays)) if delays else None,'p95':float(np.quantile(delays,.95)) if delays else None,
                    'training_dates':training_dates,'n':len(delays),'scope':'local attempt-before-side-effect to successful cancel response; no guarantee of counterfactual success'}
    dump(out/'FROZEN_LATENCY.json',frozen_latency)
    decisions=[];alerts={};states={};opp_economics=[]
    eval_dates=set(fit.get('split',{}).get('evaluation',[]))
    for s in sorted(snaps,key=lambda r:(r['decision_at'],r['physical_order_id'])):
        if s['target_date'] not in eval_dates:continue
        for arm,m in [('B1',None),('B2','M1'),('M0_budget','M0')]:
            key=(arm,s['opportunity_id'])
            state=states.get(key,OrderState(s['opportunity_id'],s['quantity']))
            state=replace_state(state,s)
            policy={'authority':AUTHORITY,'identity':primary,'arm':arm,'threshold':thresholds[m]['threshold'] if m else None}
            decision,state=evaluate_maker_risk(s,state,models.get(m),policy);states[key]=state
            decision.update(arm=arm,snapshot_id=s['snapshot_id'],physical_order_id=s['physical_order_id'],target_date=s['target_date']);decisions.append(decision)
            if decision['action'].startswith('WOULD_CANCEL'):alerts.setdefault(key,decision)
    for o in opportunities:
        fs=[f for oid in o['physical_orders'] for f in facts[oid]]
        row={'opportunity_id':o['opportunity_id'],'target_date':o['target_date'],'strategy_instance':o['strategy_instance'],
             'scope':'evaluation' if o['target_date'] in eval_dates else 'outside_evaluation','baseline_fill_rows':len(fs),
             'baseline_settled_net':sum(f['pnl_usd_at_fill'] for f in fs if f.get('settled')) if all(f.get('settled') for f in fs) and o['dispatch_state']!='unknown' else None,
             'unknown_settlement':sum(not f.get('settled') for f in fs),'dispatch_state':o['dispatch_state']}
        for arm in ('B1','B2','M0_budget'):
            a=alerts.get((arm,o['opportunity_id']))
            eligible=o['target_date'] in eval_dates and o['identity']==primary
            no_trade=o['dispatch_state']=='confirmed_no_generated_plan_in_registered_ledger'
            if no_trade and fs:raise ValueError('no-trade opportunity has fills')
            row[arm]={'status':'ACTUAL_NO_GENERATED_ORDER','delta':0} if no_trade else {'status':'NOT_EVALUABLE','delta':None} if not eligible else counterfactual(fs,a['decision_at'] if a else None,frozen_latency['p95'],None,added_cost=None)
            row[arm]['alert_at']=a['decision_at'] if a else None
        opp_economics.append(row)
    write_rows(out/'decisions.jsonl',decisions);write_rows(out/'opportunity_ledger.jsonl',opp_economics)
    daily=[]
    for d in sorted({o['target_date'] for o in opportunities}):
        os=[r for r in opp_economics if r['target_date']==d and r['strategy_instance']==NEAR]
        daily.append({'target_date':d,'registered_near_opportunities':len(os),'B2_unknown':sum(r['B2']['delta'] is None for r in os),
                      'B2_known_delta':sum(r['B2']['delta'] for r in os if r['B2']['delta'] is not None),
                      'full_denominator_delta':None if any(r['B2']['delta'] is None for r in os) else sum(r['B2']['delta'] for r in os)/len(os)})
    write_rows(out/'daily.jsonl',daily)
    dump(out/'RESULT.json',{'stage':'DATA_BLOCKED' if fit['status']=='BLOCKED' else 'DEVELOPMENT_ONLY',
                          'fit_status':fit['status'],'economic_status':'counterfactual_diagnostic_unknown_clock_or_coverage',
                          'thresholds':thresholds,'frozen_latency':frozen_latency,'decision_counts':dict(Counter(d['action'] for d in decisions)),
                          'production_changes':0,'authority':AUTHORITY})
    print(json.dumps({'coverage':coverage,'fit':fit,'thresholds':thresholds},ensure_ascii=False,indent=2))


def replace_state(state,s):
    from dataclasses import replace
    return replace(state,matched=max(state.matched,s['matched']),terminal=state.terminal or s['terminal'],safety_cancel=state.safety_cancel or s['safety_cancel'])


def summarize(out, settlement_path=None):
    """Coverage and economic limitations on the fixed registered denominator."""
    out=Path(out);spec=json.loads((out/'FROZEN_SPEC.json').read_text())
    opportunities=list(rows(out/'opportunities.jsonl'));snapshots=list(rows(out/'decision_snapshots.jsonl'))
    market_labels={r['snapshot_id']:r for r in rows(out/'market_labels.jsonl')}
    predictions=list(rows(out/'training/predictions.jsonl'));ledger=json.loads((out/'training/FIT_LEDGER.json').read_text())
    decisions=list(rows(out/'decisions.jsonl'));fills=list(rows(out/'fill_clock_linkage.jsonl'))
    final_labels=[];settlements=defaultdict(list)
    if settlement_path:
        for r in rows(settlement_path):
            settlements[(r['condition_id'],r['token_id'],r['target_date'],str(r['bracket']))].append(r)
    for o in opportunities:
        key=(o['condition_id'],o['token_id'],o['target_date'],str(o['bracket']));matches=settlements[key]
        outcomes={r['final_price'] for r in matches if r['settlement_status']=='settled' and r['final_price'] in (0,1)}
        final_labels.append({'opportunity_id':o['opportunity_id'],'condition_id':key[0],'token_id':key[1],
            'city':o['city'],'target_date':key[2],'bracket':key[3],'rule_version':'exact_bracket_yes',
            'final_yes':next(iter(outcomes)) if len(outcomes)==1 else None,
            'label_known_at':json.loads((Path(settlement_path).parent/'MANIFEST.json').read_text())['observed_at'] if matches and settlement_path else None,
            'source_created_at':max((r['created_at_utc'] for r in matches),default=None),
            'label_source_hash':sha(settlement_path) if settlement_path else None,
            'status':'canonical_key_join' if len(outcomes)==1 else 'unknown_or_conflicting','source_rows':len(matches)})
    write_rows(out/'settlement_labels.jsonl',final_labels)
    final_byopp={r['opportunity_id']:r['final_yes'] for r in final_labels}
    pids={r['snapshot_id'] for r in predictions};train_dates=ledger.get('split',{}).get('train',[])
    from weather_model_evaluation.maker_risk import checked_features
    exclusion=[];usable=[]
    for s in snapshots:
        reason=None
        try:
            if s['identity']!=spec['identity']:raise ValueError('identity_not_primary')
            if not s['resting_confirmed']:raise ValueError('not_confirmed_resting')
            if not s['book_valid']:raise ValueError('book_gap_or_unconfirmed')
            checked_features(s,M0+M1_EXTRA)
        except ValueError as e:reason=str(e)
        row={'snapshot_id':s['snapshot_id'],'opportunity_id':s['opportunity_id'],'target_date':s['target_date'],
             'final_yes':final_byopp.get(s['opportunity_id']),'reason':reason,'y30':market_labels[s['snapshot_id']]['y30']}
        (exclusion if reason else usable).append(row)
    write_rows(out/'excluded_snapshots.jsonl',exclusion)
    class_by_date=[]
    for d in sorted({o['target_date'] for o in opportunities}):
        ss=[r for r in usable if r['target_date']==d]
        class_by_date.append({'target_date':d,'common_scorable':len(ss),'positive30':sum(r['y30']==1 for r in ss),
                             'negative30':sum(r['y30']==0 for r in ss),'unknown30':sum(r['y30'] is None for r in ss),
                             'opportunities':len({r['opportunity_id'] for r in ss})})
    write_rows(out/'class_by_date.jsonl',class_by_date)
    missing_distribution=dict(Counter(str(r['final_yes']) for r in exclusion))
    near=[o for o in opportunities if o['strategy_instance']==NEAR]
    evaluation_dates=ledger.get('split',{}).get('evaluation',[])
    economic_rows=list(rows(out/'opportunity_ledger.jsonl'))
    economic_eval=[r for r in economic_rows if r['strategy_instance']==NEAR and r['target_date'] in evaluation_dates]
    b1_known=[r for r in economic_eval if r['B1']['delta'] is not None]
    # Model thresholds unavailable means no B2 risk experiment; equality of abstaining
    # software branches is not evidence of zero risk-policy effect.
    thresholds=json.loads((out/'training/THRESHOLDS.json').read_text())
    economic={'kind':'counterfactual_diagnostic','registered_N':len(near),'evaluation_N':len(economic_eval),
        'B1_minus_B0':{'known_opportunities':len(b1_known),'unknown_opportunities':len(economic_eval)-len(b1_known),
                      'known_sum':sum(r['B1']['delta'] for r in b1_known),
                      'per_all_evaluation_opportunity':None if len(b1_known)!=len(economic_eval) or not economic_eval else sum(r['B1']['delta'] for r in b1_known)/len(economic_eval)},
        'B2_minus_B1':None,'B2_minus_B0':None,'M0_budget_comparison':None,
        'B2_reason':thresholds['M1']['status'],'M0_budget_reason':thresholds['M0']['status'],
        'avoided_losing_fill_quantity':0,'avoided_loss':0,'forgone_winning_fill_quantity':0,'forgone_winner_profit':0,
        'meaning_of_zero':'no evaluation WOULD_CANCEL signals; no profitable intervention demonstrated',
        'cancel_direct_fee':'historical cancel request/response has no charged-fee evidence; do not silently impute infrastructure cost',
        'infrastructure_cost':None,'clock_offset':'unmeasured; no unconditional executability assertion',
        'same_capital_return_and_account_drawdown':'not estimated'}
    lead_rows=[]
    for d in decisions:
        if not d['action'].startswith('WOULD_CANCEL'):continue
        ff=[f for f in fills if f['order_id']==d['physical_order_id']]
        for f in ff:
            lead_rows.append({'opportunity_id':d['opportunity_id'],'arm':d['arm'],'fill_id':f['fill_id'],
                'alert_at':d['decision_at'],'fill_source_at':f['fill_ts_utc'],'fill_receive_at':f.get('fill_received_at'),
                'source_lead_sec':epoch(f['fill_ts_utc'])-epoch(d['decision_at']) if f['fill_clock_verified'] else None,
                'local_receive_lead_sec':epoch(f['fill_received_at'])-epoch(d['decision_at']) if f.get('fill_received_at') else None,
                'clock_offset_unknown':True})
    write_rows(out/'action_leads.jsonl',lead_rows)
    # Fixed economic B1 also has an explicitly development-only descriptive replay.
    dev=[];state_by_opp={}
    for s in sorted(snapshots,key=lambda s:(s['decision_at'],s['physical_order_id'])):
        if s['target_date'] in evaluation_dates:continue
        st=state_by_opp.get(s['opportunity_id'],OrderState(s['opportunity_id'],s['quantity']));st=replace_state(st,s)
        d,st=evaluate_maker_risk(s,st,None,{'authority':AUTHORITY,'identity':spec['identity'],'arm':'B1','threshold':None})
        state_by_opp[s['opportunity_id']]=st
        if d['action']=='WOULD_CANCEL_ECONOMIC':
            ff=[f for f in fills if f['order_id']==s['physical_order_id']]
            dev.append({**d,'physical_order_id':s['physical_order_id'],'city':s['city'],'target_date':s['target_date'],
                        'scope':'development_only','real_fills':len(ff),'counterfactual_delta':None if ff else 0})
    write_rows(out/'B1_development_alerts.jsonl',dev)
    # Exactly three requested types; unavailable types are stated, never handpicked.
    cases=[{'type':kind,'selection_rule':'earliest evaluation alert in type, tie by opportunity_id','case':None,
            'reason':'no evaluation alert; B2 threshold unidentifiable'} for kind in ('possible_bad_fill_avoided','winner_forfeited','too_late')]
    dump(out/'CASES.json',cases);dump(out/'ECONOMIC_DIAGNOSTIC.json',economic)
    dump(out/'ACCEPTANCE.json',{'engineering':'implemented_and_testable_pending_independent_review',
        'prediction':'no_supported_increment' if ledger['status']=='FITTED' else 'data_blocked',
        'actionability':'no_evaluation_alert_to_assess','economics':'unidentified',
        'recommendation':'STOP_CURRENT_V1_EXPRESSION_PRESERVE_DORMANT_CODE',
        'reopen_condition':'new separately approved bounded evidence, valid threshold and complete candidate identities; no automatic live or collector',
        'scorable_snapshots':len(usable),'excluded_snapshots':len(exclusion),'excluded_fraction':len(exclusion)/len(snapshots) if snapshots else None,
        'excluded_final_outcome_distribution':missing_distribution,'settlement_known_opportunities':sum(r['final_yes'] is not None for r in final_labels),
        'synthetic_is_only_software_verification':True})
