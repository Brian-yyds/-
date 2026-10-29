import json
import store
import sound_ui
import gmgn_monitor as gmgn


def test_only_displayed_gmgn_alarm_ids_are_sent_and_updates_do_not_create_sound_events(tmp_path,monkeypatch):
    monkeypatch.setattr(store,'USER',tmp_path)
    monkeypatch.setattr(store,'DB',tmp_path/'sound.sqlite')
    with store.connect() as con:
        gmgn.schema(con)
        con.execute("INSERT INTO spike_alerts(id,time,symbol) VALUES (999,123,'BTCUSDT')")
        for i in range(1,5):
            token=dict(chain='bsc',address='0x'+f'{i:040x}',symbol='TOKEN')
            con.execute('INSERT INTO gmgn_alerts(id,chain,address,rule,triggered,last_seen,data) VALUES (?,?,?,?,?,?,?)',
                        (i,'bsc',token['address'],'非发射台放量',100+i,100+i,json.dumps(token)))
    packet=sound_ui.snapshot()
    assert packet['version']==2 and packet['latest']==dict(gmgn=4)
    assert packet['events']==[dict(source='gmgn',id=i,time=100+i) for i in (4,3,2)]
    with store.connect() as con:con.execute('UPDATE gmgn_alerts SET last_seen=999')
    assert sound_ui.snapshot()==packet
