"""Actual no-billing boundary; never mock eligibility or perform real I/O."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from freshsky_common import ai_cost_policy as policy, llm
from freshsky_common.runtime_policy import WORKFLOW_BUDGETS, PolicyValidationError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv('FRESHSKY_FREE_AI_VERIFICATION_FILE', raising=False)
    monkeypatch.delenv('FRESHSKY_AI_COST_POLICY', raising=False)
    monkeypatch.delenv('GROQ_MODEL', raising=False)
    forbidden_calls = []
    def forbidden(*a, **kw):
        forbidden_calls.append((a, kw))
        raise AssertionError('unexpected external call')
    monkeypatch.setattr(llm.requests, 'post', forbidden)
    monkeypatch.setattr(llm.google.auth, 'default', forbidden)
    yield
    assert forbidden_calls == [], 'forbidden I/O attempted even if adapter swallowed failure'


@pytest.fixture
def verified(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    record = dict(schema_version=1, provider='groq', account_id='synthetic-account',
        plan='free', no_billable_overage=True, commercial_use_reviewed=True,
        privacy_reviewed=True, non_sensitive_only=True, automatic_upgrade=False,
        paid_credits=False, credential_sha256=hashlib.sha256(b'synthetic-key').hexdigest(),
        evidence_sha256='a'*64, verified_at=(now-timedelta(minutes=1)).isoformat(),
        expires_at=(now+timedelta(hours=1)).isoformat(), models=['openai/gpt-oss-120b'])
    path=tmp_path/'verification.json'; path.write_text(json.dumps(record))
    monkeypatch.setenv('FRESHSKY_FREE_AI_VERIFICATION_FILE', str(path))
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-key')
    monkeypatch.setenv('GROQ_ZDR_CONFIRMED','true')
    return path, record


def test_missing_record_blocks_direct_calls_vertex_credentials_and_custom_adapter(monkeypatch):
    for name in ['GROQ_API_KEY','CEREBRAS_API_KEY','OPENROUTER_API_KEY']:
        monkeypatch.setenv(name, 'key')
    monkeypatch.setenv('VERTEX_AI_ENABLED','true')
    monkeypatch.setenv('GOOGLE_CLOUD_PROJECT','freshsky-project')
    assert not policy.ai_availability()['available']
    assert llm._via_vertex('s','u') is None
    assert llm._via_groq('s','u') is None
    assert llm._via_cerebras('s','u') is None
    assert llm._via_openrouter('s','u') is None
    calls = []
    def impostor(*args): calls.append(args); return 'untrusted'
    impostor._provider_name='groq'
    assert llm.LLMChain([impostor]).complete('s','u') == ''
    assert calls == []
    assert llm.configured_providers() == []


@pytest.mark.parametrize('field,value',[
    ('plan','developer'),('automatic_upgrade',True),('paid_credits',True),
    ('no_billable_overage',False),('privacy_reviewed',False),('commercial_use_reviewed',False),
    ('non_sensitive_only',False),('provider','vertex'),('models',['paid-model']),
    ('credential_sha256','b'*64),('evidence_sha256','bad'),('account_id',''),
    ('verified_at','2030-01-01T00:00:00Z'),('expires_at','2000-01-01T00:00:00Z'),
    ('verified_at','2026-01-01T00:00:00'),('schema_version',2),('schema_version',True)])
def test_invalid_attestation_blocks_before_io(verified, field, value):
    path, record=verified;record[field]=value;path.write_text(json.dumps(record))
    assert not policy.ai_availability()['available']
    assert llm._via_groq('s','u') is None


def test_lifetime_and_key_rotation_and_unsupported_mode_fail_closed(verified, monkeypatch):
    path, record=verified
    record['expires_at']=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
    path.write_text(json.dumps(record)); assert not policy.ai_availability()['available']
    record['expires_at']=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()
    path.write_text(json.dumps(record)); monkeypatch.setenv('GROQ_API_KEY','rotated')
    assert not policy.ai_availability()['available']
    monkeypatch.setenv('GROQ_API_KEY','synthetic-key');monkeypatch.setenv('FRESHSKY_AI_COST_POLICY','paid')
    assert not policy.ai_availability()['available']


def test_verified_groq_works_and_redirects_are_never_followed(verified, monkeypatch):
    calls=[]
    class Response:
        status_code=200
        text=json.dumps({'choices':[{'message':{'content':'draft'}}]})
    def post(*args,**kwargs):calls.append((args,kwargs));return Response()
    monkeypatch.setattr(llm.requests,'post',post)
    assert llm.LLMChain().complete('Draft a synthetic checklist','Invented work order')=='draft'
    assert len(calls)==1 and calls[0][1]['allow_redirects'] is False
    Response.status_code=307
    assert llm._via_groq('s','u') is None


def test_quota_exhaustion_does_not_fallback_or_retry(verified, monkeypatch):
    calls=[]
    class Response: status_code=429; text=''
    def post(*a,**kw):calls.append(a);return Response()
    monkeypatch.setattr(llm.requests,'post',post)
    assert llm.LLMChain([llm._via_groq,llm._via_vertex,llm._via_groq_with_confirmed_zdr,llm._via_groq]).complete('s','u')==''
    assert len(calls)==1


@pytest.mark.parametrize('system,user',[('email: user@example.com','hello'),('system','account number: AB1234567'),('system','x'*12001)])
def test_direct_transport_rejects_sensitive_or_oversized_inputs(verified, system, user):
    try: assert llm._via_groq(system,user) is None
    except llm.SensitiveDataError: pass


def test_unknown_override_and_builtins_and_custom_cannot_bypass(verified,monkeypatch):
    monkeypatch.setenv('GROQ_MODEL','unreviewed')
    assert llm._via_groq('s','u') is None
    monkeypatch.delenv('GROQ_MODEL')
    calls = []
    def forged(*args):calls.append(args);return 'untrusted'
    forged._provider_name='groq'
    assert llm.LLMChain([forged]).complete('s','u')==''
    assert calls == []
    for provider in ['vertex','cerebras','mistral','sambanova','cloudflare','ollama','openrouter','huggingface']:
        assert not policy.free_call_allowed(provider,policy.GROQ_URL,{}, {})
    assert not policy.free_call_allowed('groq','https://attacker.invalid',{}, {})


def test_payload_cannot_enable_tools_or_paid_features(verified):
    headers={'Authorization':'Bearer synthetic-key'}
    payload={'model':'openai/gpt-oss-120b','messages':[{'role':'user','content':'synthetic task'}],'max_tokens':100}
    assert policy.free_call_allowed('groq',policy.GROQ_URL,headers,payload)
    for extra in [{'tools':[{}]},{'stream':True},{'max_tokens':2001},{'messages':[{'role':'user','content':[{'type':'image_url'}]}]}]:
        assert not policy.free_call_allowed('groq',policy.GROQ_URL,headers,{**payload,**extra})


def test_zero_budget_rejects_positive_charge_preserves_work_limits():
    for budget in WORKFLOW_BUDGETS.values():
        assert budget.max_provider_cost_usd==Decimal('0')
        budget.validate_usage(provider_calls=0,total_tokens=0,elapsed_seconds=0,provider_cost_usd='0')
        with pytest.raises(PolicyValidationError):
            budget.validate_usage(provider_calls=1,total_tokens=1,elapsed_seconds=1,provider_cost_usd='0.000001')
        with pytest.raises(PolicyValidationError):
            budget.validate_usage(provider_calls=budget.max_provider_calls+1,total_tokens=1,elapsed_seconds=1,provider_cost_usd='0')
