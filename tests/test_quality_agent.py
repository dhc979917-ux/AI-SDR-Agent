import json
import httpx
import pytest
from sdr.models import Extraction, Fact, Evidence, AnalyzeRequest
from sdr.preprocess import split_turns
from sdr.quality_agent import Decision, run_quality_agent, decide


def case():
    turns = split_turns('销售：我们有20名设计师。\n客户：我们有8名设计师。')
    ex = Extraction(call_type='sales', classification_evidence=[Evidence(turn_id='T02', quote=turns[1].text)])
    ex.fields.shejishi = Fact(value=20, evidence=[Evidence(turn_id='T01', quote=turns[0].text)])
    return ex, turns


def inspection():
    return Decision(action='inspect_evidence', field='shejishi', turn_ids=['T02'])


def revision(value=8, tid='T02', quote='我们有8名设计师。'):
    return Decision(action='revise_field', field='shejishi', fact=Fact(value=value, evidence=[Evidence(turn_id=tid, quote=quote)]))


def mock_decisions(monkeypatch, decisions):
    iterator = iter(decisions); calls = []
    def fake(payload, settings, remaining):
        calls.append(payload)
        return next(iterator)
    monkeypatch.setattr('sdr.quality_agent.decide', fake)
    return calls


def test_inspect_correct_revalidate_preserves_initial_and_roles(settings, monkeypatch):
    ex, turns = case(); original = ex.model_dump(); original_turns = [t.model_dump() for t in turns]
    calls = mock_decisions(monkeypatch, [inspection(), revision()])
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert result['fields']['shejishi']['value'] == 8 and not issues
    assert audit['status'] == 'resolved' and audit['calls'] == 2
    assert audit['steps'][1]['before']['value'] == 20 and audit['steps'][1]['after']['value'] == 8
    assert calls[1]['observation']['turns'][0]['speaker'] == 'sales'
    assert ex.model_dump() == original and [t.model_dump() for t in turns] == original_turns


@pytest.mark.parametrize('patch', [revision(20, 'T01', '我们有20名设计师。'), revision(99), revision(8, 'T02', '虚构证据'), Decision(action='revise_field', field='shejishi', fact=Fact())])
def test_bad_repair_cannot_clear_issue(settings, monkeypatch, patch):
    ex, turns = case()
    mock_decisions(monkeypatch, [inspection(), patch, Decision(action='handoff', field='shejishi')])
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert result['fields']['shejishi']['value'] is None and issues
    assert audit['manual_tasks'][0]['field'] == 'shejishi' and '未采用' in audit['steps'][1]['outcome']


def test_cannot_patch_before_inspection_or_touch_other_fields(settings, monkeypatch):
    ex, turns = case()
    mock_decisions(monkeypatch, [revision(), Decision(action='revise_field', field='phone', fact=Fact(value='13812345678'))])
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert result['fields']['phone']['value'] is None and issues and audit['status'] == 'invalid_action'
    assert '先查看' in audit['steps'][0]['outcome']


def test_repeated_action_stops(settings, monkeypatch):
    ex, turns = case(); mock_decisions(monkeypatch, [inspection(), inspection()])
    _, issues, audit = run_quality_agent(ex, turns, settings)
    assert issues and audit['calls'] == 2 and audit['status'] == 'repeated_action'


def test_model_failure_retains_valid_fields_and_pending_issues(settings, monkeypatch):
    ex, turns = case(); ex.fields.industry = Fact(value='家居', evidence=[Evidence(turn_id='T02', quote=turns[1].text)])
    def fail(*args): raise httpx.ReadTimeout('secret upstream information')
    monkeypatch.setattr('sdr.quality_agent.decide', fail)
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert result['fields']['industry']['value'] == '家居' and issues
    assert audit['status'] == 'model_unavailable' and 'secret' not in json.dumps(audit)


def test_context_limit_and_no_issue_do_not_call_model(settings, monkeypatch):
    mock_decisions(monkeypatch, []); ex, turns = case(); turns[0].text += '长文本' * 10000
    _, issues, audit = run_quality_agent(ex, turns, settings)
    assert issues and audit['status'] == 'context_budget' and audit['calls'] == 0
    _, issues, audit = run_quality_agent(Extraction(), turns, settings)
    assert not issues and audit['status'] == 'no_issues'


def test_deadline_discards_late_proposal(settings, monkeypatch):
    ex, turns = case(); clock = [0]
    monkeypatch.setattr('sdr.quality_agent.time.monotonic', lambda: clock[0])
    def late(*args):
        clock[0] = 46
        return inspection()
    monkeypatch.setattr('sdr.quality_agent.decide', late)
    _, issues, audit = run_quality_agent(ex, turns, settings)
    assert issues and audit['status'] == 'time_budget' and not audit['steps']


def test_guard_rechecks_after_network(settings, monkeypatch):
    ex, turns = case(); checked = []; mock_decisions(monkeypatch, [inspection()])
    def guard():
        checked.append(1)
        if len(checked) == 2: raise PermissionError('ownership changed')
    with pytest.raises(PermissionError): run_quality_agent(ex, turns, settings, guard)
    assert len(checked) == 2


def test_request_redaction_and_strict_action_schema(settings, monkeypatch):
    settings.api_key = 'fixture'; captured = []; real = httpx.Client
    def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"action":"finish"}'}}]})
    monkeypatch.setattr('sdr.quality_agent.httpx.Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert decide({'text': '手机号13812345678'}, settings, 10).action == 'finish'
    assert '13812345678' not in captured[0]['messages'][1]['content'] and '[PHONE_' in captured[0]['messages'][1]['content']
    with pytest.raises(ValueError): Decision.model_validate({'action': 'revise_field', 'field': 'phone', 'do_not_contact': False})


def test_engine_persists_audit_and_keeps_original(system, settings, monkeypatch):
    store, engine, _ = system; settings.api_key = 'fixture'; settings.quality_agent_enabled = True
    ex, turns = case()
    monkeypatch.setattr('sdr.engine.llm_extract', lambda *args: (ex, {'provider': 'llm', 'model': 'fixture'}))
    mock_decisions(monkeypatch, [inspection(), revision()])
    request = AnalyzeRequest(transcript='销售：我们有20名设计师。\n客户：我们有8名设计师。', mode='llm', idempotency_key='quality-engine-test').model_dump()
    row, _ = store.create(request, 'test', 'A')
    result = engine.run(row['id'])['result']
    assert result['extraction']['fields']['shejishi']['value'] == 8
    assert result['raw_extraction']['fields']['shejishi']['value'] == 20
    assert store.get(row['id'])['result']['quality_agent']['status'] == 'resolved'
    assert result['review']['status'] != 'approved'


@pytest.mark.parametrize('policy', ['disabled', 'dnc', 'service'])
def test_engine_skips_ineligible_tasks(system, settings, monkeypatch, policy):
    store, engine, _ = system; settings.api_key = 'fixture'; settings.quality_agent_enabled = policy != 'disabled'
    ex, turns = case()
    if policy == 'service': ex.call_type = 'service'
    text = '销售：我们有20名设计师。\n客户：我们有8名设计师。'
    if policy == 'dnc': text += '\n客户：不要再联系我。'
    monkeypatch.setattr('sdr.engine.llm_extract', lambda *args: (ex, {'provider': 'llm', 'model': 'fixture'}))
    mock_decisions(monkeypatch, [])
    req = AnalyzeRequest(transcript=text, mode='llm', idempotency_key='quality-skip-' + policy).model_dump()
    row, _ = store.create(req, 'test', 'A'); result = engine.run(row['id'])['result']
    assert 'quality_agent' not in result


def test_signal_repair_revalidates_and_preserves_policy(settings, monkeypatch):
    from sdr.models import Signal
    turns = split_turns('客户：我们有预算五万。')
    ex = Extraction()
    ex.signals.B = Signal(kind='budget_approved', evidence=[Evidence(turn_id='T01', quote=turns[0].text)])
    mock_decisions(monkeypatch, [Decision(action='inspect_evidence', field='signals.B', turn_ids=['T01']),
                               Decision(action='revise_field', field='signals.B', signal=Signal(kind='budget_range', evidence=ex.signals.B.evidence))])
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert not issues and result['signals']['B']['kind'] == 'budget_range' and audit['status'] == 'resolved'
    assert result['do_not_contact'] is False and ex.signals.B.kind == 'budget_approved'


def test_three_call_budget_keeps_pending_issue(settings, monkeypatch):
    ex, turns = case()
    mock_decisions(monkeypatch, [inspection(), revision(99), Decision(action='inspect_evidence', field='shejishi', turn_ids=['T01'])])
    _, issues, audit = run_quality_agent(ex, turns, settings)
    assert issues and audit['calls'] == 3 and audit['status'] == 'call_budget'


@pytest.mark.parametrize('speaker', ['pending', 'excluded', 'other', 'unknown'])
def test_unconfirmed_roles_cannot_become_facts(settings, monkeypatch, speaker):
    ex, turns = case(); turns[1].speaker = speaker
    mock_decisions(monkeypatch, [inspection(), revision(), Decision(action='finish')])
    result, issues, audit = run_quality_agent(ex, turns, settings)
    assert result['fields']['shejishi']['value'] is None and issues
    assert turns[1].speaker == speaker


def test_cancel_before_model_call(settings, monkeypatch):
    ex, turns = case(); mock_decisions(monkeypatch, [])
    def cancelled(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        run_quality_agent(ex, turns, settings, cancelled)
