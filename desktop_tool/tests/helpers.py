import hashlib
import json
from pathlib import Path
import sqlite3


def project(root, schema=2):
    root = Path(root).resolve()
    (root / '.scs').mkdir(parents=True)
    con = sqlite3.connect(root / '.scs/state.sqlite3')
    con.executescript('''CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE sessions(id TEXT PRIMARY KEY,client TEXT,mode TEXT,verification TEXT,codex_review INTEGER,review_registration TEXT,created_at TEXT);
    CREATE TABLE work_packages(id TEXT PRIMARY KEY,session_id TEXT,revision INTEGER,version INTEGER,state TEXT,data TEXT,docs_root TEXT,created_at TEXT);
    CREATE TABLE work_attempts(id TEXT PRIMARY KEY,package_id TEXT,revision INTEGER,subtask_id TEXT,attempt_no INTEGER,state TEXT,host_agent_id TEXT,controller_session TEXT,input_snapshot TEXT,resources TEXT,result_state TEXT,result_data TEXT,created_at TEXT,updated_at TEXT,last_observed_at TEXT);
    CREATE TABLE events(seq INTEGER PRIMARY KEY,at TEXT,tool TEXT,event TEXT,error_code TEXT);''')
    con.execute('INSERT INTO metadata VALUES(?,?)', ('project_root', str(root)))
    con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)', ('session-one','fixture-host','long','["full"]',0,'registered','2026-11-10T00:00:00Z'))
    con.execute('INSERT INTO work_packages VALUES(?,?,?,?,?,?,?,?)', ('WP-demo','session-one',1,1,'REVIEW_READY',json.dumps({'goal':'测试示例（合成数据）','subtasks':[{'id':'T1','status':'PASS'}]}),'docs','2026-11-10T00:00:00Z'))
    con.execute('INSERT INTO events VALUES(?,?,?,?,?)',(1,'2026-11-10T00:00:00Z','work_package_update','UPDATED',None))
    con.execute(f'PRAGMA user_version={schema}')
    con.commit()
    con.close()
    view = root / 'docs/agent/acceptance'
    view.mkdir(parents=True)
    (root / 'docs/.obsidian').mkdir()
    (root / 'docs/.obsidian/app.json').write_text('{"user":true}')
    note = view / '00_项目总览.md'
    note.write_text('<!-- scs-project-view:1 -->\n# 示例\n<!-- manual:begin -->\n我的批注\n<!-- manual:end -->\n')
    m = {'schema':1, 'docs_root':'docs','project_hash':hashlib.sha256(str(root).encode()).hexdigest(),
         'files':{'docs/agent/acceptance/00_项目总览.md':['a'*64]},'snapshot':'fixture','auto_refresh':True,'pending':False}
    (view / '.scs-view.json').write_text(json.dumps(m))
    (root / 'docs/agent/evidence').mkdir()
    (root / 'docs/agent/evidence/keep.txt').write_text('evidence')
    return root
