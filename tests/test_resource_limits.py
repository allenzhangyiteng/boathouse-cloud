"""Approval, cross-tenant access, notifications and host admission invariants."""
import io
import tarfile
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_resource_isolation import state, insert_tool, headers
from app import bounded_build, billing, config, db, deploy, resources, main
from fastapi.testclient import TestClient

@pytest.fixture
def guarded(state,monkeypatch):
    monkeypatch.setattr(config,'RESOURCE_GUARD',True)
    t=insert_tool('a');other=insert_tool('b'); events=[];stops=[];starts=[]
    allocations={}
    def broker(method,path,body=None):
        if path=='/health':return {'enforced':True,'host_available_bytes':20*1024**3,'host_reserve_bytes':8*1024**3}
        key=path.rsplit('/',1)[-1]
        if method=='POST': allocations[key]={'storage_bytes':0,'limit_bytes':body['limit_bytes'],'enforced':True}
        return allocations.get(key,{'storage_bytes':0,'limit_bytes':1024**3,'enforced':True})
    ctr=SimpleNamespace(name=deploy.ctr_name(t),start=lambda:starts.append(t['id']),stats=lambda **kw:{})
    containers=SimpleNamespace(get=lambda name:ctr,list=lambda **kw:[ctr])
    monkeypatch.setattr(deploy,'client',lambda:SimpleNamespace(containers=containers))
    monkeypatch.setattr(deploy,'stop',lambda t:stops.append(t['id']))
    monkeypatch.setattr(resources,'broker',broker)
    monkeypatch.setattr(resources,'notify',lambda t,l,m:events.append((t['id'],l,m)))
    return t,other,events,stops,starts,containers

def usage(fraction):return {'storage_bytes':int(fraction*1024**3),'limit_bytes':1024**3,'enforced':True}

def test_thresholds_notify_once_pause_only_offending_app_and_hysteresis(guarded):
    t,other,events,stops,starts,_=guarded
    for n in (.81,.82,.91,.92,.999,.999):resources.sample(t,usage(n))
    assert [e[1] for e in events]==[80,90,100]
    assert set(stops)=={t['id']} and resources.blocked(t) and not resources.blocked(other)
    resources.sample(t,usage(.94));assert resources.blocked(t) and starts==[]
    resources.sample(t,usage(.50));assert not resources.blocked(t) and len(starts)==1

def test_capacity_requires_matching_owner_amount_and_unexpired_quote(guarded):
    t,other,*_=guarded; ws=db.workspace_by_id('ws_a')
    q=resources.quote(t,ws,2,'a@test.invalid')
    assert q['max_extra_monthly_cents']==25 and resources.current(t)['storage_limit_bytes']==1024**3
    for tool,owner,amount in [(other,'a@test.invalid',25),(t,'b@test.invalid',25),(t,'a@test.invalid',0),(t,'a@test.invalid',True)]:
        with pytest.raises(resources.ResourceError):resources.confirm(tool,ws,q['quote_id'],amount,owner)
    with db.conn() as c:c.execute('UPDATE resource_quotes SET expires=0 WHERE id=?',(q['quote_id'],))
    with pytest.raises(resources.ResourceError,match='expired'):resources.confirm(t,ws,q['quote_id'],25,'a@test.invalid')

def test_capacity_replay_applies_once_and_never_debits_credit(guarded):
    t,*_=guarded;ws=db.workspace_by_id('ws_a');q=resources.quote(t,ws,3,'a@test.invalid')
    before=billing.balance(ws['id'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:resources.confirm(t,ws,q['quote_id'],50,'a@test.invalid'),range(4)))
    assert all(r['storage_limit_bytes']==3*1024**3 for r in results)
    assert billing.balance(ws['id'])==before
    with db.conn() as c:assert c.execute("SELECT count(*) FROM audit WHERE action='resource.capacity_approved'").fetchone()[0]==1

def test_host_admission_refuses_ninth_app_but_allows_update(guarded):
    t,other,_,_,_,containers=guarded
    containers.list=lambda **kw:[SimpleNamespace(name=deploy.ctr_name(t))]+[SimpleNamespace(name='tool-'+str(n)) for n in range(7)]
    resources.admission(t)
    with pytest.raises(resources.ResourceError,match='safe app capacity'):resources.admission(other)

def test_capacity_api_blocks_guest_and_cross_workspace(guarded,monkeypatch):
    t,other,*_=guarded
    with TestClient(main.app) as client:
        h=headers('a');q=client.post('/api/tools/tracker/capacity',headers=h,json={'storage_gb':2})
        assert q.status_code==200
        response=client.post('/api/tools/tracker/capacity',headers=headers('b'),json={'confirm':True,'quote_id':q.json()['quote_id'],'max_extra_monthly_cents':25})
        assert response.status_code==409
        with db.conn() as c:c.execute("UPDATE users SET role='guest' WHERE workspace_id='ws_a'")
        assert client.post('/api/tools/tracker/capacity',headers=h,json={'storage_gb':2}).status_code==403

@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('symlink','link'),('device','device')])
def test_source_extraction_refuses_escape_links_and_devices(tmp_path,name,kind):
    b=io.BytesIO()
    with tarfile.open(fileobj=b,mode='w') as tf:
        m=tarfile.TarInfo(name)
        if kind=='link':m.type=tarfile.SYMTYPE;m.linkname='/etc'
        if kind=='device':m.type=tarfile.CHRTYPE
        tf.addfile(m)
    with pytest.raises(ValueError):bounded_build.extract(b.getvalue(),tmp_path)


def test_declared_anonymous_volume_cannot_bypass_quota(guarded):
    t,*_=guarded
    client=SimpleNamespace(images=SimpleNamespace(get=lambda tag:SimpleNamespace(attrs={'Config':{'Volumes':{'/unmetered':{}}}})))
    with pytest.raises(ValueError,match='unbounded volume'):deploy._start(client,'synthetic',t,{})

def test_compressed_image_layer_cannot_bypass_unpacked_limit(tmp_path):
    import gzip,json
    large=tarfile.TarInfo('huge');large.size=bounded_build.MAX_IMAGE+1
    compressed=gzip.compress(large.tobuf()+bytes(1024))
    path=tmp_path/'image.tar'
    with tarfile.open(path,'w') as out:
        for name,body in [('manifest.json',json.dumps([{'Layers':['layer.tar.gz']}]).encode()),('layer.tar.gz',compressed)]:
            m=tarfile.TarInfo(name);m.size=len(body);out.addfile(m,io.BytesIO(body))
    with pytest.raises(ValueError,match='unpacked app image'):bounded_build.check_image_size(path)
