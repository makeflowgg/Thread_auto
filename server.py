"""Local-only dashboard. No third-party dependencies; Python 3.9+."""
import argparse
import ctypes
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
MAX_UPLOAD = 300 * 1024 * 1024
MEDIA = {'.mp4', '.mov', '.webm', '.jpg', '.jpeg', '.png', '.webp'}
DEFAULT_PRESET = {
    'ratio': '4:3', 'font': 'Apple SD Gothic Neo', 'fontSize': 48,
    'bold': True, 'maxChars': 22, 'padding': 18, 'category': 'food',
    'editPrompt': '원본 영상·사진·게시글에 근거해 한국어 제목과 핵심 특징을 작성하세요. 상단에는 짧은 제목, 하단에는 장면에 맞는 설명을 넣으세요. 원본에 없는 가격·효능을 만들지 마세요. 글자는 지정한 여백과 안전 영역 안에 배치하세요.',
    'bodyPrompt': '원본에서 확인한 특징으로 자연스러운 한국어 소개 글을 작성하세요. URL과 도메인은 포함하지 마세요. 제휴 관계 고지 문구를 유지하세요.',
    'commentPrompt': '등록된 상품명과 실제 파트너스 링크만 사용해 구매 안내 댓글을 작성하세요. 링크를 새로 만들지 마세요.',
    'disclosure': '이 게시물에는 제휴 상품 소개가 포함되어 있으며, 구매 시 수수료를 받을 수 있습니다.'
}

def now():
    return datetime.now(timezone.utc).isoformat()

def uid():
    return uuid.uuid4().hex

def required(value, label, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(label + '을(를) 확인하세요.')
    return value.strip()

def web_url(value):
    value = required(value, '주소', 2048)
    p = urlparse(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password:
        raise ValueError('HTTPS 주소를 입력하세요.')
    return value

def validate_body(text):
    if re.search(r'https?\s*:|www\s*\.|[\w가-힣-]+\.(?:[a-z]{2,63}|한국)(?:\b|/)', text, re.I):
        raise ValueError('게시물 본문에는 링크를 포함할 수 없습니다. 상품 링크는 댓글에 입력하세요.')
    if len(text) > 500:
        raise ValueError('게시물 본문은 500자 이내로 입력하세요.')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

class Keychain:
    """Secrets never reach SQLite, command arguments, or browser storage."""
    def __init__(self):
        self.lib = None
        if sys.platform == 'darwin':
            self.lib = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
            self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            v, u, s = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
            self.lib.SecKeychainFindGenericPassword.argtypes = [v,u,s,u,s,ctypes.POINTER(u),ctypes.POINTER(v),ctypes.POINTER(v)]
            self.lib.SecKeychainAddGenericPassword.argtypes = [v,u,s,u,s,u,s,ctypes.POINTER(v)]
            self.lib.SecKeychainItemModifyAttributesAndData.argtypes = [v,v,u,s]
            self.lib.SecKeychainItemFreeContent.argtypes = [v,v]
            self.lib.SecKeychainItemDelete.argtypes = [v]
            self.cf.CFRelease.argtypes = [v]
        self.service = b'ThreadAuto.local.tokens'

    def _find(self, account):
        if not self.lib:
            raise ValueError('현재 보안 저장은 macOS 키체인에서 지원합니다.')
        account = account.encode()
        length, data, item = ctypes.c_uint32(), ctypes.c_void_p(), ctypes.c_void_p()
        status = self.lib.SecKeychainFindGenericPassword(None,len(self.service),self.service,len(account),account,ctypes.byref(length),ctypes.byref(data),ctypes.byref(item))
        return status, length, data, item

    def get(self, account):
        status, length, data, item = self._find(account)
        if status == -25300:
            return None
        if status:
            raise ValueError('키체인 접근이 허용되지 않았습니다.')
        try:
            return ctypes.string_at(data, length.value).decode()
        finally:
            self.lib.SecKeychainItemFreeContent(None, data)
            self.cf.CFRelease(item)

    def put(self, account, secret):
        value = secret.encode()
        status, length, data, item = self._find(account)
        if status == 0:
            self.lib.SecKeychainItemFreeContent(None, data)
            try:
                status = self.lib.SecKeychainItemModifyAttributesAndData(item, None, len(value), value)
            finally:
                self.cf.CFRelease(item)
        elif status == -25300:
            account = account.encode()
            status = self.lib.SecKeychainAddGenericPassword(None,len(self.service),self.service,len(account),account,len(value),value,None)
        if status:
            raise ValueError('키체인 저장에 실패했습니다. macOS 접근 권한을 확인하세요.')

class Store:
    def __init__(self, root, vault=None):
        self.root = Path(root)
        self.data = self.root / '.data'
        self.out = self.root / 'OUT'
        self.data.mkdir(mode=0o700, exist_ok=True)
        self.out.mkdir(exist_ok=True)
        self.path = self.data / 'dashboard.sqlite3'
        self.vault = vault or Keychain()
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT, connected INTEGER DEFAULT 0, verified_at TEXT, has_token INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, platform TEXT, category TEXT, title TEXT, url TEXT, note TEXT, created TEXT);
            CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, group_no INTEGER, name TEXT UNIQUE, source_id TEXT, kind TEXT, bytes INTEGER, created TEXT);
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, account_id TEXT, scheduled TEXT, status TEXT, stage TEXT, detail TEXT, preset TEXT, media_ids TEXT, body TEXT, comment TEXT, created TEXT, published_at TEXT, comment_due_at TEXT, post_id TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, message TEXT, created TEXT);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('preset',json.dumps(DEFAULT_PRESET,ensure_ascii=False)))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('next_number','1'))
        os.chmod(self.path, 0o600)

    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.db() as db:
            result = {table:[dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY rowid DESC')] for table in ('accounts','sources','media','jobs')}
            result['preset'] = json.loads(db.execute("SELECT value FROM settings WHERE id='preset'").fetchone()[0])
            result['events'] = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 30')]
        for job in result['jobs']:
            job['preset'] = json.loads(job['preset'])
            job['media_ids'] = json.loads(job['media_ids'])
        result['capabilities'] = [
            {'name':'로컬 저장 · OUT 관리', 'ready':True, 'detail':'이 컴퓨터에 저장'},
            {'name':'계정 인증', 'ready':any(a['connected'] for a in result['accounts']), 'detail':'계정별 공식 API 확인 필요'},
            {'name':'급상승 콘텐츠 수집', 'ready':False, 'detail':'수집 서비스 연결 필요'},
            {'name':'원본 분석 · AI 원고', 'ready':False, 'detail':'AI 서비스 선택 및 연결 필요'},
            {'name':'영상 렌더링', 'ready':False, 'detail':'영상 편집 엔진 구현 필요' + (' · FFmpeg 감지됨' if shutil.which('ffmpeg') else ' · FFmpeg 미설치')},
            {'name':'게시 · 15분 후 댓글', 'ready':False, 'detail':'게시 연동 및 미디어 전송 구성 필요'}
        ]
        return result

    def save_account(self, data):
        name = required(data.get('name'), '계정 이름', 80)
        account_id = data.get('id') or uid()
        token = data.get('token', '').strip()
        if token and (len(token) > 8192 or any(c.isspace() for c in token)):
            raise ValueError('올바른 액세스 토큰을 입력하세요.')
        with self.lock, self.db() as db:
            old = db.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
            if not old and not token:
                raise ValueError('새 계정의 액세스 토큰을 입력하세요.')
            if token:
                self.vault.put(account_id, token)
            db.execute('''INSERT INTO accounts(id,name,has_token) VALUES (?,?,?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, has_token=MAX(accounts.has_token,excluded.has_token)''', (account_id,name,int(bool(token))))
            if token:
                db.execute('UPDATE accounts SET connected=0, verified_at=NULL, username=NULL WHERE id=?',(account_id,))
        return {'id':account_id}

    def verify(self, account_id):
        with self.db() as db:
            if not db.execute('SELECT id FROM accounts WHERE id=?',(account_id,)).fetchone():
                raise ValueError('계정을 찾을 수 없습니다.')
        token = self.vault.get(account_id)
        if not token:
            raise ValueError('저장된 토큰이 없습니다.')
        try:
            req = Request('https://graph.threads.net/v1.0/me?fields=id,username',headers={'Authorization':'Bearer '+token})
            with build_opener(NoRedirect).open(req, timeout=15) as response:
                info = json.load(response)
            username = required(info.get('username'), 'API 응답 계정', 100)
        except Exception:
            with self.db() as db:
                db.execute('UPDATE accounts SET connected=0 WHERE id=?',(account_id,))
            raise ValueError('공식 API 인증 확인에 실패했습니다. 토큰·권한·네트워크를 확인하세요.') from None
        with self.db() as db:
            db.execute('UPDATE accounts SET connected=1,username=?,verified_at=? WHERE id=?',(username,now(),account_id))
        return {'username':username}

    def save_preset(self, data):
        p = {k:data.get(k, v) for k,v in DEFAULT_PRESET.items()}
        if p['ratio'] not in ('16:9','4:3','1:1','9:16') or p['category'] not in ('food','fashion','all'):
            raise ValueError('비율 또는 카테고리를 확인하세요.')
        for key, low, high in [('fontSize',12,100),('maxChars',5,60),('padding',10,35)]:
            p[key] = int(p[key])
            if not low <= p[key] <= high:
                raise ValueError('프리셋 수치가 허용 범위를 벗어났습니다.')
        p['bold'] = bool(p['bold'])
        for key in ('font','editPrompt','bodyPrompt','commentPrompt','disclosure'):
            p[key] = required(p[key],key,10000)
        validate_body(p['disclosure'])
        with self.db() as db:
            db.execute("UPDATE settings SET value=? WHERE id='preset'",(json.dumps(p,ensure_ascii=False),))
        return p

    def save_source(self, data):
        url = web_url(data.get('url'))
        host = urlparse(url).hostname.lower()
        platform = next((name for name,domains in [('샤오홍슈',('xiaohongshu.com','xhslink.com')),('Threads',('threads.net','threads.com'))] if any(host==d or host.endswith('.'+d) for d in domains)),None)
        if not platform:
            raise ValueError('샤오홍슈 또는 스레드 링크를 입력하세요.')
        category = data.get('category','food')
        if category not in ('food','fashion','all'):
            raise ValueError('카테고리를 확인하세요.')
        item_id = uid()
        with self.db() as db:
            db.execute('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',(item_id,platform,category,required(data.get('title'),'제목',200),url,str(data.get('note',''))[:10000],now()))
        return {'id':item_id}

    def import_media(self, name, content, source_id='', group=None):
        ext = Path(name).suffix.lower()
        if ext not in MEDIA or not content:
            raise ValueError('지원되는 영상 또는 사진 파일을 선택하세요.')
        video = ext in ('.mp4','.mov','.webm')
        # Validate file signatures, not just the extension.
        valid = ((ext in ('.mp4','.mov') and content[4:8] == b'ftyp') or
                 (ext == '.webm' and content[:4] == b'\x1aE\xdf\xa3') or
                 (ext in ('.jpg','.jpeg') and content[:3] == b'\xff\xd8\xff') or
                 (ext == '.png' and content[:8] == b'\x89PNG\r\n\x1a\n') or
                 (ext == '.webp' and content[:4] == b'RIFF' and content[8:12] == b'WEBP'))
        if not valid:
            raise ValueError('파일 내용과 확장자가 일치하지 않습니다.')
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if source_id and not db.execute('SELECT id FROM sources WHERE id=?',(source_id,)).fetchone():
                raise ValueError('원본 링크를 찾을 수 없습니다.')
            if group:
                group = int(group)
                members = db.execute('SELECT * FROM media WHERE group_no=?',(group,)).fetchall()
                if not members:
                    raise ValueError('연결할 작업 번호가 없습니다.')
                if video:
                    raise ValueError('기존 작업 번호에는 사진을 연결할 수 있습니다.')
                if any(m['kind']=='image' for m in members):
                    raise ValueError('현재 작업당 사진 한 장을 지원합니다. 추가 사진의 번호 규칙은 별도 설정이 필요합니다.')
                source_id = members[0]['source_id']
            else:
                group = int(db.execute("SELECT value FROM settings WHERE id='next_number'").fetchone()[0])
                # Existing files are never overwritten, even after a database restore.
                while any(self.out.glob(str(group)+'.*')) or any(self.out.glob(str(group)+'-*')):
                    group += 1
                if group > 99999:
                    raise ValueError('작업 번호 99999에 도달했습니다.')
                db.execute("UPDATE settings SET value=? WHERE id='next_number'",(str(group+1),))
            filename = (str(group) if video else f'{group}-{group}') + ext
            target = self.out / filename
            item_id = uid()
            created = False
            try:
                with target.open('xb') as f:
                    created = True
                    f.write(content)
                db.execute('INSERT INTO media VALUES (?,?,?,?,?,?,?)',(item_id,group,filename,source_id,'video' if video else 'image',len(content),now()))
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise
        return {'id':item_id,'name':filename,'group':group}

    def create_job(self, data):
        account = required(data.get('account_id'),'계정',100)
        scheduled = datetime.fromisoformat(required(data.get('scheduled'),'예약 시간',100))
        if scheduled.tzinfo is None or scheduled <= datetime.now(timezone.utc):
            raise ValueError('미래의 날짜와 시간을 선택하세요.')
        if scheduled.minute or scheduled.second or scheduled.microsecond:
            raise ValueError('예약은 1시간 단위로 설정하세요.')
        body, comment = str(data.get('body','')),str(data.get('comment',''))
        validate_body(body)
        if len(comment)>500:
            raise ValueError('댓글은 500자 이내로 입력하세요.')
        ids = data.get('media_ids',[])
        if not isinstance(ids,list) or any(not isinstance(x,str) for x in ids):
            raise ValueError('파일 선택을 확인하세요.')
        with self.lock, self.db() as db:
            if not db.execute('SELECT id FROM accounts WHERE id=?',(account,)).fetchone():
                raise ValueError('계정을 선택하세요.')
            for file_id in ids:
                if not db.execute('SELECT id FROM media WHERE id=?',(file_id,)).fetchone():
                    raise ValueError('선택한 파일이 존재하지 않습니다.')
            if db.execute("SELECT id FROM jobs WHERE account_id=? AND scheduled=? AND status!='cancelled'",(account,scheduled.isoformat())).fetchone():
                raise ValueError('이 계정에 같은 시간의 예약이 있습니다.')
            preset = db.execute("SELECT value FROM settings WHERE id='preset'").fetchone()[0]
            job_id = uid()
            db.execute('''INSERT INTO jobs(id,account_id,scheduled,status,stage,detail,preset,media_ids,body,comment,created)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(job_id,account,scheduled.isoformat(),'attention','preflight','자동 실행 준비 대기: 수집·AI 분석·영상 렌더링·게시 연동이 필요합니다.',preset,json.dumps(ids),body,comment,now()))
            db.execute('INSERT INTO events(job_id,message,created) VALUES (?,?,?)',(job_id,'작업 저장 · 실행에 필요한 연결을 확인하세요.',now()))
        return {'id':job_id,'status':'attention'}

    def cancel_job(self, job_id):
        with self.lock, self.db() as db:
            row = db.execute('SELECT status FROM jobs WHERE id=?',(job_id,)).fetchone()
            if not row or row['status'] in ('complete','cancelled'):
                raise ValueError('취소할 수 있는 작업이 아닙니다.')
            db.execute("UPDATE jobs SET status='cancelled',detail='사용자가 취소했습니다.' WHERE id=?",(job_id,))
            db.execute('INSERT INTO events(job_id,message,created) VALUES (?,?,?)',(job_id,'작업 취소',now()))
        return {'ok':True}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_data(self, status, content, mime='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(content)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def json(self, status, data):
        self.send_data(status,json.dumps(data,ensure_ascii=False).encode())

    def check_host(self):
        return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')

    def do_GET(self):
        if not self.check_host():
            return self.json(403,{'error':'로컬 접근만 허용합니다.'})
        path = urlparse(self.path).path
        if path == '/api/state':
            return self.json(200, self.server.store.state())
        if path == '/api/session':
            return self.json(200, {'csrf':self.server.csrf})
        assets = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path in assets:
            target = ROOT / 'web' / assets[path]
        elif path.startswith('/media/') and re.fullmatch(r'/media/\d+(?:-\d+)?\.(mp4|mov|webm|jpg|jpeg|png|webp)',path):
            target = self.server.store.out / path.split('/')[-1]
        else:
            return self.json(404,{'error':'페이지를 찾을 수 없습니다.'})
        if not target.is_file():
            return self.json(404,{'error':'파일을 찾을 수 없습니다.'})
        # Stream large media instead of loading it all into memory; support previews seeking.
        size = target.stat().st_size
        start, end = 0, size-1
        range_value = self.headers.get('Range')
        if range_value:
            match = re.fullmatch(r'bytes=(\d+)-(\d*)',range_value)
            if not match:
                return self.json(416,{'error':'지원하지 않는 범위입니다.'})
            start = int(match[1]); end = min(int(match[2]) if match[2] else end,end)
            if start > end:
                return self.json(416,{'error':'파일 범위를 벗어났습니다.'})
        if path in assets:
            return self.send_data(200,target.read_bytes(),mimetypes.guess_type(str(target))[0]+'; charset=utf-8')
        self.send_response(206 if range_value else 200)
        self.send_header('Content-Type',mimetypes.guess_type(str(target))[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(end-start+1))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if range_value:
            self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        with target.open('rb') as f:
            f.seek(start)
            remaining = end-start+1
            while remaining:
                part = f.read(min(65536,remaining))
                if not part: break
                self.wfile.write(part); remaining -= len(part)

    def do_POST(self):
        origin = self.headers.get('Origin')
        allowed_origins = (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')
        if not self.check_host() or (origin and origin not in allowed_origins) or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf):
            return self.json(403,{'error':'로컬 화면에서 다시 시도하세요.'})
        try:
            length = int(self.headers.get('Content-Length','0'))
            path = urlparse(self.path).path
            if length<=0 or length>(MAX_UPLOAD if path=='/api/import' else 100000):
                raise ValueError('요청 크기를 확인하세요. 파일은 최대 300MB입니다.')
            content = self.rfile.read(length)
            if len(content)!=length:
                raise ValueError('전송이 완료되지 않았습니다.')
            if path == '/api/import':
                q = parse_qs(urlparse(self.path).query)
                result = self.server.store.import_media(q.get('name',[''])[0],content,q.get('source',[''])[0],q.get('group',[''])[0])
            else:
                data = json.loads(content)
                if not isinstance(data,dict): raise ValueError('올바르지 않은 요청입니다.')
                routes = {'/api/accounts':self.server.store.save_account,'/api/preset':self.server.store.save_preset,'/api/sources':self.server.store.save_source,'/api/jobs':self.server.store.create_job}
                if path in routes:
                    result = routes[path](data)
                elif path=='/api/verify': result = self.server.store.verify(required(data.get('id'),'계정',100))
                elif path=='/api/cancel': result = self.server.store.cancel_job(required(data.get('id'),'작업',100))
                else: return self.json(404,{'error':'지원하지 않는 작업입니다.'})
            self.json(200,result)
        except (ValueError,TypeError,KeyError,OverflowError):
            error = sys.exc_info()[1]
            self.json(400,{'error':str(error) if isinstance(error,ValueError) else '입력값을 확인하세요.'})
        except Exception:
            self.json(500,{'error':'처리하지 못했습니다. 저장 공간과 로컬 접근 권한을 확인하세요.'})

def make_server(root=ROOT, port=8765, vault=None):
    server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.store = Store(root,vault)
    server.csrf = secrets.token_urlsafe(32)
    return server

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    args = parser.parse_args()
    os.umask(0o077)
    server = make_server(port=args.port)
    print(f'Thread Studio → http://127.0.0.1:{args.port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
