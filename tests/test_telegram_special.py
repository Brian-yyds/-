import json
import sqlite3
from deploy import telegram_special as t

CA='0x'+'a'*40


def source():
    db=sqlite3.connect(':memory:')
    db.executescript('''CREATE TABLE special_alerts(id INTEGER PRIMARY KEY,chain TEXT,address TEXT,triggered REAL,data TEXT);
    CREATE TABLE gmgn_market_caps(chain TEXT,address TEXT,market_cap REAL,observed REAL);
    CREATE TABLE gmgn_alerts(id INTEGER PRIMARY KEY,data TEXT);''')
    return db


def add(db,i,name='<fake & coin>'):
    data=dict(token=dict(chain='bsc',address=CA,symbol=name,volume=345678,market_cap=1234567))
    db.execute('INSERT INTO special_alerts VALUES (?,?,?,?,?)',(i,'bsc',CA,1000+i,json.dumps(data)))


def test_baseline_live_update_and_restart_no_replay():
    s=source();q=t.state_db(':memory:');add(s,1)
    t.initialize(q,s,1001);assert t.enqueue(q,s,1002)==0
    add(s,2);s.execute('INSERT INTO gmgn_alerts VALUES (?,?)',(99,'{}'))
    s.execute('INSERT INTO gmgn_market_caps VALUES (?,?,?,?)',('bsc',CA,2222222,1003))
    assert t.enqueue(q,s,1004)==1
    calls=[]
    def send(token,method,payload):calls.append(payload);return dict(message_id=17)
    t.deliver(q,'dummy',123,1005,send)
    text=calls[0]['text']
    assert '&lt;fake &amp; coin&gt;' in text and CA in text and '$2.22M' in text
    assert calls[0]['chat_id']==123 and '/bsc/token/'+CA in text
    t.initialize(q,s,1006);t.enqueue(q,s,1006);t.deliver(q,'dummy',123,1006,send)
    assert len(calls)==1
    assert q.execute('SELECT status,message_id FROM delivery').fetchall()==[('sent',17)]


def test_rate_limit_durable_retry_and_ambiguous_no_repeat():
    s=source();q=t.state_db(':memory:');t.initialize(q,s,1000);add(s,1);t.enqueue(q,s,1002)
    calls=[]
    def limited(*args):calls.append(1);raise t.ApiError(429,120)
    t.deliver(q,'dummy',123,1003,limited);t.deliver(q,'dummy',123,1010,limited)
    assert len(calls)==1
    def timeout(*args):calls.append(1);raise t.ApiError('network',ambiguous=True)
    t.deliver(q,'dummy',123,1124,timeout);t.deliver(q,'dummy',123,2000,timeout)
    assert len(calls)==2
    assert q.execute('SELECT status FROM delivery').fetchone()[0]=='uncertain'


def test_missing_cap_and_malformed_event():
    s=source();q=t.state_db(':memory:');t.initialize(q,s,1000);add(s,1)
    s.execute("UPDATE special_alerts SET address='wrong' WHERE id=1")
    t.enqueue(q,s,1002)
    assert q.execute('SELECT status FROM delivery').fetchone()[0]=='invalid'
    assert t.money(None)=='未知' and t.money(True)=='未知' and t.money(float('nan'))=='未知'
    assert t.money(0)=='$0.00'


def test_only_exact_verified_private_start_can_bind():
    def update(chat=123,text='/start',stamp=1000,kind='private',sender=None):
        return dict(message=dict(text=text,date=stamp,chat=dict(id=chat,type=kind),
                                **{'from':dict(id=chat if sender is None else sender,is_bot=False)}))
    rows=[update(),update(chat=456,stamp=999),update(chat=-99,kind='group'),update(chat=789,sender=456)]
    assert t.binding_chats(rows,1100)==set()
    assert t.binding_chats(rows,1100,1000)=={123}
    assert t.binding_chats(rows,9000,1000)==set()
    command='/start example_binding_code'
    assert t.binding_chats([update(text=command)],1100)==set()
    assert t.binding_chats([update(text=command)],1100,bind_command=command)=={123}
