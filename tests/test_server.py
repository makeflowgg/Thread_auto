import json
import tempfile
import threading
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from server import Store, make_server, DEFAULT_PRESET, validate_body

class MemoryVault:
    def __init__(self): self.values = {}
    def put(self, account, value): self.values[account] = value
    def get(self, account): return self.values.get(account)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.store = Store(self.temp.name, self.vault)

    def tearDown(self): self.temp.cleanup()

    def account(self):
        return self.store.save_account({'name':'테스트 계정','token':'secret-do-not-expose'})['id']

    def job(self, **overrides):
        data = {'account_id':self.account(),'scheduled':(datetime.now(timezone.utc)+timedelta(days=1)).replace(minute=0,second=0,microsecond=0).isoformat()}
        data.update(overrides)
        return data

    def test_credentials_only_in_vault(self):
        account = self.account()
        self.assertEqual(self.vault.get(account),'secret-do-not-expose')
        self.assertNotIn('secret-do-not-expose',json.dumps(self.store.state()))
        self.assertNotIn(b'secret-do-not-expose',self.store.path.read_bytes())
        self.store.save_account({'id':account,'name':'수정','token':''})
        self.assertEqual(self.vault.get(account),'secret-do-not-expose')

    def test_exact_flat_file_numbering_and_restart(self):
        for number in range(1,4):
            result = self.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom')
            self.assertEqual(result['group'],number)
            self.store.import_media('photo.jpg',b'\xff\xd8\xfftest',group=number)
        self.assertEqual(sorted(p.name for p in self.store.out.iterdir()),['1-1.jpg','1.mp4','2-2.jpg','2.mp4','3-3.jpg','3.mp4'])
        reopened = Store(self.temp.name,self.vault)
        self.assertEqual(reopened.import_media('next.mp4',b'\x00\x00\x00\x18ftypisom')['group'],4)

    def test_signature_and_existing_file_protection(self):
        with self.assertRaises(ValueError): self.store.import_media('bad.mp4',b'<script>bad</script>')
        (self.store.out/'1.mp4').write_bytes(b'original')
        result=self.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom')
        self.assertEqual(result['group'],2)
        self.assertEqual((self.store.out/'1.mp4').read_bytes(),b'original')
        self.assertEqual(len(self.store.state()['media']),1)

    def test_source_link_inherited_by_photo(self):
        source=self.store.save_source({'title':'원본','url':'https://www.threads.net/@example/post/test'})['id']
        video=self.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom',source)
        self.store.import_media('photo.jpg',b'\xff\xd8\xfftest',group=video['group'])
        self.assertTrue(all(m['source_id']==source for m in self.store.state()['media']))

    def test_job_keeps_snapshot_and_never_fakes_success(self):
        data=self.job()
        job=self.store.create_job(data)
        self.assertEqual(job['status'],'attention')
        new=dict(DEFAULT_PRESET,ratio='16:9')
        self.store.save_preset(new)
        saved=self.store.state()['jobs'][0]
        self.assertEqual(saved['preset']['ratio'],'4:3')
        self.assertIsNone(saved['published_at'])
        self.assertIsNone(saved['comment_due_at'])
        with self.assertRaises(ValueError): self.store.create_job(data)
        self.store.cancel_job(job['id'])
        self.assertEqual(self.store.state()['jobs'][0]['status'],'cancelled')

    def test_invalid_schedules_and_links(self):
        for time in ['2000-01-01T09:00:00+09:00','2099-01-01T09:30:00+09:00','2099-01-01T09:00:00']:
            with self.assertRaises(ValueError): self.store.create_job(self.job(scheduled=time))
        for body in ['링크 https://example.com','www.example.com','구매 coupang.com','https : //example']:
            with self.assertRaises(ValueError): validate_body(body)
        validate_body('원본에 근거한 주방용품 소개입니다.')

    def test_number_limit_and_extra_image(self):
        with self.store.db() as db: db.execute("UPDATE settings SET value='99999' WHERE id='next_number'")
        self.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom')
        self.store.import_media('photo.jpg',b'\xff\xd8\xfftest',group=99999)
        with self.assertRaises(ValueError): self.store.import_media('photo.jpg',b'\xff\xd8\xfftest',group=99999)
        with self.assertRaises(ValueError): self.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom')

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.server=make_server(self.temp.name,0,MemoryVault())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()

    def test_http_security_and_persistence(self):
        with urlopen(self.url) as r:
            self.assertIn(b'Thread Studio',r.read())
            self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        payload=json.dumps({'name':'Test','token':'test-token'}).encode()
        for headers in [{},{'X-CSRF-Token':self.server.csrf,'Origin':'https://attacker.example'}]:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url+'/api/accounts',data=payload,headers=headers))
            self.assertEqual(error.exception.code,403)
        with urlopen(Request(self.url+'/api/accounts',data=payload,headers={'X-CSRF-Token':self.server.csrf})) as r:
            self.assertIn('id',json.load(r))
        with urlopen(self.url+'/api/state') as r:
            data=r.read();self.assertNotIn(b'test-token',data)
            self.assertEqual(len(json.loads(data)['accounts']),1)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url+'/api/state',headers={'Host':'attacker.example'}))
        self.assertEqual(error.exception.code,403)

    def test_media_range(self):
        self.server.store.import_media('clip.mp4',b'\x00\x00\x00\x18ftypisom')
        with urlopen(Request(self.url+'/media/1.mp4',headers={'Range':'bytes=4-7'})) as r:
            self.assertEqual(r.status,206);self.assertEqual(r.read(),b'ftyp')
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url+'/.data/dashboard.sqlite3')
        self.assertEqual(error.exception.code,404)

if __name__=='__main__': unittest.main()
