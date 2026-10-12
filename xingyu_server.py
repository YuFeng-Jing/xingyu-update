# -*- coding: utf-8 -*-
"""
星屿账号小服务器
- 每台新设备（按设备ID识别）首次登录自动分配账号：星屿测试1、星屿测试2 …
- 账号持久化到 xingyu_accounts.json，重启不丢
- 浏览器打开 http://127.0.0.1:8770/ 即可查看所有账号
启动：python xingyu_server.py
"""
import json
import os
import random
import re
import threading
import base64
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.parse import quote
from urllib.error import HTTPError

HOST = '0.0.0.0'
PORT = int(os.environ.get('PORT', 8770))
ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(ROOT, 'xingyu_accounts.json')

# GitHub 数据持久化（Render 重启后从 GitHub 恢复数据）
GH_TOKEN = os.environ.get('GITHUB_TOKEN', '')
GH_OWNER = 'YuFeng-Jing'
GH_REPO = 'xingyu-update'
GH_BRANCH = 'main'
GH_DATA_PATH = 'xingyu_accounts.json'
GH_API = 'https://api.github.com/repos/%s/%s/contents' % (GH_OWNER, GH_REPO)

_lock = threading.Lock()


def _gh_headers():
    return {
        'Authorization': 'token ' + GH_TOKEN,
        'Accept': 'application/vnd.github.v3+json',
        'User-Agent': 'xingyu-server',
    }


def _load():
    """先读本地文件；没有则从 GitHub API 加载（用于 Render 重启恢复）。"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get('accounts'), list):
                return data
        except Exception:
            pass
    if GH_TOKEN:
        try:
            q = quote(GH_DATA_PATH, safe='')
            req = Request('%s/%s?ref=%s' % (GH_API, q, GH_BRANCH), headers=_gh_headers())
            with urlopen(req, timeout=15) as r:
                info = json.loads(r.read())
            content = base64.b64decode(info['content']).decode('utf-8')
            data = json.loads(content)
            if isinstance(data, dict) and isinstance(data.get('accounts'), list):
                # 写一份到本地缓存
                try:
                    with open(DATA_FILE, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return data
        except Exception:
            pass
    return {'accounts': []}


def _save(data):
    """本地保存 + 异步推送到 GitHub（Render 持久化）。"""
    try:
        tmp = DATA_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception:
        pass
    if GH_TOKEN:
        threading.Thread(target=_push_github, args=(data,), daemon=True).start()


def _push_github(data):
    """把 xingyu_accounts.json commit 到 GitHub 仓库。"""
    try:
        q = quote(GH_DATA_PATH, safe='')
        url = '%s/%s' % (GH_API, q)
        # 获取当前 sha
        sha = None
        try:
            req = Request('%s/%s?ref=%s' % (GH_API, q, GH_BRANCH), headers=_gh_headers())
            with urlopen(req, timeout=10) as r:
                sha = json.loads(r.read()).get('sha')
        except Exception:
            pass
        content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')).decode('ascii')
        body = json.dumps({'message': 'data update', 'content': content, 'sha': sha, 'branch': GH_BRANCH}).encode('utf-8')
        req = Request(url, data=body, headers=dict(_gh_headers(), **{'Content-Type': 'application/json'}), method='PUT')
        urlopen(req, timeout=15).read()
    except Exception:
        pass


PENDING = ''  # device 为空表示待领取的预留账号，只有管理员能生成


def _gen_password():
    return str(random.randint(100000, 999999))


def _migrate(data):
    """确保每个账号都有 password 和 nickname 字段，并把旧名迁移到 6 位数字格式。"""
    changed = False
    for acc in data['accounts']:
        if 'password' not in acc:
            acc['password'] = _gen_password()
            changed = True
        if 'nickname' not in acc:
            acc['nickname'] = ''
            changed = True
        new_name = '%06d' % acc.get('n', 0)
        if acc.get('name') != new_name:
            acc['name'] = new_name
            changed = True
    return changed


def register(device):
    """已绑定设备返回原账号；未绑定返回 None（需走登录流程）。"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _lock:
        data = _load()
        for acc in data['accounts']:
            if device and acc.get('device') == device:
                acc['lastSeen'] = now
                _save(data)
                return acc
        return None


def create_accounts(count):
    """管理员生成 count 个预留账号（含随机6位密码，待用户登录领取）。返回 [{n,name,password},...]。"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _lock:
        data = _load()
        used = {a.get('n', 0) for a in data['accounts']}
        created = []
        for _ in range(count):
            n = 1
            while n in used:
                n += 1
            used.add(n)
            pwd = _gen_password()
            data['accounts'].append({
                'n': n,
                'name': '%06d' % n,
                'device': PENDING,
                'password': pwd,
                'nickname': '',
                'createdAt': now,
                'lastSeen': '',
            })
            created.append({'n': n, 'name': '%06d' % n, 'password': pwd})
        _save(data)
        return created


def login(name, password, device):
    """验证账号名+密码，通过则绑定 device（支持换设备登录）。返回账号或 None。"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with _lock:
        data = _load()
        for acc in data['accounts']:
            if acc.get('name') == name:
                if acc.get('password') != password:
                    return None
                acc['device'] = device
                acc['lastSeen'] = now
                _save(data)
                return acc
        return None


def update_profile(device, nickname=None, password=None):
    """通过 device 定位账号，更新昵称和/或密码。返回账号或 None。"""
    with _lock:
        data = _load()
        for acc in data['accounts']:
            if device and acc.get('device') == device:
                if nickname is not None:
                    acc['nickname'] = nickname[:24]
                if password is not None and password != '':
                    acc['password'] = password
                _save(data)
                return acc
        return None


def delete_account(n):
    """删除指定 n 的账户，后面的账户自动前移重编号 1..N，name 同步重写。返回是否删除成功。"""
    with _lock:
        data = _load()
        before = len(data['accounts'])
        data['accounts'] = [a for a in data['accounts'] if a.get('n') != n]
        if len(data['accounts']) == before:
            return False
        # 按 n 升序排序后重新连续编号 1..N，name 同步重写
        data['accounts'].sort(key=lambda a: a.get('n', 0))
        for idx, acc in enumerate(data['accounts'], 1):
            acc['n'] = idx
            acc['name'] = '%06d' % idx
        _save(data)
        return True


_ADMIN_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>星屿 · 账号管理</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;
         background:#f5f5f7; min-height:100vh; padding:40px 20px; }
  .wrap { max-width:860px; margin:0 auto; }
  h1 { font-size:26px; color:#1d1d1f; margin-bottom:4px; }
  .sub { font-size:13px; color:#86868b; margin-bottom:24px; }
  .bar { display:flex; gap:10px; align-items:center; margin-bottom:16px; }
  .bar .cnt { font-size:13px; color:#86868b; }
  button { border:0; background:#0a84ff; color:#fff; font-size:13px;
           padding:8px 18px; border-radius:9px; cursor:pointer; }
  button:active { opacity:.8; }
  .del { background:#ff3b30; padding:6px 14px; font-size:12px; }
  .gen { background:#34c759; }
  input.num { width:64px; padding:8px 10px; border:1px solid #d2d2d7; border-radius:9px; font-size:13px; text-align:center; outline:none; }
  .pending { display:inline-block; background:#fff4e0; color:#ff9500; font-size:11px; padding:2px 8px; border-radius:6px; }
  table { width:100%; border-collapse:separate; border-spacing:0;
          background:#fff; border-radius:14px; overflow:hidden;
          box-shadow:0 1px 3px rgba(0,0,0,.06); }
  th,td { text-align:left; padding:12px 16px; font-size:13px; }
  th { background:#fafafa; color:#86868b; font-weight:500; border-bottom:1px solid #ececec; }
  td { color:#1d1d1f; border-bottom:1px solid #f2f2f2; }
  tr:last-child td { border-bottom:0; }
  .tag { display:inline-block; background:#e8f1ff; color:#0a84ff;
         font-size:11px; padding:2px 8px; border-radius:6px; }
  .pwd { font-family:ui-monospace,Consolas,monospace; font-size:13px; color:#1d1d1f; letter-spacing:1px; }
  .dev { font-family:ui-monospace,Consolas,monospace; font-size:12px; color:#86868b; }
  .empty { text-align:center; padding:48px 0; color:#86868b; font-size:14px; background:#fff; border-radius:14px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>星屿 · 账号管理</h1>
  <div class="sub">管理员生成账号时会自动分配随机密码，请把「账号名+密码」发给用户。用户首次登录输入账号名+密码绑定设备，之后自动登录。账号全部删除后无人可登录。数据保存在 xingyu_accounts.json</div>
  <div class="bar">
    <button onclick="load()">刷新</button>
    <span class="cnt" id="cnt"></span>
    <span style="flex:1"></span>
    <input id="gen-num" class="num" type="number" min="1" max="100" value="1">
    <button class="gen" onclick="createAccounts()">生成账号</button>
  </div>
  <div id="box"><div class="empty">加载中…</div></div>
</div>
<script>
function esc(s){ return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function load(){
  try{
    const r = await fetch('/api/accounts');
    const data = await r.json();
    const list = data.accounts || [];
    document.getElementById('cnt').textContent = '共 ' + list.length + ' 个账号';
    if(!list.length){
      document.getElementById('box').innerHTML = '<div class="empty">还没有设备登录过</div>';
      return;
    }
    let rows = list.map(a =>
      '<tr><td><span class="tag">' + esc(a.name) + '</span></td>' +
      '<td>' + (a.nickname ? esc(a.nickname) : '<span style="color:#c7c7cc">未设置</span>') + '</td>' +
      '<td class="pwd">' + esc(a.password || '') + '</td>' +
      '<td class="dev" title="' + esc(a.device) + '">' + (a.device ? esc(String(a.device).slice(0,10)) + '…' : '<span class="pending">待领取</span>') + '</td>' +
      '<td>' + esc(a.createdAt || '') + '</td>' +
      '<td>' + esc(a.lastSeen || '') + '</td>' +
      '<td><button class="del" onclick="delAccount(' + a.n + ')">删除</button></td></tr>').join('');
    document.getElementById('box').innerHTML =
      '<table><tr><th>账号</th><th>昵称</th><th>密码</th><th>设备</th><th>首次登录</th><th>最近活跃</th><th>操作</th></tr>' + rows + '</table>';
  }catch(e){
    document.getElementById('box').innerHTML = '<div class="empty">加载失败：' + esc(e) + '</div>';
  }
}
async function delAccount(n){
  if(!confirm('确认删除 账号 ' + String(n).padStart(6,'0') + ' ？\\n删除后，后面的账号会自动前移重编号（如删3，则原4→3、原5→4…）。')) return;
  try{
    const r = await fetch('/api/delete?n=' + n);
    const d = await r.json();
    if(!d.ok){ alert('删除失败：该账号不存在'); return; }
    load();
  }catch(e){ alert('删除失败：' + e); }
}
async function createAccounts(){
  const v = document.getElementById('gen-num').value;
  const n = parseInt(v);
  if(!n || n < 1 || n > 100){ alert('数量范围 1-100'); return; }
  if(!confirm('生成 ' + n + ' 个预留账号？\\n生成后自动分配随机密码，请把账号名+密码发给对应用户。')) return;
  try{
    const r = await fetch('/api/admin/create?count=' + n);
    const d = await r.json();
    if(!d.ok){ alert('生成失败：' + (d.error || '未知')); return; }
    load();
    const list = d.accounts || [];
    const text = list.map(a => a.name + '    密码：' + a.password).join('\\n');
    setTimeout(() => alert('已生成 ' + list.length + ' 个账号，请复制账号名+密码发给用户：\\n\\n' + text), 50);
  }catch(e){ alert('生成失败：' + e); }
}
load();
setInterval(load, 10000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        raw = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/' or path == '/admin':
            return self._send(200, _ADMIN_PAGE, 'text/html; charset=utf-8')
        if path == '/api/accounts':
            with _lock:
                data = _load()
            return self._send(200, json.dumps(data, ensure_ascii=False))
        if path == '/api/register':
            m = re.search(r'[?&]device=([^&]+)', self.path)
            if not m:
                return self._send(400, json.dumps({'ok': False, 'error': 'missing device'}, ensure_ascii=False))
            from urllib.parse import unquote
            device = unquote(m.group(1))[:64]
            acc = register(device)
            if acc is None:
                return self._send(200, json.dumps({'ok': False, 'needLogin': True}, ensure_ascii=False))
            return self._send(200, json.dumps({'ok': True, 'name': acc['name'], 'nickname': acc.get('nickname', ''), 'n': acc['n']}, ensure_ascii=False))
        if path == '/api/login':
            m_name = re.search(r'[?&]name=([^&]*)', self.path)
            m_pwd = re.search(r'[?&]password=([^&]*)', self.path)
            m_dev = re.search(r'[?&]device=([^&]+)', self.path)
            if not (m_name and m_pwd and m_dev):
                return self._send(400, json.dumps({'ok': False, 'error': 'missing params'}, ensure_ascii=False))
            from urllib.parse import unquote
            name = unquote(m_name.group(1))
            password = unquote(m_pwd.group(1))
            device = unquote(m_dev.group(1))[:64]
            acc = login(name, password, device)
            if acc is None:
                return self._send(200, json.dumps({'ok': False, 'error': '账号或密码错误'}, ensure_ascii=False))
            return self._send(200, json.dumps({'ok': True, 'name': acc['name'], 'nickname': acc.get('nickname', ''), 'n': acc['n']}, ensure_ascii=False))
        if path == '/api/profile':
            m_dev = re.search(r'[?&]device=([^&]+)', self.path)
            if not m_dev:
                return self._send(400, json.dumps({'ok': False, 'error': 'missing device'}, ensure_ascii=False))
            from urllib.parse import unquote
            device = unquote(m_dev.group(1))[:64]
            nickname = None
            password = None
            m_nick = re.search(r'[?&]nickname=([^&]*)', self.path)
            m_pwd = re.search(r'[?&]password=([^&]*)', self.path)
            if m_nick:
                nickname = unquote(m_nick.group(1))
            if m_pwd:
                password = unquote(m_pwd.group(1))
            acc = update_profile(device, nickname, password)
            if acc is None:
                return self._send(200, json.dumps({'ok': False, 'error': '未找到账号'}, ensure_ascii=False))
            return self._send(200, json.dumps({'ok': True, 'name': acc['name'], 'nickname': acc.get('nickname', '')}, ensure_ascii=False))
        if path == '/api/delete':
            m = re.search(r'[?&]n=([^&]+)', self.path)
            if not m:
                return self._send(400, json.dumps({'ok': False, 'error': 'missing n'}, ensure_ascii=False))
            try:
                n = int(m.group(1))
            except ValueError:
                return self._send(400, json.dumps({'ok': False, 'error': 'invalid n'}, ensure_ascii=False))
            ok = delete_account(n)
            return self._send(200, json.dumps({'ok': ok}, ensure_ascii=False))
        if path == '/api/admin/create':
            m = re.search(r'[?&]count=([^&]+)', self.path)
            try:
                count = int(m.group(1)) if m else 1
            except ValueError:
                return self._send(400, json.dumps({'ok': False, 'error': 'invalid count'}, ensure_ascii=False))
            if count < 1 or count > 100:
                return self._send(400, json.dumps({'ok': False, 'error': 'count 范围 1-100'}, ensure_ascii=False))
            created = create_accounts(count)
            return self._send(200, json.dumps({'ok': True, 'accounts': created}, ensure_ascii=False))
        # 静态文件（仅限工作目录，防目录穿越）
        from urllib.parse import unquote
        name = os.path.normpath(unquote(path.lstrip('/')))
        fp = os.path.join(ROOT, name)
        if not fp.startswith(ROOT) or not os.path.isfile(fp):
            return self._send(404, json.dumps({'ok': False, 'error': 'not found'}, ensure_ascii=False))
        ctype = 'text/html; charset=utf-8' if fp.endswith('.html') else \
                'text/css; charset=utf-8' if fp.endswith('.css') else \
                'application/javascript; charset=utf-8' if fp.endswith('.js') else \
                'application/octet-stream'
        with open(fp, 'rb') as f:
            return self._send(200, f.read(), ctype)

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    with _lock:
        data = _load()
        if _migrate(data):
            _save(data)
            print('已为旧账号补齐 password/nickname 字段')
    print('星屿账号服务器已启动: http://%s:%d/  (管理页 / ，登录接口 /api/login)' % (HOST, PORT))
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
