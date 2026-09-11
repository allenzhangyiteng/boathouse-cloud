"""Enforced app limits, explicit storage approvals, and plain-language usage."""
from contextlib import contextmanager
import fcntl
import json
from pathlib import Path
import time
import threading
_local=threading.local()

import httpx
import psycopg
from . import config, db

GIB=1024**3

class ResourceError(RuntimeError):
    pass

def broker(method,path,body=None):
    transport=httpx.HTTPTransport(uds=config.RESOURCE_SOCKET)
    try:
        with httpx.Client(transport=transport,base_url='http://resource-agent',timeout=35) as client:
            r=client.request(method,path,json=body)
        result=r.json()
        if r.status_code>=400: raise ResourceError(result.get('error','Storage service unavailable.'))
        if not isinstance(result,dict): raise ResourceError('Storage service unavailable.')
        return result
    except (httpx.HTTPError,ValueError) as e:
        raise ResourceError('We cannot verify your storage limit right now. Your existing data is safe; try again shortly.') from e

def limits(tool):
    from . import deploy
    key=deploy.resource_key(tool)
    with db.conn() as c:
        c.execute('INSERT OR IGNORE INTO resource_limits(resource_key,workspace_id,slug) VALUES(?,?,?)',
                  (key,tool['workspace_id'],tool['slug']))
        return dict(c.execute('SELECT * FROM resource_limits WHERE resource_key=?',(key,)).fetchone())

def ensure(tool):
    from . import deploy
    row=limits(tool)
    return broker('POST','/resources/'+deploy.resource_key(tool),{'limit_bytes':row['limit_bytes']})

def measure(tool):
    from . import deploy
    result=broker('GET','/resources/'+deploy.resource_key(tool))
    if not result.get('enforced'): raise ResourceError('This app does not yet have an enforced storage limit.')
    return result

def current(tool):
    row=limits(tool)
    return public(row)

def public(row):
    used=row.get('storage_bytes')
    percent=round(used/row['limit_bytes']*100,1) if used is not None else None
    if row['state']=='paused':
        message=row.get('reason') or 'This app reached its storage limit. Its existing data is safe. Increase capacity to resume.'
    elif percent is not None and percent>=80:
        message=f'This app has used {percent:g}% of its storage. Increase capacity or remove unused files before it fills up.'
    elif row.get('reason'): message=row['reason']
    else: message='Your app is within its limits.' if used is not None else 'Your first usage reading will appear shortly.'
    return {'tool':row['slug'],'state':row['state'],'message':message,'storage_bytes':used,
            'storage_limit_bytes':row['limit_bytes'],'storage_percent':percent,
            'memory_bytes':row.get('memory_bytes'),'memory_limit_bytes':512*1024**2,
            'cpu_percent':row.get('cpu_percent'),'cpu_limit_cores':config.TOOL_CPUS,
            'sampled_at':row.get('sampled_at'),'sample_stale':not row.get('sampled_at') or time.time()-row['sampled_at']>config.RESOURCE_INTERVAL*3,
            'included_storage_bytes':GIB,'extra_storage_cents_per_gb_month':25,
            'upgrade_requires_approval':True,'hard_limits_enabled':config.RESOURCE_GUARD}

def blocked(tool):
    if not config.RESOURCE_GUARD: return None
    row=limits(tool)
    return public(row)['message'] if row['state']=='paused' else None

def admission(tool=None,allow_paused=False):
    if not config.RESOURCE_GUARD: return
    from . import deploy
    health=broker('GET','/health')
    if not health.get('enforced') or health['host_available_bytes'] < health['host_reserve_bytes']:
        raise ResourceError('We are preserving capacity for existing apps. Contact support to review this deployment; no extra capacity will be charged.')
    live=deploy.client().containers.list(filters={'label':'boathouse.tool'})
    existing=tool is not None and any(c.name==deploy.ctr_name(tool) for c in live)
    if len(live)>=config.TOOL_MAX_RUNNING and not existing:
        raise ResourceError('This host has reached its safe app capacity. Contact support to place the app; no new hosting charge has started.')
    if tool is not None and not allow_paused and blocked(tool): raise ResourceError(blocked(tool))

def quote(tool,ws,gib,by):
    if not config.RESOURCE_GUARD:
        raise ResourceError('Storage upgrades are not enabled on this host.')
    if type(gib) is not int or not 1<=gib<=config.TOOL_MAX_STORAGE_GIB:
        raise ResourceError(f'Choose a whole number of GB, up to {config.TOOL_MAX_STORAGE_GIB}. Larger apps need a reviewed plan.')
    row=limits(tool); requested=gib*GIB
    if requested<=row['limit_bytes']: raise ResourceError('Choose a capacity above the current limit. No storage reduction or charge was made.')
    opid=db.new_id('capacity'); now=time.time(); maximum=(gib-1)*25
    with db.conn() as c:
        c.execute('INSERT INTO resource_quotes VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                  (opid,ws['id'],row['resource_key'],row['limit_bytes'],requested,maximum,by,now,now+900))
    return {'quote_id':opid,'storage_limit_gb':gib,'max_extra_monthly_cents':maximum,'expires':now+900,
            'message':f'Increase this app to {gib} GB. The first GB stays included; used extra storage costs $0.25 per GB per calendar month, at most ${maximum/100:.2f}/month at this limit. This is additional to hosting. Confirm only after the owner approves.'}

def confirm(tool,ws,opid,maximum,by):
    from . import deploy
    folder=config.STATE_DIR/'locks';folder.mkdir(exist_ok=True)
    with (folder/('capacity-'+deploy.resource_key(tool))).open('a') as lock, deploy.operation(tool):
        fcntl.flock(lock,fcntl.LOCK_EX)
        with db.conn() as c:
            q=c.execute('SELECT * FROM resource_quotes WHERE id=? AND workspace_id=? AND resource_key=?',
                        (opid,ws['id'],deploy.resource_key(tool))).fetchone()
        if not q or type(maximum) is not int or maximum!=q['max_extra_monthly_cents'] or q['created_by']!=by:
            raise ResourceError('That capacity approval does not match this app, owner, and maximum price.')
        if q['confirmed']: return current(tool)
        if q['expires']<time.time(): raise ResourceError('That capacity quote expired. Request a fresh quote.')
        row=limits(tool)
        if row['limit_bytes']!=q['old_limit_bytes']: raise ResourceError('This app’s capacity changed. Request a fresh quote.')
        result=broker('POST','/resources/'+deploy.resource_key(tool),{'limit_bytes':q['limit_bytes']})
        with db.conn() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute("UPDATE resource_limits SET limit_bytes=?,approved_by=?,approved_at=?,state='ok',reason=NULL,last_warning=0 WHERE resource_key=?",
                      (q['limit_bytes'],by,time.time(),q['resource_key']))
            c.execute('UPDATE resource_quotes SET confirmed=? WHERE id=?',(time.time(),opid))
            c.execute('COMMIT')
        db.audit(by,'resource.capacity_approved',tool['slug'],{'limit_bytes':q['limit_bytes'],'max_extra_monthly_cents':maximum},ws['id'])
        if row['state']=='paused' and not ws['paused']:
            try:
                with host_operation():
                    admission(tool,allow_paused=True)
                    deploy.client().containers.get(deploy.ctr_name(tool)).start()
            except ResourceError: pass
        _sample(tool,result)
        return current(tool)

def notify(tool,level,message):
    now=time.time()
    ws=db.workspace_by_id(tool['workspace_id'])
    with db.conn() as c:
        owners=c.execute("SELECT email FROM users WHERE workspace_id=? AND role='owner'",(tool['workspace_id'],)).fetchall()
        for owner in owners:
            if c.execute('SELECT 1 FROM resource_notifications WHERE resource_key=? AND level=? AND email=? AND created>?',(tool['resource_key'],level,owner['email'],now-3600)).fetchone():continue
            c.execute('INSERT OR IGNORE INTO resource_notifications(id,resource_key,level,email,subject,body,created) VALUES(?,?,?,?,?,?,?)',
                      (db.new_id('usage'),tool['resource_key'],level,owner['email'],
                       'Boat House: '+tool['name']+' capacity update',message+'\n\nOpen https://'+config.PLATFORM_DOMAIN+'/account?ws='+ws['slug']+' to review capacity, or ask your connected agent to check app usage.',now))

def sample(tool,result=None):
    from . import deploy
    with deploy.operation(tool): return _sample(tool,result)

def _sample(tool,result=None):
    from . import deploy
    result=result or measure(tool); row=limits(tool)
    used=result['storage_bytes']; limit=result['limit_bytes']
    memory=cpu=None; high_since=None
    try:
        container=deploy.client().containers.get(deploy.ctr_name(tool))
        s=container.stats(stream=False,one_shot=False) if container.status=='running' else {}
        m=s.get('memory_stats',{}); memory=max(0,m.get('usage',0)-m.get('stats',{}).get('inactive_file',0))
        a=s.get('cpu_stats',{}); b=s.get('precpu_stats',{})
        delta=a.get('cpu_usage',{}).get('total_usage',0)-b.get('cpu_usage',{}).get('total_usage',0)
        system=a.get('system_cpu_usage',0)-b.get('system_cpu_usage',0)
        if system>0: cpu=max(0,delta/system*a.get('online_cpus',1)*100)
        if cpu is not None and cpu>=80: high_since=row['high_cpu_since'] or time.time()
    except Exception: pass
    ratio=max(used/limit,result.get('inodes',0)/result.get('inode_limit',200000))
    level=100 if ratio>=.995 else 90 if ratio>=.90 else 80 if ratio>=.80 else 0
    reason=None
    if level==100: reason='This app reached its storage limit and is paused to protect your data. Approve more capacity to resume; nothing was deleted.'
    elif memory is not None and memory>=512*1024**2*.9: reason='This app is approaching its memory limit. Ask your agent to reduce memory use, or contact support for a larger plan.'
    elif high_since and time.time()-high_since>=300: reason='This app has sustained high CPU use. Ask your agent to optimize it, or contact support for a larger plan.'
    state='paused' if level==100 else 'ok'
    if level==100:
        deploy.stop(tool)
    elif row['state']=='paused' and ratio<.90:
        ws=db.workspace_by_id(tool['workspace_id'])
        if not ws['paused']:
            try:
                with host_operation():
                    admission(tool,allow_paused=True)
                    deploy.client().containers.get(deploy.ctr_name(tool)).start()
            except ResourceError:
                state='paused';reason='This app is waiting for host capacity. Contact support; your data is kept.'
    elif row['state']=='paused': state='paused'; reason=row['reason']
    with db.conn() as c:
        c.execute('UPDATE resource_limits SET sampled_at=?,storage_bytes=?,memory_bytes=?,cpu_percent=?,state=?,reason=?,high_cpu_since=?,last_warning=? WHERE resource_key=?',
                  (time.time(),used,memory,cpu,state,reason,high_since,max(row['last_warning'],level) if ratio>=.70 else 0,tool['resource_key']))
    if level>row['last_warning']:
        message=reason if level==100 else f'{tool["name"]} has used {round(ratio*100)}% of its storage. Increase capacity or remove unused files before it fills up. Your existing data is safe.'
        notify(tool,level,message)
    if reason and level!=100 and reason!=row['reason']: notify(tool,70,reason)

def monitor_once():
    if not config.RESOURCE_GUARD: return
    from . import mail
    with db.conn() as c: tools=c.execute('SELECT * FROM tools WHERE current_release_id IS NOT NULL').fetchall()
    for tool in tools:
        try: sample(tool)
        except Exception:
            db.audit('resources','resource.sample_failed',tool['slug'],None,tool['workspace_id'])
    # Application roles cannot increase their connection limit or temp-file limit.
    # A watchdog also terminates work that overrides client-settable SQL timeouts.
    with psycopg.connect(host=config.PG_HOST,user='postgres',password=config.PG_ADMIN_PASSWORD,dbname='postgres',autocommit=True) as pg:
        pg.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename LIKE 't\\_%' AND pid<>pg_backend_pid() AND ((state='active' AND query_start<clock_timestamp()-(%s * interval '1 second')) OR (state='idle in transaction' AND state_change<clock_timestamp()-(%s * interval '1 second')))",
                   (config.PG_QUERY_SECONDS,config.PG_QUERY_SECONDS))
    with db.conn() as c:
        pending=c.execute('SELECT * FROM resource_notifications WHERE sent IS NULL AND (last_attempt IS NULL OR last_attempt<?) ORDER BY created LIMIT 20',(time.time()-300,)).fetchall()
    for event in pending:
        with db.conn() as c:c.execute('UPDATE resource_notifications SET last_attempt=? WHERE id=?',(time.time(),event['id']))
        if mail.send(event['email'],event['subject'],event['body']):
            with db.conn() as c:c.execute('UPDATE resource_notifications SET sent=? WHERE id=?',(time.time(),event['id']))


@contextmanager
def host_operation():
    if not config.RESOURCE_GUARD or getattr(_local,'host_lock',False):
        yield
        return
    folder=config.STATE_DIR/'locks';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'host-deployment.lock').open('a') as lock:
        if not _try_lock(lock):
            raise ResourceError('Another app is publishing. Please retry in a moment; your current app stays online.')
        _local.host_lock=True
        try: yield
        finally:
            _local.host_lock=False
            fcntl.flock(lock,fcntl.LOCK_UN)

def _try_lock(lock):
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); return True
    except BlockingIOError: return False
