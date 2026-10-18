from types import SimpleNamespace as NS
import json
import pytest
from cardnews_auto import usage_meter as meter, build_progress


def test_text_usage_includes_cached_tokens_and_web_search():
    response=NS(usage={'input_tokens':1000,'output_tokens':100,'input_tokens_details':{'cached_tokens':200}},output=[{'type':'web_search_call'}])
    cost,basis,_=meter.price(response,'openai','gpt-6-sol')
    assert cost==pytest.approx(.01264) and basis=='estimated'


def test_cost_count_does_not_serialize_parsed_message_content():
    class ParsedMessage:
        type = 'message'
        def model_dump(self):
            raise AssertionError('Parsed news content is unrelated to billing')
    response = NS(usage={'input_tokens':1000, 'output_tokens':100},
                  output=[ParsedMessage(), NS(type='web_search_call')])
    assert meter.price(response, 'openai', 'gpt-6-sol')[0] == pytest.approx(.013)


def test_image_cost_uses_modality_tokens():
    response=NS(usage={'input_tokens':300,'output_tokens':500,'input_tokens_details':{'text_tokens':100,'image_tokens':200}})
    assert meter.price(response,'openai','gpt-image-2.5-sunburst')[0]==pytest.approx(.0171)


def test_xai_reported_cost_takes_precedence_over_estimate():
    response=NS(usage={'cost_in_usd_ticks':1230000000},data=[{}])
    assert meter.price(response,'xai','grok-imagine-image-2.0')[:2]==(.123,'reported')
    assert meter.price(NS(data=[{}]),'xai','grok-imagine-image-2.0')[:2]==(.08,'estimated')


def test_missing_usage_and_unknown_models_are_not_zero():
    assert meter.price(NS(),'openai','gpt-6-sol')[0] is None
    assert meter.price(NS(usage={'input_tokens':1,'output_tokens':1}),'openai','unknown')[0] is None


def test_failed_api_call_is_recorded_and_reraised(capsys):
    def fail(**kw):raise RuntimeError('private failure')
    with pytest.raises(RuntimeError):meter.metered_call('원고','openai','gpt-6-sol',fail,model='gpt-6-sol')
    output=capsys.readouterr().out
    event=json.loads(output.split(meter.PREFIX)[1])
    assert event['usd'] is None and event['status']=='failed'
    assert 'private failure' not in output


def test_successful_response_is_returned_and_metered(capsys):
    result=NS(usage={'input_tokens':10,'output_tokens':20},output=[])
    assert meter.metered_call('원고','openai','gpt-6-sol',lambda **kw:result,model='gpt-6-sol') is result
    assert json.loads(capsys.readouterr().out.split(meter.PREFIX)[1])['usd']>0


def test_telegram_reports_stage_time_costs_and_unknown_without_double_count(monkeypatch,tmp_path):
    calls=[]
    class Client:
        chat_id='fake'
        def call(self,method,payload,**kwargs):calls.append(payload['text']);return {'message_id':1}
    monkeypatch.setattr('cardnews_auto.telegram_bot.client_from_env',Client)
    monkeypatch.setattr(build_progress.time,'sleep',lambda _:None)
    now=[0.]
    monkeypatch.setattr(build_progress.time,'monotonic',lambda:now[0])
    p=build_progress.BuildProgress('주제','id',True);p.report_path=tmp_path/'usage.json';p.start()
    p.event({'stage':'원고 작성','status':'running'})
    now[0]=3
    event={'kind':'usage','id':'one','label':'원고 초안','seconds':3,'usd':.01,'basis':'estimated'}
    p.event(event);p.event(event)
    p.event({'stage':'원고 작성','status':'completed'})
    p.event({'kind':'usage','id':'two','label':'1번 편집','seconds':2,'usd':None,'basis':'unknown'})
    p.previous_cost=.02;now[0]=5;p.send('실패','실패 안내',force=True)
    report=json.loads((tmp_path/'usage.json').read_text(encoding='utf-8'))
    assert report['total_known_usd']==pytest.approx(.03) and len(report['events'])==2
    assert report['stage_seconds']['원고 작성']==3 and report['runtime_seconds']==5
    assert '원고 초안' in calls[-1] and '$0.0300' in calls[-1] and '확인 불가' in calls[-1]
    assert len(calls[-1])<=4000
