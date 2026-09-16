#!/usr/bin/env python3
"""Small root-owned Unix-socket broker for XFS project quotas.

The control service may allocate a validated resource ID and a bounded quota.
There is no arbitrary path/command interface. Customer containers cannot access
the socket. Set up the dedicated filesystem before starting this service.
"""
import fcntl
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socketserver
import sqlite3
import subprocess

ROOT = Path(os.environ.get('BH_QUOTA_ROOT', '/var/lib/boathouse/storage'))
STATE = Path(os.environ.get('BH_GUARD_STATE', '/var/lib/boathouse/guard'))
SOCKET = Path(os.environ.get('BH_GUARD_SOCKET', '/run/boathouse-guard/agent.sock'))
GIB = 1024**3
MAX_DATA = 10*GIB
RESERVE = int(os.environ.get('BH_HOST_DISK_RESERVE', str(8*GIB)))
UNIT_ROOT = Path('/etc/systemd/system')
CGROUP_ROOT = Path('/sys/fs/cgroup/boathouse.slice')

def organization(workspace,create=True):
    """A kernel-enforced parent for all runtime containers in one organization.

    Only a derived slice name can be managed; no caller-supplied unit, path or
    resource settings are accepted. Persistent units restore limits on reboot.
    """
    if not re.fullmatch(r'ws_[a-z0-9]{1,64}',workspace):
        raise ValueError('Invalid organization identity.')
    name='boathouse-org'+hashlib.sha256(workspace.encode()).hexdigest()[:24]+'.slice'
    folder=CGROUP_ROOT/name
    if not create and not folder.exists():
        return {'slice':name,'enforced':False,'memory_bytes':0,'memory_limit_bytes':536870912,'cpu_limit_cores':0.5,'oom_kills':0}
    body='[Unit]\nDescription=Boat House organization runtime\n[Slice]\nMemoryAccounting=yes\nCPUAccounting=yes\nMemoryMax=536870912\nMemorySwapMax=0\nCPUQuota=50%\nTasksMax=1024\n'
    path=UNIT_ROOT/name
    if path.is_symlink(): raise ValueError('Invalid organization unit.')
    if create and (not path.exists() or path.read_text()!=body):
        tmp=path.with_suffix('.slice.new')
        tmp.write_text(body);tmp.chmod(0o644);tmp.replace(path)
        command(['systemctl','daemon-reload'])
    if create: command(['systemctl','start',name])
    if (folder/'memory.max').read_text().strip()!='536870912' or (folder/'memory.swap.max').read_text().strip()!='0':
        raise RuntimeError('The shared memory limit is not enforced; publishing is disabled.')
    quota,period=map(int,(folder/'cpu.max').read_text().split())
    if quota/period != 0.5: raise RuntimeError('The shared compute limit is not enforced; publishing is disabled.')
    events=dict(line.split() for line in (folder/'memory.events').read_text().splitlines())
    return {'slice':name,'enforced':True,'memory_limit_bytes':536870912,
            'memory_bytes':int((folder/'memory.current').read_text()),'cpu_limit_cores':0.5,
            'oom_kills':int(events.get('oom_kill','0'))}

def command(args):
    p = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if p.returncode:
        raise RuntimeError('Resource operation failed; the operator must inspect the resource service.')
    return p.stdout.strip()

def filesystem():
    result = command(['findmnt','-no','FSTYPE,OPTIONS','--target',str(ROOT)]).split()
    if not result or result[0] != 'xfs' or not {'prjquota','pquota'} & set(result[-1].split(',')):
        raise RuntimeError('Hard storage quotas are not mounted; new allocations are disabled.')
    fs = os.statvfs(ROOT)
    host = os.statvfs(STATE)
    return {'enforced':True,'total_bytes':fs.f_blocks*fs.f_frsize,
            'available_bytes':fs.f_bavail*fs.f_frsize,
            'host_available_bytes':host.f_bavail*host.f_frsize,'host_reserve_bytes':RESERVE}

def connect():
    STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    c = sqlite3.connect(STATE/'allocations.sqlite3',timeout=30,isolation_level=None)
    c.row_factory=sqlite3.Row
    c.execute('CREATE TABLE IF NOT EXISTS allocations(project INTEGER PRIMARY KEY AUTOINCREMENT,resource TEXT UNIQUE NOT NULL,kind TEXT NOT NULL,limit_bytes INTEGER NOT NULL)')
    return c

def valid(resource):
    if not re.fullmatch(r'[a-z][a-z0-9-]{1,63}',resource):
        raise ValueError('Invalid resource identity.')

def usage(row):
    fs=filesystem()
    report=command(['xfs_quota','-x','-c','report -p -b -n -N',str(ROOT)])
    for line in report.splitlines():
        parts=line.split()
        if parts and parts[0].lstrip('#') == str(row['project']):
            used,_,hard=map(int,parts[1:4])
            inodes=0
            for line in command(['xfs_quota','-x','-c','report -p -i -n -N',str(ROOT)]).splitlines():
                fields=line.split()
                if fields and fields[0].lstrip('#')==str(row['project']): inodes=int(fields[1])
            if hard*1024 != row['limit_bytes']:
                raise RuntimeError('The storage quota differs from its allocation; new writes need operator review.')
            return {'resource':row['resource'],'kind':row['kind'],'storage_bytes':used*1024,
                    'limit_bytes':row['limit_bytes'],'inodes':inodes,'inode_limit':200000,'files_path':str(ROOT/row['resource']/'files'),
                    'postgres_path':'/boathouse-storage/'+row['resource']+'/postgres',
                    'project':row['project'],'enforced':True,**fs}
    raise RuntimeError('The storage quota could not be read; retry shortly.')

def allocation(resource,limit=None,kind='data'):
    valid(resource)
    with connect() as c, (STATE/'allocation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        row=c.execute('SELECT * FROM allocations WHERE resource=?',(resource,)).fetchone()
        if limit is None:
            if row is None: raise KeyError('This app has no storage allocation yet.')
            return usage(row)
        if type(limit) is not int or limit % 1024 or kind not in ('data','build'):
            raise ValueError('Use an integer storage limit in whole KiB.')
        maximum=MAX_DATA if kind=='data' else 3*GIB
        if not 16*1024**2 <= limit <= maximum:
            raise ValueError('This storage request needs operator review.')
        fs=filesystem()
        if fs['host_available_bytes'] < RESERVE:
            raise ValueError('The host is preserving free space. Existing data is safe; contact support for more capacity.')
        reserved=c.execute('SELECT coalesce(sum(limit_bytes),0) FROM allocations WHERE resource!=?',(resource,)).fetchone()[0]
        build_reserve=3*GIB if kind=='data' and not c.execute("SELECT 1 FROM allocations WHERE kind='build'").fetchone() else 0
        if reserved+limit+build_reserve > fs['total_bytes']*0.80:
            raise ValueError('This host has no unreserved storage for that upgrade. Contact support; no extra storage charge was approved.')
        if row and (row['kind']!=kind or limit < row['limit_bytes']):
            raise ValueError('Storage cannot be silently reduced or reassigned.')
        c.execute('INSERT OR IGNORE INTO allocations(resource,kind,limit_bytes) VALUES(?,?,?)',(resource,kind,limit))
        row=c.execute('SELECT * FROM allocations WHERE resource=?',(resource,)).fetchone()
        folder=ROOT/resource
        folder.mkdir(mode=0o711,exist_ok=True)
        if folder.is_symlink(): raise ValueError('Storage path must not be a symbolic link.')
        folder.chmod(0o711)
        for sub in ['files','postgres'] if kind=='data' else ['files']:
            path=folder/sub; path.mkdir(mode=0o755,exist_ok=True)
            if path.is_symlink(): raise ValueError('Storage path must not be a symbolic link.')
            if sub=='postgres': os.chown(path,999,999); path.chmod(0o700)
            else: path.chmod(0o777)  # mounted only into this app; support non-root images
        project=row['project']
        command(['xfs_quota','-x','-c',f'project -s -p {folder} {project}',str(ROOT)])
        command(['xfs_quota','-x','-c',f'limit -p bsoft={limit//1024}k bhard={limit//1024}k ihard=200000 {project}',str(ROOT)])
        c.execute('UPDATE allocations SET limit_bytes=? WHERE resource=?',(limit,resource))
        return usage(c.execute('SELECT * FROM allocations WHERE resource=?',(resource,)).fetchone())

def remove(resource,purge=False):
    valid(resource)
    with connect() as c, (STATE/'allocation.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        row=c.execute('SELECT * FROM allocations WHERE resource=?',(resource,)).fetchone()
        if not row: return {'removed':False}
        folder=ROOT/resource
        # Databases/files need explicit purge handling elsewhere. Automatic cleanup
        # is deliberately restricted to disposable build allocations.
        if row['kind']!='build' and not purge: raise ValueError('Persistent app storage needs explicit purge confirmation.')
        if row['kind']=='data' and (folder/'postgres').exists() and any((folder/'postgres').glob('PG_*/[0-9]*')): raise ValueError('Drop the app database and tablespace before purging files.')
        if folder.is_symlink() or folder.parent!=ROOT: raise ValueError('Invalid storage path.')
        shutil.rmtree(folder)
        command(['xfs_quota','-x','-c',f'limit -p bsoft=0 bhard=0 ihard=0 {row["project"]}',str(ROOT)])
        c.execute('DELETE FROM allocations WHERE resource=?',(resource,))
        return {'removed':True}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def handle_request(self):
        try:
            if self.path=='/health' and self.command=='GET': result=filesystem()
            elif self.path.startswith('/organizations/') and self.command in ('POST','GET'):
                result=organization(self.path.removeprefix('/organizations/'),create=self.command=='POST')
            elif self.path.startswith('/resources/'):
                resource=self.path.removeprefix('/resources/')
                if self.command=='GET': result=allocation(resource)
                elif self.command=='DELETE':
                    size=int(self.headers.get('Content-Length','0'))
                    if size>4096: raise ValueError('Invalid request size.')
                    body=json.loads(self.rfile.read(size)) if size else {}
                    result=remove(resource,body.get('purge') is True)
                elif self.command=='POST':
                    size=int(self.headers.get('Content-Length','0'))
                    if not 0<size<=4096: raise ValueError('Invalid request size.')
                    body=json.loads(self.rfile.read(size))
                    result=allocation(resource,body['limit_bytes'],body.get('kind','data'))
                else: raise ValueError('Unsupported method.')
            else: raise KeyError('No such resource.')
            self.reply(200,result)
        except KeyError as e: self.reply(404,{'error':str(e)})
        except (ValueError,TypeError) as e: self.reply(409,{'error':str(e)})
        except Exception as e: self.reply(503,{'error':str(e) if isinstance(e,RuntimeError) else 'Storage service unavailable.'})
    def reply(self,status,result):
        data=json.dumps(result).encode()
        self.send_response(status); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET=do_POST=do_DELETE=handle_request

class Server(socketserver.UnixStreamServer):
    allow_reuse_address=True

if __name__=='__main__':
    filesystem()
    SOCKET.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if SOCKET.exists(): SOCKET.unlink()
    with Server(str(SOCKET),Handler) as server:
        SOCKET.chmod(0o600)
        server.serve_forever()
