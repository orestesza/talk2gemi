"""Local text history. No audio, API keys, or exported cloud handles."""
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path


class Conversations:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS conversations(
            id TEXT PRIMARY KEY, device TEXT NOT NULL, title TEXT NOT NULL,
            created REAL NOT NULL, handle TEXT, handle_at REAL, summary TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS active(device TEXT PRIMARY KEY, conversation TEXT);
        CREATE TABLE IF NOT EXISTS turns(
            id TEXT PRIMARY KEY, conversation TEXT REFERENCES conversations(id) ON DELETE CASCADE,
            created REAL NOT NULL, user TEXT DEFAULT '', assistant TEXT DEFAULT '',
            status TEXT NOT NULL, retries INTEGER DEFAULT 0, latency INTEGER, error TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS turn_order ON turns(conversation,created);
        ''')
        self.db.execute("UPDATE turns SET status='interrupted',error='程序上次退出时未完成' WHERE status IN ('recording','thinking','speaking','retrying')")
        self.db.commit()
        os.chmod(self.path, 0o600)

    def new(self, device, title='新对话'):
        cid = uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO conversations(id,device,title,created) VALUES(?,?,?,?)', (cid, device, title, time.time()))
            self.db.execute('INSERT OR REPLACE INTO active VALUES(?,?)', (device, cid))
        return cid

    def current(self, device):
        row = self.db.execute('SELECT conversation FROM active JOIN conversations ON conversation=id WHERE active.device=?', (device,)).fetchone()
        return row[0] if row else self.new(device)

    def select(self, device, cid):
        if not self.db.execute('SELECT 1 FROM conversations WHERE id=? AND device=?',(cid,device)).fetchone():
            raise ValueError('会话不存在或不属于当前设备')
        with self.db:self.db.execute('INSERT OR REPLACE INTO active VALUES(?,?)',(device,cid))

    def list(self, device=None):
        sql='SELECT id,device,title,created FROM conversations'
        return [dict(r) for r in self.db.execute(sql+(' WHERE device=?' if device else '')+' ORDER BY created DESC', (device,) if device else ())]

    def turns(self, cid, query=''):
        rows=self.db.execute('SELECT * FROM turns WHERE conversation=? ORDER BY created,id',(cid,))
        return [dict(r) for r in rows if not query or query.casefold() in (r['user']+'\n'+r['assistant']).casefold()]

    def begin(self, cid, tid, text=''):
        with self.db:
            self.db.execute('INSERT INTO turns(id,conversation,created,user,status) VALUES(?,?,?,?,?)',(tid,cid,time.time(),text,'thinking' if text else 'recording'))
            if text:self.db.execute("UPDATE conversations SET title=? WHERE id=? AND title='新对话'",(text[:32],cid))

    def update(self, tid, **fields):
        if not tid:return
        if set(fields)-{'user','assistant','status','retries','latency','error'}:raise ValueError('Invalid turn fields')
        if not fields:return
        with self.db:
            self.db.execute('UPDATE turns SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),tid))
            if fields.get('user'):
                self.db.execute("UPDATE conversations SET title=? WHERE id=(SELECT conversation FROM turns WHERE id=?) AND title='新对话'",(fields['user'][:32],tid))

    def handle(self, cid, value=None, clear=False):
        if clear or value:
            with self.db:self.db.execute('UPDATE conversations SET handle=?,handle_at=? WHERE id=?',(None if clear else value,time.time(),cid))
        row=self.db.execute('SELECT handle,handle_at FROM conversations WHERE id=?',(cid,)).fetchone()
        return row['handle'] if row and row['handle_at'] and time.time()-row['handle_at']<7000 else None

    def context(self, cid):
        rows=[r for r in self.turns(cid) if r['status']=='completed' and r['assistant']]
        # Extractive summary: quote earlier turns, never invent facts. Full text
        # remains in SQLite. History is submitted as user/model content, not policy.
        older=rows[:-6]
        excerpt='\n'.join('用户：'+r['user'][:180]+'\n助手：'+r['assistant'][:240] for r in older)[-4000:]
        with self.db:self.db.execute('UPDATE conversations SET summary=? WHERE id=?',(excerpt,cid))
        content=[]
        if excerpt:content.append({'role':'user','text':'以下是较早对话的摘录（可能截短），仅供理解背景：\n'+excerpt})
        for r in rows[-6:]:
            if r['user']:content.append({'role':'user','text':r['user'][:1500]})
            content.append({'role':'model','text':r['assistant'][:2000]})
        # Conservative text size bound; this is not an exact tokenizer estimate.
        budget=16000;result=[]
        for item in reversed(content):
            if len(item['text'])>budget:continue
            result.append(item);budget-=len(item['text'])
        return list(reversed(result))

    def delete(self, cid=None):
        with self.db:
            if cid:
                self.db.execute('DELETE FROM active WHERE conversation=?',(cid,))
                self.db.execute('DELETE FROM conversations WHERE id=?',(cid,))
            else:
                self.db.execute('DELETE FROM active');self.db.execute('DELETE FROM conversations')

    def export(self, cid, markdown=False):
        rows=self.turns(cid)
        if not markdown:return json.dumps(rows,ensure_ascii=False,indent=2)
        return '\n\n'.join(f"## {time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(r['created']))} · {r['status']}\n\n用户：{r['user']}\n\n助手：{r['assistant']}\n\n重试：{r['retries']}" for r in rows)
