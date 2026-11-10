#!/usr/bin/env python3
"""B4 v2b bounded read-only VM100/VM101 diagnostic capture prototype.

NOT a production-trigger/API component. No remote writes. Raw SSH returns are
kept in process memory and never copied into output; output is allowlisted.
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

SCHEMA = 'wg-paid-b4-diagnostics-v2b-prototype-4'
UTC = timezone.utc
SLOT_IDS = [f'egress{i}' for i in range(1, 6)]
SELECTORS = [f'cs{i}' for i in range(1, 6)]
# Authoritative current mapping as verified in VM101 source slots.conf / production status.
EXPECTED_MAP = {'egress1':'cs4', 'egress2':'cs5', 'egress3':'cs1', 'egress4':'cs2', 'egress5':'cs3'}
FILES = {
 'vm100': {
  'topology': ('/var/lib/router-wgpay-topology/state.kv', 10000, None),
  'allocator': ('/var/lib/router-wgpay-egress/state.kv', 30000, None),
 },
 'vm101': {
  'fallback': ('/etc/router-wgpay-fallback/state.kv', 12000, None),
  'watcher_topology': ('/etc/router-wgpay-watcher-topology/state.kv', 12000, None),
  'delivery': ('/etc/router-wgpay-topology-delivery/state.kv', 12000, None),
  'ack': ('/etc/router-wgpay-topology-delivery/last-ack.kv', 12000, None),
  'recovery': ('/etc/router-egress-recovery/state.kv', 12000, None),
  'watcher_last': ('/var/lib/router-egress-recovery/health-watch/last.json', 20000, None),
  'health_events': ('/var/log/router-egress-health-repair.log', 65000, 150),
  'recovery_events': ('/etc/router-egress-recovery/events.tsv', 65000, 120),
 }
}
ALLOW = {
 'vm100.topology': ('accepted_generation','mode','allowed_selectors','exhausted_selectors','source_vm101_generation'),
 'vm100.allocator': ('generated_epoch','topology_state_valid','topology_mode','topology_generation','allowed_selectors','exhausted_selectors','active_now_count','active_state_count','assignment_pending_count','blocked','block_reason','rows','exhausted_row_count','action_reason'),
 'vm101.fallback': ('accepted_generation','mode','healthy_slots','exhausted_slots','healthy_selectors','exhausted_selectors','source_vm101_generation'),
 'vm101.watcher_topology': ('updated_epoch','last_action','last_slot','last_reason','fallback_generation','mode','healthy_slots','exhausted_slots'),
 'vm101.delivery': ('pending_generation','last_attempt_result','last_acked_generation','last_ack_epoch','last_attempt_epoch','pending_attempt_count'),
 'vm101.ack': ('accepted_generation','direct_action','direct_action_result'),
 'vm101.recovery': ('mode','active_generation_id','last_refresh_result','last_refresh_epoch','repair_events_since_full_refresh','full_refresh_due','last_retry_result'),
}
VALID_TOKEN = re.compile(r'^[A-Za-z0-9_.:-]{0,128}$')
VALID_GENERATION = re.compile(r'^\d{12}$')
VALID_TIMESTAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
# A recently touched file is not proof that its content is valid.  Require the
# minimum accepted state shape before using it as evidence in cross-VM findings.
REQUIRED_KV = {
 'vm100.topology': {'accepted_generation','mode','allowed_selectors','exhausted_selectors'},
 'vm100.allocator': {'generated_epoch','topology_mode','allowed_selectors','exhausted_selectors'},
 'vm101.fallback': {'mode','healthy_slots','exhausted_slots','healthy_selectors','exhausted_selectors'},
 'vm101.watcher_topology': {'mode','healthy_slots','exhausted_slots'},
 'vm101.delivery': {'pending_generation','last_attempt_result','last_acked_generation'},
 'vm101.ack': {'accepted_generation'},
 'vm101.recovery': {'mode'},
}
MAX_FUTURE_SKEW_SECONDS = 30
# Even allowlisted status fields may unexpectedly contain an address or email.
SENSITIVE_TOKEN = re.compile(r'(?:\d{1,3}\.){3}\d{1,3}|@|://|[/\\]')

EVENT_TYPES = {'health_fail_first','health_fail_threshold','health_recovered','repair_success','repair_failed','repair_suppressed_cooldown','topology_stable_clear','topology_stable_clear_failed','topology_stable_clear_uncertain','full_refresh_requested','full_refresh_request_failed','repair_deferred_lock_busy'}
EVENT_OUTCOMES = {'PASS','FAIL','SUCCESS','FAILED','NOOP'}
# Select rare, meaningful health events on OpenWrt BEFORE tailing. A fixed tail
# of noisy health log lines can otherwise omit every incident (seen in R04).
HEALTH_EVENT_PATTERN = r'(^|[[:space:]])event=(' + '|'.join(sorted(EVENT_TYPES)) + r')([[:space:]]|$)'
HEALTH_EVENT_RX = re.compile(r'(^|\s)event=(' + '|'.join(sorted(EVENT_TYPES)) + r')(\s|$)')
HEALTH_LOG_MARKER = '__ROUTER_EGRESS_HEALTH_REPAIR_LOG_TRUNCATED'


def time_iso(epoch=None):
    return datetime.fromtimestamp(epoch, UTC).strftime('%Y-%m-%dT%H:%M:%SZ') if epoch is not None else datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def safe_number(v, max_value=2**40):
    if type(v) is int and 0 <= v <= max_value: return v
    if isinstance(v, str) and re.fullmatch(r'\d{1,18}',v) and int(v)<=max_value:return int(v)
    return None


def validated_token(v):
    return v if isinstance(v,str) and VALID_TOKEN.fullmatch(v) and not SENSITIVE_TOKEN.search(v) else None


def csv_enum(v, universe):
    if not isinstance(v,str):return None
    out = v.split(',') if v else []
    if len(out)!=len(set(out)) or any(x not in universe for x in out):return None
    if out != [x for x in universe if x in out]:return None
    return out


def normalize_kv(source, contents):
    allow=ALLOW[source]
    extracted={}
    for line in contents.splitlines():
        if '=' not in line:continue
        k,v=line.split('=',1)
        if k in allow and k not in extracted:extracted[k]=v
    out={}
    for key in allow:
        if key not in extracted: continue
        v=extracted[key]
        if key in {'healthy_slots','exhausted_slots'}:
            v=csv_enum(v,SLOT_IDS)
        elif key in {'allowed_selectors','exhausted_selectors','healthy_selectors'}:
            v=csv_enum(v,SELECTORS)
        elif key in {'generated_epoch','last_ack_epoch','last_attempt_epoch','updated_epoch','last_refresh_epoch','repair_events_since_full_refresh','active_now_count','active_state_count','assignment_pending_count','rows','exhausted_row_count','pending_attempt_count'}:
            v=safe_number(v)
        elif key in {'blocked','topology_state_valid','full_refresh_due'}:
            v={'true':True,'false':False}.get(v)
        elif key in {'accepted_generation','fallback_generation','last_acked_generation','pending_generation','topology_generation'}:
            v=v if (v=='' or VALID_GENERATION.fullmatch(v)) else None
        else:
            v=validated_token(v)
        if v is not None:out[key]=v
    if not REQUIRED_KV[source].issubset(out):
        raise ValueError('MISSING_OR_INVALID_REQUIRED_FIELD')
    if source in ('vm100.topology','vm101.fallback','vm101.watcher_topology') and out.get('mode') not in ('NORMAL','SLOT_EXHAUSTED'):
        raise ValueError('UNEXPECTED_TOPOLOGY_MODE')
    return out


def normalize_watcher(raw):
    if len(raw)>20000:raise ValueError('SOURCE_TOO_LARGE')
    o=json.loads(raw)
    if not isinstance(o,dict) or o.get('schema')!='router-egress-health-repair-watch-v6' or not isinstance(o.get('slots'),list):raise ValueError('SCHEMA_INVALID')
    slots={}
    for s in o['slots']:
        if not isinstance(s,dict) or s.get('slot') not in SLOT_IDS or s['slot'] in slots:raise ValueError('INVALID_SLOTS')
        slot=s['slot']
        row={'slot':slot,'selector':EXPECTED_MAP[slot]}
        for field in ('health_ok',):
            if type(s.get(field)) is bool:row[field]=s[field]
        for field in ('https_quorum','fail_count'):
            n=safe_number(s.get(field),100000)
            if n is not None:row[field]=n
        for field in ('decision','action'):
            t=validated_token(s.get(field))
            if t is not None:row[field]=t
        slots[slot]=row
    if set(slots) != set(SLOT_IDS) or any(type(v.get('health_ok')) is not bool or not isinstance(v.get('https_quorum'),int) or not 0 <= v['https_quorum'] <= 3 for v in slots.values()):
        raise ValueError('INCOMPLETE_WATCHER_SNAPSHOT')
    mode=validated_token(o.get('mode'))
    if mode not in ('--commit','--dry-run'): raise ValueError('WATCHER_MODE_INVALID')
    return {'mode':mode,'slots':slots}


def normalize_events(key, raw):
    events=[]; truncated=False; discarded=0
    if key=='health_events':
        for line in raw.splitlines():
            if line.startswith('__ROUTER_EGRESS_HEALTH_REPAIR_LOG_TRUNCATED'):truncated=True;continue
            tokens=line.split()
            if not tokens or not VALID_TIMESTAMP.fullmatch(tokens[0]): continue
            params={}
            for chunk in tokens[1:]:
                if '=' in chunk:
                    k,v=chunk.split('=',1)
                    if k in {'event','slot','decision','reason','dispatcher_rc'}: params[k]=v
            typ=params.get('event')
            if typ not in EVENT_TYPES: continue
            slot=params.get('slot')
            if slot not in SLOT_IDS:slot=None
            events.append({'observed_at':tokens[0],'machine':'vm101','type':typ,'slot':slot,'outcome':'unknown','source':'vm101.health_events'})
    elif key=='recovery_events':
        for line in raw.splitlines():
            row=line.split('\t')
            if len(row)<5 or not VALID_TIMESTAMP.fullmatch(row[1]): continue
            ev=validated_token(row[2]);result=validated_token(row[3])
            if ev not in {'local_repair','full_pool_refresh','daily_quality'}:continue
            slot= row[5] if len(row)>5 else ''
            slot=slot if slot in SLOT_IDS else ('egress'+slot[3:] if re.fullmatch(r'vpn[1-5]',slot) else None)
            events.append({'observed_at':row[1],'machine':'vm101','type':ev,'slot':slot,'outcome':result if result in EVENT_OUTCOMES else 'unknown','source':'vm101.recovery_events'})
    return events, truncated, discarded


def generate_script(host):
    """Fixed bounded read-only source frames; VM100 allocator is pre-filtered on guest.

    Never bring the full allocator registry over SSH. Only explicitly allowlisted
    state keys are returned, while file-size limits apply to returned content.
    """
    lines=['set -eu','umask 077',"printf 'B4FRAME_V1\n'", "printf 'CLOCK %s\n' \"$(date +%s)\"" ]
    for name,(path,limit,tail) in FILES[host].items():
        if not re.fullmatch(r'[a-z_]+', name) or not re.fullmatch(r'/[A-Za-z0-9_./-]+',path):raise AssertionError('invalid source definition')
        lines.append(f"if test -r '{path}'; then")
        if host=='vm100' and name=='allocator':
            # These are status AGGREGATES; never extract roster, peers, IPs or keys.
            pattern='^('+'|'.join(ALLOW['vm100.allocator'])+')='
            lines.append(f"  data=$(LC_ALL=C grep -E '{pattern}' '{path}' || true)")
            lines.append('  bytes=$(printf %s "$data" | wc -c)')
            lines.append(f'  if test "$bytes" -le {limit}; then')
            lines.append(f"    printf 'DATA {name} %s %s\n' \"$(stat -c %Y '{path}')\" \"$bytes\"")
            lines.append('    printf %s "$data"')
        elif tail is None:
            lines.append(f"  bytes=$(wc -c < '{path}')")
            lines.append(f'  if test "$bytes" -le {limit}; then')
            lines.append(f"    printf 'DATA {name} %s %s\n' \"$(stat -c %Y '{path}')\" \"$bytes\"")
            lines.append(f"    cat '{path}'")
        else:
            # All raw data stays on the guest; transmit a bounded tail only.
            # Health logs contain many routine lines, so filter by an exact
            # allowlist of meaningful event labels BEFORE tailing.
            lines.append(f"  total_lines=$(wc -l < '{path}')")
            if name == 'health_events':
                lines.append(f"  significant_lines=$(LC_ALL=C grep -E -c '{HEALTH_EVENT_PATTERN}' '{path}' || true)")
                lines.append(f"  data=$(LC_ALL=C grep -E '{HEALTH_EVENT_PATTERN}' '{path}' | tail -n {tail})")
                lines.append(f"  if LC_ALL=C grep -q '^{HEALTH_LOG_MARKER}' '{path}'; then log_marker=1; else log_marker=0; fi")
            else:
                lines.append(f"  data=$(tail -n {tail} '{path}')")
            lines.append('  bytes=$(printf %s "$data" | wc -c)')
            lines.append(f'  if test "$bytes" -le {limit}; then')
            if name == 'health_events':
                lines.append(f"    printf 'DATA {name} %s %s %s %s %s\n' \"$(stat -c %Y '{path}')\" \"$bytes\" \"$total_lines\" \"$significant_lines\" \"$log_marker\"")
            else:
                lines.append(f"    printf 'DATA {name} %s %s %s\n' \"$(stat -c %Y '{path}')\" \"$bytes\" \"$total_lines\"")
            lines.append('    printf %s "$data"')
        lines.extend(["    printf 'END\n'", "  else",f"    printf 'ERROR {name} SOURCE_TOO_LARGE\n'",'  fi','else',f"  printf 'ERROR {name} SOURCE_UNAVAILABLE\n'",'fi'])
    lines.append("printf 'DONE\n'")
    return ('\n'.join(lines)+'\n').encode()


def decode_frames(raw,host):
    if len(raw)>400000:raise ValueError('SOURCE_TOO_LARGE')
    pos=0
    def line():
        nonlocal pos
        end=raw.find(b'\n',pos,pos+240)
        if end<0:raise ValueError('FRAMING_INVALID')
        part=raw[pos:end];pos=end+1
        return part.decode('ascii','strict')
    if line()!='B4FRAME_V1':raise ValueError('FRAMING_INVALID')
    head=line()
    if not re.fullmatch(r'CLOCK \d{9,12}',head):raise ValueError('CLOCK_INVALID')
    remote_epoch=int(head.split()[1]);data={}
    for name,(_,limit,tail) in FILES[host].items():
        hdr=line()
        if hdr == f'ERROR {name} SOURCE_UNAVAILABLE' or hdr==f'ERROR {name} SOURCE_TOO_LARGE':
            data[name]={'error':hdr.split()[-1]};continue
        suffix = (r' (\d{1,12}) (\d{1,12}) ([01])' if name=='health_events' else
                  r' (\d{1,12})' if tail is not None else '')
        m=re.fullmatch(rf'DATA {name} (\d{{9,12}}) (\d{{1,8}})'+suffix,hdr)
        if not m:raise ValueError('FRAMING_INVALID')
        epoch,length=int(m.group(1)),int(m.group(2))
        if length>limit or len(raw)-pos<length:raise ValueError('SOURCE_TOO_LARGE')
        body=raw[pos:pos+length];pos+=length
        if line()!='END':raise ValueError('FRAMING_INVALID')
        data[name]={'mtime_epoch':epoch,'raw':body.decode('utf-8','replace')}
        if tail is not None:data[name]['total_lines']=int(m.group(3))
        if name=='health_events':
            data[name]['significant_lines']=int(m.group(4))
            data[name]['log_marker_seen']=m.group(5)=='1'
    if line()!='DONE' or pos!=len(raw):raise ValueError('FRAMING_INVALID')
    return remote_epoch,data


def ssh_capture(host, timeout=35):
    import shlex
    ip={'vm100':'10.71.100.1','vm101':'10.71.100.2'}[host]
    inner=['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=8','-o','StrictHostKeyChecking=yes','-i','/root/.ssh/pve_to_openwrt_mgts_ed25519',f'root@{ip}','sh','-s']
    cmd=['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=9','-o','StrictHostKeyChecking=yes','pve-mgts',' '.join(map(shlex.quote,inner))]
    try:
        p=subprocess.run(cmd,input=generate_script(host),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired):return None,{'error':'SOURCE_UNAVAILABLE'}
    if p.returncode:return None,{'error':'SOURCE_UNAVAILABLE'}
    try:return decode_frames(p.stdout,host)
    except (ValueError,UnicodeError):return None,{'error':'SOURCE_INVALID'}


def fixture_capture(host,root):
    base=root/host
    if not base.exists(): return None,{'error':'SOURCE_UNAVAILABLE'}
    fixture_epoch=int((base/'clock.txt').read_text().strip()) if (base/'clock.txt').exists() else 1794159000
    epoch=int(datetime.now(UTC).timestamp())
    shift=epoch-fixture_epoch
    out={}
    for name in FILES[host]:
        f=base/(name+'.txt')
        if not f.exists():out[name]={'error':'SOURCE_UNAVAILABLE'}
        else:
            raw=f.read_text()
            if host=='vm100' and name=='allocator':
                # Shift synthetic logical observation time as well as mtime.
                # Otherwise an offline fixture becomes 'stale' tomorrow.
                raw=re.sub(r'(?m)^generated_epoch=(\d+)$',lambda m:'generated_epoch='+str(int(m.group(1))+shift),raw)
            out[name]={'mtime_epoch':int((base/(name+'.epoch')).read_text().strip())+shift if (base/(name+'.epoch')).exists() else epoch,'raw':raw}
            if FILES[host][name][2] is not None:out[name]['total_lines']=len(raw.splitlines())
            if name=='health_events':
                out[name]['significant_lines']=sum(bool(HEALTH_EVENT_RX.search(line)) for line in raw.splitlines())
                out[name]['log_marker_seen']=HEALTH_LOG_MARKER in raw
    return epoch,out


def source_age(seconds,age_max=None):
    if seconds is None:return 'unavailable'
    return 'stale' if age_max is not None and seconds>age_max else 'fresh'


def make_snapshot(fetch,now):
    sources=[];parsed={};events=[];truncations=[]
    for host in FILES:
        remote_epoch,items=fetch(host)
        if 'error' in items:items={name:{'error':items['error']} for name in FILES[host]}
        for name in FILES[host]:
            item=items.get(name,{'error':'SOURCE_UNAVAILABLE'});src=host+'.'+name
            age=now - item['mtime_epoch'] if 'mtime_epoch' in item else None
            clock_skew=age is not None and age < -MAX_FUTURE_SKEW_SECONDS
            age_limit=90 if name=='watcher_last' else 150 if name=='allocator' else None
            durable=name not in ('watcher_last','health_events','recovery_events','allocator')
            record={'id':src,'machine':host,'source_type':'durable_state' if durable else 'observed_snapshot_or_history',
                    'remote_clock_at_read':time_iso(remote_epoch) if remote_epoch is not None else None,
                    'observed_at':time_iso(now) if durable and 'mtime_epoch' in item else (time_iso(item['mtime_epoch']) if 'mtime_epoch' in item else None),
                    'file_mtime_at':time_iso(item['mtime_epoch']) if 'mtime_epoch' in item else None,
                    'file_mtime_age_seconds':age,'freshness_basis':'successful_read_of_state' if durable else 'source_mtime',
                    'collected_at':time_iso(now),'age_seconds':0 if durable and age is not None else age,
                    'freshness':source_age(0 if durable and age is not None else age,age_limit),
                    'error_code':item.get('error')}
            if 'error' in item:record['freshness']='unavailable' if item['error']=='SOURCE_UNAVAILABLE' else 'invalid';parsed[src]=None
            elif clock_skew:
                record['freshness']='invalid';record['error_code']='SOURCE_CLOCK_SKEW';parsed[src]=None
            else:
                try:
                    if name=='watcher_last': parsed[src]=normalize_watcher(item['raw'])
                    elif name in ('health_events','recovery_events'):
                        es,log_marker_seen,_=normalize_events(name,item['raw']);events+=es
                        limit=FILES[host][name][2]
                        line_count=item.get('total_lines')
                        significant_lines=item.get('significant_lines') if name=='health_events' else None
                        cutoff_unknown=(significant_lines if name=='health_events' else line_count) is None
                        actual_tail_loss=cutoff_unknown or (significant_lines if name=='health_events' else line_count)>limit
                        parsed[src]={'parsed_count':len(es),'bounded_tail':True,
                                     'tail_limit_lines':limit,'source_total_lines':line_count,
                                     'source_significant_lines':significant_lines,
                                     'truncated':actual_tail_loss,'truncation_unknown':cutoff_unknown,
                                     'log_truncation_seen':item.get('log_marker_seen',log_marker_seen)}
                        if actual_tail_loss:truncations.append(src)
                    else:
                        parsed[src]=normalize_kv(src,item['raw'])
                        if src=='vm100.allocator':
                            # Authoritative time is the allocator generated_epoch.
                            generation_age=now-parsed[src]['generated_epoch']
                            record['observed_at']=time_iso(parsed[src]['generated_epoch'])
                            record['age_seconds']=generation_age
                            record['freshness_basis']='generated_epoch'
                            if generation_age < -MAX_FUTURE_SKEW_SECONDS:
                                record['freshness']='invalid'
                                record['error_code']='SOURCE_CLOCK_SKEW'
                                parsed[src]=None
                            else:
                                record['freshness']=source_age(generation_age,150)
                except (ValueError,KeyError,TypeError,json.JSONDecodeError):
                    record['freshness']='invalid';record['error_code']='SOURCE_INVALID';parsed[src]=None
            sources.append(record)
    events.sort(key=lambda e:(e['observed_at'],e['machine'],e['type']))
    if len(events)>200:truncations.append('events.total');events=events[-200:]
    return sources, parsed, events, truncations


def find(sources,parsed):
    meta={x['id']:x for x in sources};findings=[]
    def add(code,severity,ids,summary,confidence='high'):
        findings.append({'code':code,'severity':severity,'confidence':confidence,'sources':ids,'summary':summary,'read_only_direction':{'TOPOLOGY_HEALTH_MISMATCH':'Investigate VM101 recovery/fallback state and missed bridge-clear', 'TOPOLOGY_GENERATION_DIVERGENCE':'Investigate VM101 topology delivery and VM100 accepted ACK', 'SOURCE_STALE':'Check collector/watcher scheduling and data freshness', 'SOURCE_UNAVAILABLE':'Check management path and source availability'}.get(code,'Investigate cited sources')})
    for s in sources:
        if s['freshness']=='unavailable':add('SOURCE_UNAVAILABLE','unknown',[s['id']],s['id']+' unavailable','high')
        elif s['freshness']=='invalid':add('SOURCE_INVALID','unknown',[s['id']],s['id']+' invalid','high')
        elif s['freshness']=='stale':add('SOURCE_STALE','warning',[s['id']],s['id']+' observation stale','high')
    fb=parsed.get('vm101.fallback');watch=parsed.get('vm101.watcher_last');vm100=parsed.get('vm100.topology');delivery=parsed.get('vm101.delivery')
    if fb and watch and meta['vm101.watcher_last']['freshness']=='fresh':
        exhausted=fb.get('exhausted_slots')
        rows=watch.get('slots',{})
        if exhausted is not None and all(x in rows and rows[x].get('health_ok') is True and rows[x].get('https_quorum',-1)>=2 for x in exhausted):
            if exhausted:
                add('TOPOLOGY_HEALTH_MISMATCH','critical',['vm101.fallback','vm101.watcher_last'], 'Watcher HTTPS healthy but excluded: '+','.join(exhausted))
    if vm100 and delivery:
        a=vm100.get('accepted_generation');b=delivery.get('last_acked_generation')
        if a and b and a!=b:add('TOPOLOGY_GENERATION_DIVERGENCE','critical',['vm100.topology','vm101.delivery'], f'VM100 accepted {a}, VM101 last ACK {b}')
    if vm100 and fb:
        expected=fb.get('healthy_selectors');actual=vm100.get('allowed_selectors')
        if expected is not None and actual is not None and expected!=actual:
            add('TOPOLOGY_SELECTOR_DIVERGENCE','critical',['vm100.topology','vm101.fallback'], 'Allowed selectors differ between VM100 and VM101')
    return findings


def build_report(snapshot):
    findings=snapshot['findings'];parsed=snapshot['observations'];sources=snapshot['sources']
    lines=['# WG Paid / MGTS — диагностический снимок', '', f"- Capture: `{snapshot['captured_at']}`", f"- Status: **{snapshot['capture_status']}**" , f"- Synthetic offline fixture: **{snapshot['synthetic_fixture']}**", '- Prototype scope: VM100 + VM101 only (VM121/VM130/backend/capacity not yet connected).', '', '## Findings']
    if findings:
        for f in findings:lines.append(f"- **{f['severity']} · {f['code']}** — {f['summary']}; sources: {', '.join(f['sources'])}")
    else:lines.append('- В доступных наблюдениях противоречий не выявлено; это **не** доказательство полного здоровья всей системы.')
    lines.extend(['','## Состояние топологии'])
    for name in ('vm101.fallback','vm101.delivery','vm100.topology','vm100.allocator'):
        s=parsed.get(name)
        lines.append(f'- `{name}`: '+(json.dumps(s,ensure_ascii=False,sort_keys=True) if s is not None else 'нет достоверных данных'))
    lines.extend(['','## Свежесть источников'])
    for s in sources:lines.append(f"- `{s['id']}`: {s['freshness']}, observed={s['observed_at']}, age={s['age_seconds']}s, file_mtime={s['file_mtime_at']}, basis={s['freshness_basis']}, error={s['error_code']}")
    lines.extend(['','## История событий — полнота источников'])
    for name in ('vm101.health_events','vm101.recovery_events'):
        o=parsed.get(name)
        if o is not None:lines.append(f"- `{name}`: parsed={o['parsed_count']}, source_lines={o['source_total_lines']}, significant_lines={o['source_significant_lines']}, tail_limit={o['tail_limit_lines']}, truncated={o['truncated']}, log_truncation_marker={o['log_truncation_seen']}")
    lines.extend(['','## Ограничения','- Нет атомарного снимка между машинами.','- Проверка HTTPS не выполнялась при экспорте — использованы последние результаты watcher.','- История событий ограничена хвостами файлов и может не покрывать 14 дней.','- Персональные данные и сырые логи намеренно исключены.',''])
    return '\n'.join(lines)


def generate(fetch,output,synthetic_fixture=False):
    now=int(datetime.now(UTC).timestamp());sources,observations,events,truncated=make_snapshot(fetch,now)
    findings=find(sources,observations)
    incomplete=any(s['freshness']!='fresh' for s in sources)
    snapshot={'schema_version':SCHEMA,'synthetic_fixture':synthetic_fixture,'captured_at':time_iso(now),'capture_status':'partial','observability_completeness': 'partial' if incomplete else 'vm100_vm101_only','overall_system_health':'unknown','sources':sources,'observations':observations,'findings':findings,'truncated_sources':truncated,'coverage':{'machines':['vm100','vm101'],'vm121':'not_implemented','capacity':'not_implemented','client_activity_per_selector':'not_implemented','vm101_strict_icmp':'not_collected_no_active_probe'}}
    def j(o):return (json.dumps(o,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
    payload={'README.md':('# B4 v2b offline prototype\n\nNo secrets, per-peer identifiers or raw logs. VM100/VM101 snapshot only. This is not a deployed admin button or production API.\n').encode(), 'report.md':build_report(snapshot).encode(),'snapshot.json':j(snapshot),'events.jsonl':b''.join((json.dumps(e,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode() for e in events)}
    manifest={'schema_version':SCHEMA,'capture_started_at':time_iso(now),'capture_finished_at':time_iso(),'files':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in payload.items()},'truncated':bool(truncated),'truncated_sources':truncated,'event_count':len(events),'request_id':uuid.uuid4().hex[:12],'scope':'vm100_vm101_prototype'}
    payload['manifest.json']=j(manifest)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    # Never write into static web roots / Machine Git / Latest; output only to caller-chosen private folder.
    name=f"WG_PAID_MGTS_DIAGNOSTICS_{datetime.now(UTC).strftime('%Y%m%d-%H%M%SZ')}_{manifest['request_id']}.zip"
    dest=output/name
    fd=os.open(dest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as fh:
            with ZipFile(fh,'w',ZIP_DEFLATED,compresslevel=8) as z:
                for n,b in payload.items():z.writestr(n,b)
    except BaseException:
        dest.unlink(missing_ok=True);raise
    if dest.stat().st_size>1024*1024:
        dest.unlink();raise ValueError('ZIP_TOO_LARGE')
    return dest,snapshot


def main():
    p=argparse.ArgumentParser()
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--fixture',type=Path,help='Offline synthetic source tree')
    group.add_argument('--live',action='store_true',help='Read only from VM130 to VM100/101 via existing PVE SSH')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.live:
        if os.geteuid()==0: p.error('Refuse live as root; use VM130 ops')
        allowed=Path('/home/ops').resolve()
        try: args.output.resolve().relative_to(allowed)
        except ValueError: p.error('Live output must be a private folder inside /home/ops')
        fetch=ssh_capture
    else:fetch=lambda h:fixture_capture(h,args.fixture)
    dest,snapshot=generate(fetch,args.output,synthetic_fixture=bool(args.fixture))
    print(f'RESULT=PASS_B4_V2B_PROTOTYPE_CAPTURE')
    print(f'ZIP={dest}')
    print(f'CAPTURE_STATUS={snapshot["capture_status"]}')
    print(f'FINDINGS={len(snapshot["findings"])}')

if __name__=='__main__': main()
