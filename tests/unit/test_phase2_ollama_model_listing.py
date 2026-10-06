from __future__ import annotations
from contextlib import contextmanager
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import pytest
from ai.providers.contracts import GenerateRequest, ProviderModelList
from ai.providers.ollama import MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES, OLLAMA_PROVIDER_CONTRACT_VERSION, OllamaModelListError, OllamaProvider

class HandlerBase(BaseHTTPRequestHandler):
    status=200; payload=b'{"models":[]}'; hits=0; paths=[]; redirect_location=None
    def do_GET(self):
        type(self).hits += 1; type(self).paths.append(self.path)
        if self.path != '/api/tags': self.send_response(404); self.end_headers(); return
        if type(self).redirect_location is not None:
            self.send_response(302); self.send_header('Location', type(self).redirect_location); self.end_headers(); return
        self.send_response(type(self).status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(type(self).payload))); self.end_headers(); self.wfile.write(type(self).payload)
    def log_message(self, format, *args): return

@contextmanager
def tags_server(status=200,payload=b'{"models":[]}',redirect_location=None):
    class Handler(HandlerBase): pass
    Handler.status=status; Handler.payload=payload; Handler.hits=0; Handler.paths=[]; Handler.redirect_location=redirect_location
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try: yield f'http://127.0.0.1:{server.server_address[1]}', Handler
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)

def test_version_and_limit():
    assert OLLAMA_PROVIDER_CONTRACT_VERSION=='1.2'; assert MAX_OLLAMA_MODEL_LIST_RESPONSE_BYTES==1024*1024

def test_empty_list_valid():
    with tags_server() as (endpoint,h): result=OllamaProvider(endpoint=endpoint).list_models()
    assert isinstance(result,ProviderModelList); assert result.models==(); assert h.hits==1; assert h.paths==['/api/tags']

def test_maps_names_only():
    payload=json.dumps({'models':[{'name':'qwen3:8b','size':123,'digest':'abc','details':{'family':'qwen3'}},{'name':'coder:7b'}]}).encode()
    with tags_server(payload=payload) as (endpoint,h): result=OllamaProvider(endpoint=endpoint).list_models()
    assert result.to_dict()=={'models':[{'name':'qwen3:8b'},{'name':'coder:7b'}]}; assert h.hits==1

def test_model_key_fallback():
    with tags_server(payload=b'{"models":[{"model":"fallback:1b"}]}') as (endpoint,_): result=OllamaProvider(endpoint=endpoint).list_models()
    assert [m.name for m in result.models]==['fallback:1b']

@pytest.mark.parametrize('payload',[b'',b'not-json',b'[]',b'{}',b'{"models":{}}',b'{"models":[1]}',b'{"models":[{}]}',b'{"models":[{"name":1}]}',b'{"models":[{"name":""}]}',b'{"models":[{"name":" bad"}]}'])
def test_malformed_rejected(payload):
    with tags_server(payload=payload) as (endpoint,h):
        with pytest.raises(OllamaModelListError): OllamaProvider(endpoint=endpoint).list_models()
    assert h.hits==1

def test_duplicate_rejected():
    with tags_server(payload=b'{"models":[{"name":"m1"},{"name":"m1"}]}') as (endpoint,h):
        with pytest.raises(OllamaModelListError,match='invalid_model_list'): OllamaProvider(endpoint=endpoint).list_models()
    assert h.hits==1

def test_http_failure_not_empty():
    with tags_server(status=503) as (endpoint,h):
        with pytest.raises(OllamaModelListError,match='http_status=503'): OllamaProvider(endpoint=endpoint).list_models()
    assert h.hits==1

def test_redirect_not_followed():
    with tags_server(redirect_location='http://192.0.2.1:11434/api/tags') as (endpoint,h):
        with pytest.raises(OllamaModelListError,match='http_status=302'): OllamaProvider(endpoint=endpoint).list_models()
    assert h.hits==1

def test_unavailable_explicit():
    server=ThreadingHTTPServer(('127.0.0.1',0),HandlerBase); port=server.server_address[1]; server.server_close()
    with pytest.raises(OllamaModelListError,match='unavailable'): OllamaProvider(endpoint=f'http://127.0.0.1:{port}',health_timeout_s=0.1).list_models()

def test_generate_gate_released_by_p2_6():
    assert callable(OllamaProvider.generate)

