from __future__ import annotations

import pytest

from ai.contracts.failures import FailureDescriptor
from ai.providers.base import LocalAIProvider
from ai.providers.contracts import GenerateRequest, GenerateResponse, ProviderHealth, ProviderModel, ProviderModelList
from ai.providers.errors import ProviderFailureCode, ProviderRuntimeError, build_provider_failure
from ai.providers.model_output import ModelOutputError, ModelOutputReason
from ai.providers.retry import (
    DEFAULT_PROVIDER_MAX_ATTEMPTS,
    MAX_PROVIDER_MAX_ATTEMPTS,
    PROVIDER_RETRY_CONTRACT_VERSION,
    ProviderRetryExhausted,
    ProviderRetryPolicy,
    RetryingLocalAIProvider,
    is_transient_provider_failure,
)


def perr(code: ProviderFailureCode, status: int | None = None) -> ProviderRuntimeError:
    details = {} if status is None else {"http_status": status}
    return ProviderRuntimeError(
        code.value,
        build_provider_failure(
            provider="test",
            operation="generate",
            code=code,
            message="Provider operation failed.",
            details=details,
        ),
    )


class ScriptedProvider:
    def __init__(self, generate_script=(), model_script=(), health=None):
        self.generate_script=list(generate_script)
        self.model_script=list(model_script)
        self.generate_calls=0
        self.model_calls=0
        self.health_calls=0
        self._health=health or ProviderHealth(ok=True,provider="test",detail=None)
    def health(self):
        self.health_calls += 1
        return self._health
    def list_models(self):
        self.model_calls += 1
        if not self.model_script: return ProviderModelList(models=())
        item=self.model_script.pop(0)
        if isinstance(item,BaseException): raise item
        return item
    def generate(self,request):
        self.generate_calls += 1
        if not self.generate_script: return GenerateResponse(model=request.model,content="{}",done=True)
        item=self.generate_script.pop(0)
        if isinstance(item,BaseException): raise item
        return item


def req(): return GenerateRequest(model="m1",prompt="test",system_prompt=None,output_schema=None)


def test_versions_and_bounds():
    assert PROVIDER_RETRY_CONTRACT_VERSION == "1.0"
    assert DEFAULT_PROVIDER_MAX_ATTEMPTS == 2
    assert MAX_PROVIDER_MAX_ATTEMPTS == 2

@pytest.mark.parametrize("value",[0,-1,3,100,True,1.5,"2",None])
def test_policy_rejects_invalid(value):
    with pytest.raises(ValueError): ProviderRetryPolicy(max_attempts=value)

@pytest.mark.parametrize("value",[1,2])
def test_policy_accepts_only_small_bounds(value):
    assert ProviderRetryPolicy(max_attempts=value).max_attempts == value

@pytest.mark.parametrize("code",[ProviderFailureCode.TIMEOUT,ProviderFailureCode.UNAVAILABLE])
def test_transient_codes(code):
    assert is_transient_provider_failure(perr(code))

@pytest.mark.parametrize("status",[429,502,503,504])
def test_retryable_http(status):
    assert is_transient_provider_failure(perr(ProviderFailureCode.HTTP_ERROR,status))

@pytest.mark.parametrize("status",[400,401,403,404,409,422,500,505])
def test_nonretry_http(status):
    assert not is_transient_provider_failure(perr(ProviderFailureCode.HTTP_ERROR,status))

@pytest.mark.parametrize("code",[
    ProviderFailureCode.REQUEST_TOO_LARGE,
    ProviderFailureCode.RESPONSE_TOO_LARGE,
    ProviderFailureCode.INVALID_RESPONSE,
    ProviderFailureCode.MODEL_MISMATCH,
    ProviderFailureCode.INCOMPLETE_RESPONSE,
])
def test_permanent_codes(code):
    assert not is_transient_provider_failure(perr(code))


def test_generate_retries_once_then_succeeds():
    ok=GenerateResponse(model="m1",content="{}",done=True)
    inner=ScriptedProvider(generate_script=[perr(ProviderFailureCode.TIMEOUT),ok])
    out=RetryingLocalAIProvider(inner).generate(req())
    assert out is ok and inner.generate_calls == 2


def test_model_list_retries_once_then_succeeds():
    ok=ProviderModelList(models=(ProviderModel(name="m1"),))
    inner=ScriptedProvider(model_script=[perr(ProviderFailureCode.UNAVAILABLE),ok])
    out=RetryingLocalAIProvider(inner).list_models()
    assert out is ok and inner.model_calls == 2


def test_exhaustion_stops_at_two_attempts():
    inner=ScriptedProvider(generate_script=[perr(ProviderFailureCode.TIMEOUT),perr(ProviderFailureCode.TIMEOUT),RuntimeError("unreachable")])
    with pytest.raises(ProviderRetryExhausted) as caught:
        RetryingLocalAIProvider(inner).generate(req())
    assert inner.generate_calls == 2
    assert caught.value.attempts_used == 2
    assert caught.value.max_attempts == 2
    assert caught.value.failure.code == "PROVIDER_TIMEOUT"


def test_max_attempts_one_disables_retry():
    inner=ScriptedProvider(generate_script=[perr(ProviderFailureCode.UNAVAILABLE),RuntimeError("unreachable")])
    with pytest.raises(ProviderRetryExhausted) as caught:
        RetryingLocalAIProvider(inner,policy=ProviderRetryPolicy(max_attempts=1)).generate(req())
    assert inner.generate_calls == 1 and caught.value.attempts_used == 1


def test_permanent_failure_fails_immediately():
    err=perr(ProviderFailureCode.INVALID_RESPONSE)
    inner=ScriptedProvider(generate_script=[err,GenerateResponse(model="m1",content="{}",done=True)])
    with pytest.raises(ProviderRuntimeError) as caught:
        RetryingLocalAIProvider(inner).generate(req())
    assert caught.value is err and inner.generate_calls == 1


def test_http_503_retries_but_500_does_not():
    ok=GenerateResponse(model="m1",content="{}",done=True)
    a=ScriptedProvider(generate_script=[perr(ProviderFailureCode.HTTP_ERROR,503),ok])
    assert RetryingLocalAIProvider(a).generate(req()) is ok and a.generate_calls == 2
    b=ScriptedProvider(generate_script=[perr(ProviderFailureCode.HTTP_ERROR,500),ok])
    with pytest.raises(ProviderRuntimeError): RetryingLocalAIProvider(b).generate(req())
    assert b.generate_calls == 1


def test_health_is_never_retried():
    inner=ScriptedProvider(health=ProviderHealth(ok=False,provider="test",detail="timeout"))
    health=RetryingLocalAIProvider(inner).health()
    assert health.ok is False and inner.health_calls == 1


def test_wrapper_satisfies_protocol():
    assert isinstance(RetryingLocalAIProvider(ScriptedProvider()),LocalAIProvider)


def test_exhaustion_failure_has_no_retry_authority_fields():
    inner=ScriptedProvider(generate_script=[perr(ProviderFailureCode.TIMEOUT),perr(ProviderFailureCode.TIMEOUT)])
    with pytest.raises(ProviderRetryExhausted) as caught:
        RetryingLocalAIProvider(inner).generate(req())
    payload=caught.value.failure.to_dict()
    assert payload["class"] == "PROVIDER"
    for key in ("retry","retry_count","next_state","automatic_action"):
        assert key not in payload


def test_model_output_error_is_not_retried():
    failure=FailureDescriptor.from_dict({
        "class":"MODEL","code":"MODEL_INVALID_OUTPUT",
        "message":"Model output did not satisfy the AgentResult contract.",
        "details":{"contract":"AgentResult","model":"m1","reason":"INVALID_JSON","content_bytes":3},
    })
    model_error=ModelOutputError(ModelOutputReason.INVALID_JSON,failure)
    class P(ScriptedProvider):
        def generate(self,request):
            self.generate_calls += 1
            raise model_error
    inner=P()
    with pytest.raises(ModelOutputError) as caught:
        RetryingLocalAIProvider(inner).generate(req())
    assert caught.value is model_error and inner.generate_calls == 1


def test_source_is_bounded_and_has_no_sleep():
    import ai.providers.retry as module
    text=open(module.__file__,encoding="utf-8").read()
    assert "while attempts < policy.max_attempts" in text
    assert "while True" not in text
    assert "time.sleep" not in text
    assert "asyncio.sleep" not in text


def test_retry_layer_has_no_task_project_ownership():
    provider=RetryingLocalAIProvider(ScriptedProvider())
    for name in ("task_state","project_state","next_state","needs_human","scheduler","approve_artifact"):
        assert not hasattr(provider,name)
