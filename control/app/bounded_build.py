"""Disposable rootless builder with kernel limits and no production credentials/socket."""
import io
import json
from pathlib import Path, PurePosixPath
import tarfile
import time
import uuid
from . import config, deploy, resources, sandbox

IMAGE='moby/buildkit@sha256:80b15f0735e87bab7bf59ec4d695dfb4a7cfb25521cf56dc75d6f256285b63ef'
MAX_EXPANDED=256*1024**2
MAX_IMAGE=512*1024**2

def extract(context,destination):
    with tarfile.open(fileobj=io.BytesIO(context)) as tf:
        members=tf.getmembers()
        if len(members)>10000 or sum(max(0,m.size) for m in members)>MAX_EXPANDED:
            raise ValueError('The uploaded app expands beyond the 256 MB source limit. Remove generated files and dependencies.')
        for member in members:
            path=PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError('The upload contains an unsafe path or link. Upload regular project files only.')
        tf.extractall(destination,members=members,filter='data')

def build(tool,seq,context):
    # Caller holds the host deployment lock: one build at a time.
    key='build-'+uuid.uuid4().hex
    allocation=resources.broker('POST','/resources/'+key,{'kind':'build','limit_bytes':config.BUILD_STORAGE_BYTES})
    home=Path(allocation['files_path']); ctr=None
    tag=deploy.image_tag(tool,seq)
    try:
        for folder in ('context','.local','.local/tmp','.local/share','.local/share/buildkit'):
            path=home/folder;path.mkdir(parents=True,exist_ok=True);path.chmod(0o777)
        extract(context,home/'context')
        c=deploy.client()
        declared=(c.images.get(IMAGE).attrs.get('Config') or {}).get('Volumes') or {}
        if set(declared)-{'/home/user/.local/share/buildkit'}:raise RuntimeError('The builder image declares an unexpected cache volume.')
        binds={str(home):{'bind':'/home/user','mode':'rw'},str(home/'.local/share/buildkit'):{'bind':'/home/user/.local/share/buildkit','mode':'rw'}}
        host=c.api.create_host_config(network_mode='bh-build',binds=binds,
            mem_limit=config.BUILD_MEMORY,memswap_limit=config.BUILD_MEMORY,nano_cpus=1000000000,pids_limit=256,cpu_shares=128,
            read_only=True,tmpfs={'/tmp':'rw,nosuid,size=256m','/run':'rw,nosuid,size=16m'},
            security_opt=['seccomp='+sandbox.profile(builder=True),'apparmor=unconfined'],
            log_config={'type':'json-file','config':{'max-size':'1m','max-file':'1'}})
        # Docker CLI's systempaths=unconfined translates to these API fields.
        # The rootless build can mount /proc inside its own user/PID namespace.
        host['MaskedPaths']=[];host['ReadonlyPaths']=[]
        created=c.api.create_container(IMAGE,['build','--frontend','dockerfile.v0','--local','context=/home/user/context',
            '--local','dockerfile=/home/user/context','--output',f'type=docker,name={tag},dest=/home/user/result.tar'],
            name='bh-'+key,entrypoint='buildctl-daemonless.sh',host_config=host,volumes=['/home/user','/home/user/.local/share/buildkit'],
            environment={'BUILDKITD_FLAGS':'--oci-worker-snapshotter=native','XDG_RUNTIME_DIR':'/tmp/runtime'},labels={'boathouse.build':key})
        ctr=c.containers.get(created['Id']);ctr.start();ctr.reload()
        if any(m['Type']!='bind' or not (m['Source']==str(home) or m['Source'].startswith(str(home)+'/')) for m in ctr.attrs['Mounts']):
            raise RuntimeError('The builder has storage outside its quota; build stopped.')
        deadline=time.monotonic()+config.BUILD_TIMEOUT
        while True:
            ctr.reload()
            if ctr.status in ('exited','dead'): break
            if time.monotonic()>deadline: raise RuntimeError('The build exceeded its five-minute limit. Ask your agent to reduce build work.')
            time.sleep(1)
        log=ctr.logs(tail=200).decode(errors='replace')[-16000:]
        if ctr.attrs['State']['ExitCode']!=0:
            raise RuntimeError('The bounded build failed (maximum 1 GB memory and 3 GB temporary storage).\n'+log)
        result=home/'result.tar'
        if not result.is_file() or result.stat().st_size>MAX_IMAGE:
            raise RuntimeError('The app image exceeds 512 MB. Ask your agent to use a smaller runtime image.')
        check_image_size(result)
        resources.admission(tool)
        health=resources.broker('GET','/health')
        if health['host_available_bytes']<health['host_reserve_bytes']+2*MAX_IMAGE:
            raise RuntimeError('The host is preserving free space. Contact support before publishing this app.')
        with result.open('rb') as archive:
            for event in c.api.load_image(archive,quiet=True):
                if event.get('error'): raise RuntimeError('The built app could not be imported.')
        c.images.get(tag)
        return tag,log
    finally:
        if ctr is not None: ctr.remove(force=True,v=True)
        resources.broker('DELETE','/resources/'+key)


def check_image_size(path):
    """Compressed layers must not bypass the final uncompressed image limit."""
    total=0
    with tarfile.open(path,'r:*') as archive:
        manifest=json.load(archive.extractfile('manifest.json'))
        if len(manifest)!=1:raise ValueError('The build must produce one app image.')
        for layer in manifest[0]['Layers']:
            with tarfile.open(fileobj=archive.extractfile(layer),mode='r|*') as contents:
                for member in contents:
                    total+=max(0,member.size)
                    if total>MAX_IMAGE:raise ValueError('The unpacked app image exceeds 512 MB. Use a smaller runtime image.')
