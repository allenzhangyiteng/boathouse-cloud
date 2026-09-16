"""The broker fails closed and never accepts a caller-supplied unit name."""
import hashlib, importlib.util
from pathlib import Path
import pytest
SPEC=importlib.util.spec_from_file_location('quota_broker',Path(__file__).parents[1]/'deploy/resource-agent.py')
guard=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(guard)
@pytest.fixture
def native(tmp_path,monkeypatch):
    unit=tmp_path/'units';unit.mkdir();root=tmp_path/'cgroups';root.mkdir()
    monkeypatch.setattr(guard,'UNIT_ROOT',unit);monkeypatch.setattr(guard,'CGROUP_ROOT',root)
    name='boathouse-org'+hashlib.sha256(b'ws_test').hexdigest()[:24]+'.slice'
    folder=root/name;folder.mkdir()
    for file,value in {'memory.max':'536870912','memory.swap.max':'0','cpu.max':'50000 100000','memory.current':'1000','memory.events':'oom_kill 0'}.items():(folder/file).write_text(value)
    calls=[];monkeypatch.setattr(guard,'command',lambda args:calls.append(args))
    return unit,folder,calls
@pytest.mark.parametrize('ident',['../../etc/passwd','system.slice','ws_a/b','ws_a\n[Service]','ws_','ws_A','ws_'+('a'*65)])
def test_invalid_id_never_reaches_systemd(native,ident):
    with pytest.raises(ValueError):guard.organization(ident)
    assert native[2]==[] and list(native[0].iterdir())==[]
def test_native_limits_verified_and_persisted(native):
    result=guard.organization('ws_test');assert result['enforced'] and result['cpu_limit_cores']==.5
    assert 'MemoryMax=536870912' in (native[0]/result['slice']).read_text()
    native[2].clear();guard.organization('ws_test',create=False);assert not native[2]
@pytest.mark.parametrize('file,value',[('memory.max','max'),('memory.swap.max','max'),('cpu.max','100000 100000')])
def test_incorrect_kernel_limit_refuses_publication(native,file,value):
    (native[1]/file).write_text(value)
    with pytest.raises(RuntimeError,match='not enforced'):guard.organization('ws_test')
