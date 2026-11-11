#!/usr/bin/env python3
"""B4 v2c REVIEW ONLY: R05 VM100/101 + passive VM121/VM130 signals.

No installation, timer, admin API, source changes, remote file writes, secrets,
per-user rows or network probes to customer VPN destinations.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

import b4_capture as base

SCHEMA='wg-paid-b4-diagnostics-v2c-review-1'
CAP_ROOT=Path('/opt/router-ops/state/resource-monitor')
CAP_MAX=256*1024
UTC=timezone.utc
VM121_SQL=r'''BEGIN READ ONLY;
SET LOCAL statement_timeout = '5000ms';
SELECT jsonb_build_object(
 'schema','b4v2c-vm121-runtime-v1',
 'observed_at',to_char(now() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
 'last_minute',(SELECT jsonb_build_object(
  'minute_utc',to_char(minute_utc AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'last_received_at',to_char(last_received_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'coverage_status',coverage_status,'coverage_ratio',coverage_ratio,
  'active_configurations_peak',active_configurations_peak,
  'active_users_peak',active_users_peak,'selector_peak',selector_peak,
  'conflicted_slots_total',conflicted_slots_total,
  'unmatched_rows_total',unmatched_rows_total)
  FROM public.runtime_activity_minute ORDER BY minute_utc DESC LIMIT 1),
 'window24',(SELECT jsonb_build_object(
  'observed_minutes',count(*),
  'complete_minutes',count(*) FILTER (WHERE coverage_status='complete'),
  'partial_minutes',count(*) FILTER (WHERE coverage_status='partial'),
  'unknown_minutes',count(*) FILTER (WHERE coverage_status='unknown'),
  'peak_active_configurations',max(active_configurations_peak),
  'conflicted_observations',coalesce(sum(conflicted_slots_total),0),
  'unmatched_observations',coalesce(sum(unmatched_rows_total),0))
  FROM public.runtime_activity_minute WHERE minute_utc >= now()-interval '24 hours'),
 'window48',(SELECT jsonb_build_object(
  'observed_minutes',count(*),
  'complete_minutes',count(*) FILTER (WHERE coverage_status='complete'),
  'partial_minutes',count(*) FILTER (WHERE coverage_status='partial'),
  'unknown_minutes',count(*) FILTER (WHERE coverage_status='unknown'),
  'peak_active_configurations',max(active_configurations_peak),
  'conflicted_observations',coalesce(sum(conflicted_slots_total),0),
  'unmatched_observations',coalesce(sum(unmatched_rows_total),0))
  FROM public.runtime_activity_minute WHERE minute_utc >= now()-interval '48 hours'),
 'streams',(SELECT coalesce(jsonb_agg(jsonb_build_object(
  'stream',source_stream,'continuity',continuity,
  'last_success_at',to_char(last_success_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'gap_count',gap_count,'unknown_lines_total',unknown_lines_total,
  'heartbeat_total',heartbeat_total,'ingested_events_total',ingested_events_total)
  ORDER BY source_stream),'[]'::jsonb)
  FROM public.hmn_stream_coverage WHERE source_stream IN ('health','recovery'))
)::text;
COMMIT;
'''
# Static remote program; no caller input is interpreted as SQL or shell tokens.
VM121_REMOTE=r'''import json, subprocess, urllib.request
SQL = __SQL__
result={'schema':'b4v2c-vm121-transport-v1','api':'unavailable','db_health':'unavailable','runtime':None,'runtime_error':'SOURCE_UNAVAILABLE'}
def get(path):
    try:
        with urllib.request.urlopen('http://127.0.0.1:18080'+path,timeout=4) as response:
            raw=response.read(2049)
            if len(raw)>2048:return False
            obj=json.loads(raw)
            return isinstance(obj,dict) and obj.get('status')=='ok' and (path!='/health/db' or obj.get('db')==1)
    except Exception:return False
result['api']='healthy' if get('/health') else 'unavailable'
result['db_health']='healthy' if get('/health/db') else 'unavailable'
try:
    # Existing VM130 -> ssh vm121 -> docker exec Postgres read path.
    # Use a fixed container, a read-only transaction and a hard query timeout.
    p=subprocess.run(['docker','exec','-i','wgaccess-postgres','sh','-lc',
      'psql -X -q -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-postgres}" -At'],
      input=SQL,text=True,capture_output=True,timeout=12)
    if p.returncode==0 and len(p.stdout)<16384:
        data=json.loads(p.stdout.strip())
        if isinstance(data,dict) and data.get('schema')=='b4v2c-vm121-runtime-v1':
            result['runtime']=data;result['runtime_error']=None
except Exception:pass
print(json.dumps(result,separators=(',',':'),ensure_ascii=True))
'''.replace('__SQL__',repr(VM121_SQL))


def bounded_positive(value,maxval=10**12):
    return value if isinstance(value,int) and not isinstance(value,bool) and 0<=value<=maxval else None


def finite_number(v,lo=0,hi=100):
    import math
    if isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) and lo<=v<=hi:
        return round(v,3)
    return None


def parse_ts(v):
    if not isinstance(v,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ',v):
        return None
    try:return int(datetime.strptime(v,'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=UTC).timestamp())
    except ValueError:return None


def age_state(epoch,now,limit):
    if epoch is None:return 'invalid',None
    age=now-epoch
    if age < -30:return 'invalid',age
    return ('stale' if age>limit else 'fresh'),age


def vm121_live():
    cmd=['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','vm121','python3 -']
    try:
        p=subprocess.run(cmd,input=VM121_REMOTE.encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=35)
        if p.returncode or len(p.stdout)>18000: return {'error':'SOURCE_UNAVAILABLE'}
        o=json.loads(p.stdout)
        if isinstance(o,dict):o['_capture_epoch']=int(datetime.now(UTC).timestamp())
        return o
    except (OSError,subprocess.TimeoutExpired,ValueError):return {'error':'SOURCE_UNAVAILABLE'}


def normalize_vm121(o,now):
    if not isinstance(o,dict) or o.get('schema')!='b4v2c-vm121-transport-v1':raise ValueError('SOURCE_INVALID')
    api={k:o[k] if o.get(k) in ('healthy','unavailable') else 'unavailable' for k in ('api','db_health')}
    runtime=o.get('runtime')
    if runtime is None:return api,None,None,'unavailable'
    if not isinstance(runtime,dict) or runtime.get('schema')!='b4v2c-vm121-runtime-v1':raise ValueError('SOURCE_INVALID')
    observed=runtime.get('observed_at');rt_epoch=parse_ts(observed)
    if rt_epoch is None or rt_epoch-now>30:raise ValueError('SOURCE_INVALID')
    last=runtime.get('last_minute')
    last_out=None;last_epoch=None
    if last is not None:
        if not isinstance(last,dict):raise ValueError('SOURCE_INVALID')
        last_epoch=parse_ts(last.get('minute_utc'))
        if last_epoch is None or last_epoch-now>30:raise ValueError('SOURCE_INVALID')
        s=last.get('selector_peak'); selector={}
        if not isinstance(s,dict):raise ValueError('SOURCE_INVALID')
        for key in base.SELECTORS:
            n=bounded_positive(s.get(key))
            if n is None:raise ValueError('SOURCE_INVALID')
            selector[key]=n
        cov=last.get('coverage_status')
        if cov not in ('complete','partial','unknown'):raise ValueError('SOURCE_INVALID')
        last_out={'minute_utc':last['minute_utc'],'coverage_status':cov,'coverage_ratio':finite_number(last.get('coverage_ratio'),0,1),
                  'active_configurations_peak':bounded_positive(last.get('active_configurations_peak')),
                  'active_users_peak':bounded_positive(last.get('active_users_peak')),
                  'selector_peak':selector,'conflicted_slots_total':bounded_positive(last.get('conflicted_slots_total')),
                  'unmatched_rows_total':bounded_positive(last.get('unmatched_rows_total'))}
    windows={}
    for w in ('window24','window48'):
        d=runtime.get(w)
        if not isinstance(d,dict):raise ValueError('SOURCE_INVALID')
        windows[w]={k:bounded_positive(d.get(k)) for k in ('observed_minutes','complete_minutes','partial_minutes','unknown_minutes','peak_active_configurations','conflicted_observations','unmatched_observations')}
    streams=[]
    if not isinstance(runtime.get('streams'),list):raise ValueError('SOURCE_INVALID')
    for x in runtime['streams']:
        if not isinstance(x,dict) or x.get('stream') not in ('health','recovery'):raise ValueError('SOURCE_INVALID')
        con=x.get('continuity')
        if con not in ('continuous','gap_detected','initial_unknown','stale','error'):raise ValueError('SOURCE_INVALID')
        last_s=x.get('last_success_at')
        if parse_ts(last_s) is None:raise ValueError('SOURCE_INVALID')
        streams.append({'stream':x['stream'],'continuity':con,'last_success_at':last_s,
                        **{k:bounded_positive(x.get(k)) for k in ('gap_count','unknown_lines_total','heartbeat_total','ingested_events_total')}})
    if len(streams)>2 or len({x['stream'] for x in streams})!=len(streams):raise ValueError('SOURCE_INVALID')
    state,age=age_state(last_epoch,now,180) if last_epoch is not None else ('stale',None)
    return api,{'last_minute':last_out,'window24':windows['window24'],'window48':windows['window48'],'streams':streams,
                'observed_at':observed,'retention_note':'minute_history_retained_approximately_48_hours'},last_epoch,state


def local_json(path):
    try:
        if not path.is_file() or path.stat().st_size>CAP_MAX:return {'error':'SOURCE_UNAVAILABLE' if not path.exists() else 'SOURCE_TOO_LARGE'}
        return json.loads(path.read_bytes())
    except (OSError,ValueError):return {'error':'SOURCE_INVALID'}


def normalize_resources(obj,now):
    if not isinstance(obj,dict) or obj.get('schema')!='router-resource-monitor-v1':raise ValueError('SOURCE_INVALID')
    epoch=parse_ts(obj.get('generated_at_utc'))
    state,age=age_state(epoch,now,1800)
    if state=='invalid':raise ValueError('SOURCE_INVALID')
    hosts=obj.get('hosts')
    if not isinstance(hosts,dict):raise ValueError('SOURCE_INVALID')
    h={}
    for key in ('vm100','vm101','vm103','vm121','vm130'):
        v=hosts.get(key)
        if isinstance(v,dict):
            h[key]={'reachable':v.get('reachable') if type(v.get('reachable')) is bool else None,
                    'status':v.get('status') if v.get('status') in ('PASS','WARN','CRIT') else 'unknown',
                    **{m:bounded_positive(v.get(m),1000000) for m in ('root_used_pct','root_inode_pct','tmp_used_pct','journal_kb','bounded_log_bytes')}}
    return {'generated_at_utc':obj['generated_at_utc'],'overall':obj.get('overall') if obj.get('overall') in ('PASS','WARN','CRIT') else 'unknown',
            'hosts':h,'warning_count':bounded_positive(obj.get('warning_count'))},epoch,state


def normalize_capacity(obj,now):
    if not isinstance(obj,dict) or obj.get('schema')!='router-capacity-snapshot-v1':raise ValueError('SOURCE_INVALID')
    epoch=parse_ts(obj.get('generated_at_utc'))
    state,age=age_state(epoch,now,1800)
    if state=='invalid':raise ValueError('SOURCE_INVALID')
    status=obj.get('source_status') if obj.get('source_status') in ('available','partial','unavailable') else 'unknown'
    pve=obj.get('pve');pve=pve if isinstance(pve,dict) else {}
    nodes={};vm={};nics={}
    for name,v in pve.get('nodes',{}).items() if isinstance(pve.get('nodes'),dict) else []:
        if isinstance(name,str) and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}',name) and isinstance(v,dict):
            nodes[name]={'host_cpu_count':bounded_positive(v.get('host_cpu_count'),4096),
                         'host_mem_used_pct':finite_number(v.get('host_mem_used_pct')),
                         'windows':_capacity_windows(v.get('windows'))}
    for name,v in pve.get('vms',{}).items() if isinstance(pve.get('vms'),dict) else []:
        if name in ('vm100','vm101','vm103','vm121','vm130') and isinstance(v,dict):
            vm[name]={'status':v.get('status') if v.get('status') in ('running','stopped') else 'unknown',
                      'vcpus':bounded_positive(v.get('vcpus'),1024),'windows':_capacity_windows(v.get('windows'))}
    for name,v in pve.get('physical_nics',{}).items() if isinstance(pve.get('physical_nics'),dict) else []:
        if isinstance(name,str) and re.fullmatch(r'[a-zA-Z0-9_.-]{1,32}',name) and isinstance(v,dict):
            nics[name]={'up':v.get('up') if type(v.get('up')) is bool else None,
                        'speed_mbps':bounded_positive(v.get('speed_mbps'),1000000)}
    guests={}
    for name,v in obj.get('guests',{}).items() if isinstance(obj.get('guests'),dict) else []:
        if name in ('vm103','vm121','vm130') and isinstance(v,dict):
            guests[name]={'vcpus':bounded_positive(v.get('vcpus'),1024),'mem_available_pct':finite_number(v.get('mem_available_pct')),
                          'swap_used_mib':bounded_positive(v.get('swap_used_mib'))}
    return {'generated_at_utc':obj['generated_at_utc'],'source_status':status,'failed_sources':bounded_positive(obj.get('failed_sources')),
            'pve':{'source_status':pve.get('source_status') if pve.get('source_status') in ('available','unavailable') else 'unknown',
                   'nodes':nodes,'vms':vm,'physical_nics':nics},'guests':guests},epoch,state


def _capacity_windows(windows):
    if not isinstance(windows,dict):return {}
    out={}
    for k in ('day','week'):
        w=windows.get(k)
        if isinstance(w,dict):
            out[k]={'status':w.get('status') if w.get('status') in ('available','unavailable','partial') else 'unknown',
                    'points':bounded_positive(w.get('points')),
                    **{m:finite_number(w.get(m),0,100) for m in ('cpu_avg_pct','cpu_p95_pct','mem_p95_pct')},
                    'latest_age_sec':bounded_positive(w.get('latest_age_sec'),8640000)}
    return out


def collect_extra(vm121,resources,capacity,now):
    observed={};sources=[];findings=[]
    def source(key,machine,epoch,state,error=None,basis='source_timestamp'):
        age=now-epoch if epoch is not None else None
        sources.append({'id':key,'machine':machine,'source_type':'bounded_read_only','observed_at':base.time_iso(epoch) if epoch is not None else None,
                        'collected_at':base.time_iso(now),'age_seconds':age,'file_mtime_at':None,'freshness_basis':basis,'freshness':state,'error_code':error})
        if state!='fresh':
            code='SOURCE_UNAVAILABLE' if state=='unavailable' else 'SOURCE_STALE' if state=='stale' else 'SOURCE_INVALID'
            findings.append({'code':code,'severity':'warning' if state=='stale' else 'unknown','confidence':'high','sources':[key],
                             'summary':key+' '+state,'read_only_direction':'Check source availability, freshness and allowed read path'})
    if isinstance(vm121,dict) and not vm121.get('error'):
        try:
            api,rt,rt_epoch,rt_state=normalize_vm121(vm121,now)
            observed['vm121.api']=api
            api_epoch=bounded_positive(vm121.get('_capture_epoch')) or now
            api_ok=api['api']=='healthy' and api['db_health']=='healthy'
            api_state='unavailable' if not api_ok else age_state(api_epoch,now,90)[0]
            source('vm121.api','vm121',api_epoch,api_state,
                   None if api_ok else 'SOURCE_UNAVAILABLE','live_loopback_health')
            observed['vm121.runtime']=rt
            source('vm121.runtime','vm121',rt_epoch,rt_state,None if rt is not None else 'SOURCE_UNAVAILABLE','last_closed_minute')
        except ValueError:
            observed['vm121.api']=None;observed['vm121.runtime']=None
            source('vm121.api','vm121',None,'invalid','SOURCE_INVALID');source('vm121.runtime','vm121',None,'invalid','SOURCE_INVALID')
    else:
        for key in ('vm121.api','vm121.runtime'):
            observed[key]=None;source(key,'vm121',None,'unavailable','SOURCE_UNAVAILABLE')
    for key,obj,norm in (('vm130.resources',resources,normalize_resources),('vm130.capacity',capacity,normalize_capacity)):
        if isinstance(obj,dict) and not obj.get('error'):
            try:
                observed[key],epoch,state=norm(obj,now);source(key,'vm130',epoch,state)
            except ValueError:
                observed[key]=None;source(key,'vm130',None,'invalid','SOURCE_INVALID')
        else:
            observed[key]=None;source(key,'vm130',None,'unavailable','SOURCE_UNAVAILABLE')
    # Correlation must be based on *fresh and valid* minute evidence. A historical
    # sum of conflicts is not a count of affected users or current defects.
    status={x['id']:x['freshness'] for x in sources}
    rt=observed.get('vm121.runtime')
    if status.get('vm121.runtime')=='stale':
        findings.append({'code':'RUNTIME_COLLECTOR_STALE','severity':'warning','confidence':'high',
                         'sources':['vm121.runtime'],'summary':'Latest closed runtime-activity minute is stale',
                         'read_only_direction':'Inspect existing VM121 runtime ingestion and VM130 sampler freshness'})
    elif status.get('vm121.runtime')=='fresh' and rt and rt.get('last_minute'):
        conflicts=rt['last_minute'].get('conflicted_slots_total')
        if conflicts and conflicts>0:
            findings.append({'code':'RUNTIME_ACTIVITY_CONFLICT','severity':'warning','confidence':'high',
                             'sources':['vm121.runtime'],
                             'summary':f'{conflicts} conflicting observations in latest closed minute; not distinct users',
                             'read_only_direction':'Inspect runtime sample parity and per-selector aggregate history'})
    # Interpret only *fresh* observations. The VM130 resource-monitor status
    # is the authority for its own thresholds; the diagnostic collector must
    # not independently turn a journal size into a network outage verdict.
    res=observed.get('vm130.resources')
    if status.get('vm130.resources')=='fresh' and res:
        host=res.get('hosts',{}).get('vm130',{})
        resource_status=host.get('status')
        if resource_status in ('WARN','CRIT'):
            journal=host.get('journal_kb')
            detail=f'; journal_kb={journal}' if journal is not None else ''
            findings.append({'code':'VM130_RESOURCE_MONITOR_'+resource_status,
                             'severity':'warning' if resource_status=='WARN' else 'critical',
                             'confidence':'high','sources':['vm130.resources'],
                             'summary':f'VM130 resource monitor reports {resource_status}{detail}; resource status, not VPN reachability',
                             'read_only_direction':'Review the existing VM130 resource-monitor warning and its own threshold policy'})

    # These counters aggregate observations over 24h. They do NOT describe
    # active conflicts in the latest minute, affected users, or an outage.
    if status.get('vm121.runtime')=='fresh' and rt:
        w24=rt.get('window24') or {}
        minutes=w24.get('observed_minutes')
        if minutes is not None and minutes>0:
            conflicts=w24.get('conflicted_observations')
            partial=w24.get('partial_minutes')
            if conflicts is not None and conflicts>0:
                findings.append({'code':'RUNTIME_HISTORY_24H_CONFLICTS','severity':'warning',
                                 'confidence':'high','sources':['vm121.runtime'],
                                 'summary':f'24h historical window: {conflicts} conflicting observations across {minutes} observed minutes; not current conflicts or distinct users',
                                 'read_only_direction':'Review VM121 historical aggregates; separately inspect the latest closed minute for current state'})
            if partial is not None and partial>0:
                findings.append({'code':'RUNTIME_HISTORY_24H_PARTIAL','severity':'warning',
                                 'confidence':'high','sources':['vm121.runtime'],
                                 'summary':f'24h historical window: {partial} partial-coverage minutes out of {minutes} observed; not a claim of current outage',
                                 'read_only_direction':'Review VM121 retained minute coverage and source continuity; do not infer client impact'})
    return sources,observed,findings


def history_tail_findings(sources,observations,truncated_sources):
    """Collector-size limits, never evidence of lost remote log bytes."""
    states={s['id']:s['freshness'] for s in sources}
    result=[]
    key='vm101.recovery_events'
    obj=observations.get(key)
    if key in truncated_sources and states.get(key)=='fresh' and isinstance(obj,dict) and obj.get('truncated') is True:
        limit=obj.get('tail_limit_lines'); total=obj.get('source_total_lines')
        if (isinstance(limit,int) and not isinstance(limit,bool) and limit>0 and
                isinstance(total,int) and not isinstance(total,bool) and total>limit):
            result.append({'code':'RECOVERY_HISTORY_BOUNDED_TAIL','severity':'info',
                           'confidence':'high','sources':[key],
                           'summary':f'VM101 recovery-event collector tail: last {limit} of {total} source lines; truncated view, not lost source logs',
                           'read_only_direction':'Treat this as an explicit collector history limit; do not infer missing log files or current recovery failures'})
    return result


def dedupe_findings(findings):
    """Preserve source-order evidence but never repeat the same code/source."""
    seen=set();unique=[]
    for item in findings:
        key=(item['code'],tuple(item['sources']))
        if key not in seen:
            seen.add(key);unique.append(item)
    return unique


def generate(fetch,vm121,resources,capacity,output,synthetic_fixture=False):
    now=int(datetime.now(UTC).timestamp())
    sources,observations,events,truncated=base.make_snapshot(fetch,now)
    extra_sources,extra_obs,extra_findings=collect_extra(vm121,resources,capacity,now)
    sources.extend(extra_sources);observations.update(extra_obs)
    findings=dedupe_findings(base.find(sources,observations)+extra_findings+
                             history_tail_findings(sources,observations,truncated))
    health='unknown'
    snapshot={'schema_version':SCHEMA,'synthetic_fixture':synthetic_fixture,'captured_at':base.time_iso(now),
              'capture_status':'partial' if any(s['freshness']!='fresh' for s in sources) else 'complete_for_review_scope',
              'observability_completeness':'review_scope_only','overall_system_health':health,'sources':sources,
              'observations':observations,'findings':findings,'truncated_sources':truncated,
              'coverage':{'machines':['vm100','vm101','vm121','vm130'],'vm103':'capacity_only',
                          'client_activity_per_selector':'last_closed_minute_vm121',
                          'topology_event_provenance':'not_available_as_timestamped_event_stream',
                          'capacity':'passive_local_snapshot','history':'bounded_vm101_events_and_vm121_24h_48h_aggregates'}}
    def j(o):return (json.dumps(o,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    text=base.build_report(snapshot)
    text=text.replace('Prototype scope: VM100 + VM101 only (VM121/VM130/backend/capacity not yet connected).',
                      'Review scope: VM100/101 live status; VM121 loopback/DB aggregates; VM130 passive capacity. Not full production acceptance.')
    text+='\n## VM121 и ресурсы — дополнительные наблюдения\n'
    for key in ('vm121.api','vm121.runtime','vm130.resources','vm130.capacity'):
        text+='\n- `'+key+'`: '+json.dumps(observations.get(key),ensure_ascii=False,sort_keys=True)+'\n'
    text+='\n## Ограничения B4/v2c review\n- История bridge-start/confirm/clear не содержит гарантированных меток времени в доступном источнике; историческая причинность не заявляется.\n- Нет атомарности между машинами; VM121 последняя закрытая минута не является текущим 5-секундным снимком.\n- Не выполняются активные VPN-пробы, исправления или изменения БД.\n'
    payload={'README.md':b'# B4D read-only diagnostics (Admin browser)\n\nGenerated by the installed Admin Diagnostics worker; available for download in the authenticated Admin Monitoring page. Review-scope snapshot, not an end-to-end health verdict. No raw user rows, secrets, logs or private endpoints.\n',
             'report.md':text.encode(),'snapshot.json':j(snapshot),
             'events.jsonl':b''.join((json.dumps(e,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode() for e in events)}
    manifest={'schema_version':SCHEMA,'capture_started_at':base.time_iso(now),'capture_finished_at':base.time_iso(),
              'request_id':uuid.uuid4().hex[:12], 'scope':'vm100_vm101_vm121_vm130_review',
              'files':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in payload.items()},
              'event_count':len(events),'truncated_sources':truncated}
    payload['manifest.json']=j(manifest)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    dest=output/f"WG_PAID_MGTS_DIAGNOSTICS_{datetime.now(UTC).strftime('%Y%m%d-%H%M%SZ')}_{manifest['request_id']}.zip"
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as f:
            with ZipFile(f,'w',ZIP_DEFLATED,compresslevel=8) as z:
                for k,v in payload.items():z.writestr(k,v)
        if dest.stat().st_size>1024*1024:raise ValueError('ZIP_TOO_LARGE')
    except BaseException:
        dest.unlink(missing_ok=True);raise
    return dest,snapshot


def main():
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--live',action='store_true')
    group.add_argument('--fixture',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.live:
        if os.geteuid()==0:p.error('Must run as ops, not root')
        try:args.output.resolve().relative_to(Path('/home/ops').resolve())
        except ValueError:p.error('Live output must be under /home/ops')
        fetch=base.ssh_capture
        vm121=vm121_live()
        resources=local_json(CAP_ROOT/'current.json')
        capacity=local_json(CAP_ROOT/'capacity.json')
    else:
        fetch=lambda h:base.fixture_capture(h,args.fixture)
        def fixture_json(name):
            p=args.fixture/name
            return local_json(p)
        vm121=fixture_json('vm121.json')
        resources=fixture_json('resources.json')
        capacity=fixture_json('capacity.json')
    dest,snapshot=generate(fetch,vm121,resources,capacity,args.output,synthetic_fixture=bool(args.fixture))
    print('RESULT=PASS_B4V2C_REVIEW_CAPTURE')
    print('ZIP='+str(dest))
    print('CAPTURE_STATUS='+snapshot['capture_status'])
    print('FINDINGS='+str(len(snapshot['findings'])))

if __name__=='__main__':main()
