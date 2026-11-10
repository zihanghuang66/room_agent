import sys,threading,time,json
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ui'))
from test_conversation import Service,reply
from conversation import Conversation
from room_agent.storage import room_lock


def test_current_scene_is_not_replaced_by_interrupted_edit_metadata(tmp_path):
    s=Service(tmp_path)
    current=s.show('one');current['issues']=[{'detail':'Inherited chair mismatch'}]
    c=Conversation(s,status_reader=lambda _:dict(run_id='one',execution_run_id='child',execution_status='interrupted',current_node='baseline_render',quality_status='unreviewed',issues=[],current_result_status=current),context_reader=lambda _:dict(current_scene_run_id='one'))
    facts=c.facts('one',s._load('one')[0])
    assert facts['current_scene']['edit_status']=='passed'
    assert 'chair' in facts['findings']
    assert facts['active_operation']['execution_status']=='interrupted'
    assert facts['quality_status']=='not_passed'


def test_short_lock_contention_waits_without_replaying_work(tmp_path):
    from conversation_lock import conversation_lock
    ready=threading.Event()
    def hold():
        with room_lock(tmp_path,'conversation-one'):
            ready.set();time.sleep(.1)
    t=threading.Thread(target=hold);t.start();assert ready.wait(1)
    count=0
    with conversation_lock(tmp_path,'conversation-one',timeout=1):count+=1
    t.join();assert count==1


def test_real_busy_is_clear_and_body_exceptions_are_never_retried(tmp_path):
    from conversation_lock import conversation_lock
    with room_lock(tmp_path,'conversation-one'):
        with pytest.raises(RuntimeError,match='still processing'):
            with conversation_lock(tmp_path,'conversation-one',timeout=.01):pytest.fail('lock bypassed')
    count=0
    with pytest.raises(RuntimeError,match='room is busy: body'):
        with conversation_lock(tmp_path,'conversation-one'):
            count+=1;raise RuntimeError('room is busy: body')
    assert count==1


def test_waiting_duplicate_send_rechecks_idempotency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    calls=[];entered=threading.Event()
    def planner(prompt):calls.append(prompt);entered.set();time.sleep(.15);return reply()
    c=Conversation(Service(tmp_path),planner=planner)
    data={'request_id':'same','revision':0,'message':'hello'}
    with ThreadPoolExecutor(2) as pool:
        with room_lock(tmp_path,'conversation-planner'):
            a=pool.submit(c.send,'one',data)
            b=pool.submit(c.send,'one',data)
            time.sleep(.1)
        assert a.result()==b.result()
    assert len(calls)==1
