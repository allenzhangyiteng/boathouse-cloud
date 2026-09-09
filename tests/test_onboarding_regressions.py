"""Exercise connection selection, recovery from setup failures, and truthful onboarding."""
import asyncio
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace

os.environ.setdefault('BH_DOMAIN', 'platform.test')
os.environ.setdefault('BH_PG_PASSWORD', 'test-only')
os.environ.setdefault('BH_STATE_DIR', tempfile.mkdtemp(prefix='bh-onboarding-'))
os.environ.setdefault('BH_METER', '0')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'control'))

import httpx
import pytest
from app import auth, config, db, front, main, mcp, pages


@pytest.fixture
def cli(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader('bh_onboarding_test', str(Path(__file__).resolve().parents[1] / 'cli/bh'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, 'CONFIG', tmp_path / 'config.json')
    monkeypatch.setenv('BH_CONFIG', str(module.CONFIG))
    return module


def test_claim_selects_issuing_workspace_and_keeps_account_key_private(cli, monkeypatch, capsys):
    cli.save_cfg({'logins': [{'api': 'https://api.example.test', 'key': 'old', 'workspace': '*'}], 'default': 'first'})
    monkeypatch.setattr(cli.urllib.request, 'urlopen', lambda *a, **k: io.BytesIO(json.dumps({'api': 'https://api.example.test', 'key': 'bh_secret_new', 'workspace': 'second'}).encode()))
    def api(method, path):
        assert cli.ACTIVE['ws'] == 'second'
        if path == '/api/whoami':
            return {'email': 'owner@example.test', 'workspace': 'second', 'role': 'owner'}
        return {'account_key': True, 'workspaces': [{'slug': 'first', 'role': 'owner'}, {'slug': 'second', 'role': 'admin'}]}
    monkeypatch.setattr(cli, 'call', api)
    cli.cmd_claim(SimpleNamespace(api='https://api.example.test', code='BH-AAAA-BBBB-CCCC'))
    cfg = cli.read_config()
    assert cfg['default'] == 'second' and cfg['logins'][0]['key'] == 'bh_secret_new'
    assert 'bh_secret_new' not in capsys.readouterr().out
    assert cli.CONFIG.stat().st_mode & 0o777 == 0o600


def test_claim_network_failure_is_actionable_not_traceback(cli, monkeypatch, capsys):
    def offline(*a, **k):
        raise cli.urllib.error.URLError('network unreachable')
    monkeypatch.setattr(cli.urllib.request, 'urlopen', offline)
    with pytest.raises(SystemExit):
        cli.cmd_claim(SimpleNamespace(api=None, code='BH-AAAA-BBBB-CCCC'))
    text = capsys.readouterr().err
    assert 'Check the connection and retry' in text and 'Traceback' not in text
    assert not cli.CONFIG.exists()


def test_login_failure_does_not_leave_new_credentials_in_environment(cli, monkeypatch):
    monkeypatch.setenv('BH_API', 'original-api')
    monkeypatch.setenv('BH_KEY', 'original-key')
    def fail(*a):
        raise SystemExit(1)
    monkeypatch.setattr(cli, 'call', fail)
    with pytest.raises(SystemExit):
        cli.cmd_login(SimpleNamespace(api='https://example.test', key='new-key'))
    assert os.environ['BH_API'] == 'original-api' and os.environ['BH_KEY'] == 'original-key'


def test_update_preserves_the_working_interpreter(cli, tmp_path, monkeypatch):
    executable = tmp_path / 'bh'
    executable.write_text('#!old\nprint(1)')
    monkeypatch.setattr(cli.sys, 'argv', [str(executable)])
    monkeypatch.setattr(cli, 'fetch_platform', lambda _: b'#!/usr/bin/env python3\nprint(2)\n')
    cli.cmd_update(SimpleNamespace())
    assert executable.read_text().splitlines()[0] == '#!' + sys.executable


def test_deploy_keeps_environment_secrets_out_of_uploaded_source(cli, tmp_path):
    for name in ('index.html', '.env', '.env.production', '.env.local', '.env.example'):
        (tmp_path / name).write_text(name)
    with tarfile.open(fileobj=io.BytesIO(cli.pack(tmp_path))) as archive:
        names = archive.getnames()
    assert 'index.html' in names and '.env.example' in names
    assert not {'.env', '.env.production', '.env.local'} & set(names)


@pytest.fixture
def state(tmp_path, monkeypatch):
    for attr, val in {'STATE_DIR': tmp_path, 'DB_PATH': tmp_path / 'state.sqlite3', 'SOURCE_DIR': tmp_path / 'source', 'MASTER_KEY_PATH': tmp_path / 'master.key'}.items():
        monkeypatch.setattr(config, attr, val)
    db.init()
    with db.conn() as c:
        for slug in ('first', 'second'):
            c.execute('INSERT INTO workspaces(id,slug,name,created) VALUES(?,?,?,?)', ('ws_'+slug, slug, slug, time.time()))
            for name in ('owner', 'colleague'):
                c.execute('INSERT INTO users(id,workspace_id,email,role,created) VALUES(?,?,?,?,?)', ('u_'+slug+name, 'ws_'+slug, name+'@example.test', 'owner', time.time()))
    yield


def test_other_persons_agent_does_not_mark_customer_connected(state):
    ws = db.workspace('second')
    key = auth.create_project_key(auth.ACCOUNT, 'colleague@example.test', 'colleague')
    auth.key_row(key)
    assert front._welcome_state(ws, 'owner@example.test')['connected'] is False
    key = auth.create_project_key(auth.ACCOUNT, 'owner@example.test', 'own agent')
    auth.key_row(key)
    assert front._welcome_state(ws, 'owner@example.test')['connected'] is True


def test_only_running_deployed_tools_appear_online(state, monkeypatch):
    with db.conn() as c:
        for slug, release in [('unbuilt', None), ('crashed', 'r_failed'), ('working', 'r_good')]:
            c.execute('INSERT INTO tools(id,workspace_id,slug,name,current_release_id,signing_key,db_password,created,resource_key) VALUES(?,?,?,?,?,?,?,?,?)', ('t_'+slug, 'ws_second', slug, slug, release, 's', 'p', time.time(), 'r-'+slug))
    checked=[]
    def status(tool):
        checked.append(tool['slug'])
        return {'state': 'running' if tool['slug']=='working' else 'absent'}
    monkeypatch.setattr(front.deploy, 'status', status)
    shown=front._welcome_state(db.workspace('second'), 'owner@example.test')['tools']
    assert [t['name'] for t in shown] == ['working'] and 'unbuilt' not in checked


def test_mcp_selects_named_workspace_and_refuses_conflicting_selection(state, monkeypatch):
    monkeypatch.setattr(mcp, 'client_factory', lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver'))
    key = auth.create_project_key(auth.ACCOUNT, 'owner@example.test', 'agent')
    async def exercise():
        r = await mcp._call_tool({'name': 'whoami', 'arguments': {'workspace': 'second'}}, 'Bearer '+key)
        assert r['isError'] is False and r['structuredContent']['workspace'] == 'second'
        assert {'first','second'} <= {w['slug'] for w in r['structuredContent']['workspaces']}
        r = await mcp._call_tool({'name': 'get_tool', 'arguments': {'tool': 'second/example', 'workspace': 'first'}}, 'Bearer '+key)
        assert r['isError'] and 'different workspaces' in r['content'][0]['text']
        r = await mcp._call_tool({'name': 'whoami', 'arguments': {'workspace': 'unrelated'}}, 'Bearer '+key)
        assert r['isError']
    asyncio.run(exercise())


def test_primary_connection_includes_context_and_needs_no_technical_copy_steps():
    from html import unescape
    import re
    markup=pages._connect('example.test','bh_secret','BH-AAAA-BBBB-CCCC','second','person@example.test')
    prompt=unescape(re.search(r'<textarea[^>]*>(.*?)</textarea>',markup,re.S)[1])
    assert 'workspace second' in prompt and 'person@example.test' in prompt
    assert 'skill.md' in prompt and '~/.local/bin/bh whoami' in prompt and 'this conversation' in prompt
    assert 'bh_secret' not in prompt and 'Copy connection' in markup
    assert markup.index('<details>') < markup.index('bh_secret')


def test_existing_account_reinvite_is_a_signin_not_a_password_reset(state, monkeypatch):
    from app import mail
    monkeypatch.setattr(mail, 'send', lambda *a, **k: False)
    monkeypatch.setattr(mcp, 'client_factory', lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://testserver'))
    auth.set_account_password('colleague@example.test', 'Synthetic old password 6789!')
    key = auth.create_project_key(auth.ACCOUNT, 'owner@example.test', 'agent')
    r = asyncio.run(mcp._call_tool({'name':'users_invite','arguments':{'email':'colleague@example.test','workspace':'second'}}, 'Bearer '+key))
    assert not r['isError'] and r['structuredContent']['invite_url'] is None
    assert 'Sign in at' in r['content'][0]['text'] and 'sets a new password' not in r['content'][0]['text']
    assert auth.check_login('colleague@example.test', 'Synthetic old password 6789!')


def test_cli_reinvite_handles_nullable_invitation(cli, monkeypatch, capsys):
    monkeypatch.setattr(cli,'call',lambda *a,**k:{'email':'already@example.test','sign_in_url':'https://example.test/login','invite_url':None,'expires_days':None})
    cli.cmd_users(SimpleNamespace(action='invite',email='already@example.test'))
    text=capsys.readouterr().out
    assert 'already has an account' in text and 'None' not in text and 'sets a new password' not in text


def test_login_persists_to_existing_fallback_path(cli, tmp_path, monkeypatch):
    # A pre-existing fallback file must not retain a stale credential after later saves.
    fallback=tmp_path/'fallback/config.json'; fallback.parent.mkdir()
    fallback.write_text(json.dumps({'logins': [],'default':None}))
    monkeypatch.delenv('BH_CONFIG')
    monkeypatch.setattr(cli,'CONFIG',fallback)
    monkeypatch.setattr(cli,'_config_candidates',lambda:[tmp_path/'xdg/config.json',fallback])
    cli.save_cfg({'logins':[{'key':'synthetic'}],'default':'second'})
    cli.save_cfg({'logins':[],'default':None})
    assert json.loads(fallback.read_text())['logins']==[]
    assert not (tmp_path/'xdg/config.json').exists()


def test_logout_accepts_workspace_for_an_account_key_and_removes_credentials(cli, monkeypatch):
    cli.save_cfg({'logins':[{'api':'https://api.example.test','workspace':'*','key':'private'}],'default':'second'})
    cli.cmd_logout(SimpleNamespace(workspace='second'))
    assert cli.read_config()=={'logins':[],'default':None}
    assert 'private' not in cli.CONFIG.read_text()


def test_logout_of_secondary_workspace_disconnects_only_its_account(cli, monkeypatch):
    cli.save_cfg({'logins':[{'api':'https://api.example.test','workspace':'*','key':'private'},{'api':'https://another.example.test','workspace':'other','key':'keep'}],'default':'first'})
    monkeypatch.setattr(cli.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(b'{"workspaces":[{"slug":"first"},{"slug":"second"}]}'))
    cli.cmd_logout(SimpleNamespace(workspace='second'))
    assert [x['key'] for x in cli.read_config()['logins']]==['keep']
    assert cli.read_config()['default']=='other'
