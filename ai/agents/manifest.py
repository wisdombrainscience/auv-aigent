from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from ai.agents.context import MinimalAgentContext, MinimalContextBuilder
from ai.agents.runs import AgentRunRecord, AgentRunStore
from ai.contracts.ids import CanonicalId, CanonicalIdError, IdKind
from ai.project.store import ProjectStore, ProjectNotFoundError, ProjectStoreConflictError

CONTEXT_MANIFEST_CONTRACT_VERSION = "1.0"
CONTEXT_MANIFEST_DIRNAME = "context_manifests"
MAX_CONTEXT_MANIFEST_BYTES = 512 * 1024
_CONTEXT_KEYS = frozenset({
    "contract_version","run_id","project_id","task_id","role_id","agent_identity",
    "schema_id","context_contract_version","fact_keys","context_sha256","manifest_sha256",
})

class ContextManifestError(RuntimeError): pass
class ContextManifestConflictError(ContextManifestError): pass
class ContextManifestNotFoundError(ContextManifestError): pass

def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    try:
        raw=(json.dumps(payload,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":"))+"\n").encode()
    except (TypeError,ValueError) as exc:
        raise ContextManifestConflictError("context manifest cannot be canonically encoded") from exc
    if len(raw)>MAX_CONTEXT_MANIFEST_BYTES:
        raise ContextManifestConflictError("context manifest exceeds MAX_CONTEXT_MANIFEST_BYTES")
    return raw

def _sha(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()

def _context_sha(context: MinimalAgentContext) -> str:
    raw=(json.dumps(context.to_dict(),ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(",",":"))+"\n").encode()
    return hashlib.sha256(raw).hexdigest()

def _parse_run_id(raw: CanonicalId|str) -> CanonicalId:
    try: cid=raw if isinstance(raw,CanonicalId) else CanonicalId.parse(raw)
    except (CanonicalIdError,TypeError) as exc:
        raise ContextManifestConflictError("run_id must be canonical AI-RUN") from exc
    if cid.kind is not IdKind.AGENT_RUN:
        raise ContextManifestConflictError("run_id must be canonical AI-RUN")
    return cid

@dataclass(frozen=True)
class ContextManifest:
    run_id: str
    project_id: str
    task_id: str
    role_id: str
    agent_identity: str
    schema_id: str
    context_contract_version: str
    fact_keys: tuple[str,...]
    context_sha256: str
    manifest_sha256: str

    def payload_without_hash(self) -> dict[str,object]:
        return {
            "contract_version": CONTEXT_MANIFEST_CONTRACT_VERSION,
            "run_id": self.run_id, "project_id": self.project_id, "task_id": self.task_id,
            "role_id": self.role_id, "agent_identity": self.agent_identity, "schema_id": self.schema_id,
            "context_contract_version": self.context_contract_version,
            "fact_keys": list(self.fact_keys), "context_sha256": self.context_sha256,
        }
    def to_dict(self)->dict[str,object]:
        return {**self.payload_without_hash(),"manifest_sha256":self.manifest_sha256}

    @classmethod
    def build(cls, run: AgentRunRecord, context: MinimalAgentContext) -> "ContextManifest":
        if run.project_id != context.binding.project_id or run.task_id != context.binding.task_id:
            raise ContextManifestConflictError("run/context identity mismatch")
        if run.role_id != context.binding.role_id or run.agent_identity != context.binding.agent.logical_identity:
            raise ContextManifestConflictError("run/context agent binding mismatch")
        fact_keys=tuple(item.key for item in context.project_facts)
        base={
            "contract_version":CONTEXT_MANIFEST_CONTRACT_VERSION,
            "run_id":run.run_id.value,"project_id":run.project_id.value,"task_id":run.task_id.value,
            "role_id":run.role_id,"agent_identity":run.agent_identity,"schema_id":run.schema_id,
            "context_contract_version":context.to_dict()["contract_version"],
            "fact_keys":list(fact_keys),"context_sha256":_context_sha(context),
        }
        return cls(run.run_id.value,run.project_id.value,run.task_id.value,run.role_id,
                   run.agent_identity,run.schema_id,str(base["context_contract_version"]),
                   fact_keys,str(base["context_sha256"]),_sha(base))

    @classmethod
    def from_dict(cls, raw: Mapping[str,Any]) -> "ContextManifest":
        if not isinstance(raw,Mapping) or frozenset(raw)!=_CONTEXT_KEYS:
            raise ContextManifestConflictError("context manifest must contain exactly G6.7 fields")
        facts=raw["fact_keys"]
        if not isinstance(facts,list) or any(not isinstance(x,str) or not x for x in facts):
            raise ContextManifestConflictError("fact_keys invalid")
        if facts != sorted(facts) or len(facts)!=len(set(facts)):
            raise ContextManifestConflictError("fact_keys must be unique canonical order")
        for key in ("context_sha256","manifest_sha256"):
            if not isinstance(raw[key],str) or len(raw[key])!=64 or any(c not in "0123456789abcdef" for c in raw[key]):
                raise ContextManifestConflictError(f"{key} invalid")
        obj=cls(str(raw["run_id"]),str(raw["project_id"]),str(raw["task_id"]),str(raw["role_id"]),
                str(raw["agent_identity"]),str(raw["schema_id"]),str(raw["context_contract_version"]),
                tuple(facts),str(raw["context_sha256"]),str(raw["manifest_sha256"]))
        if _sha(obj.payload_without_hash()) != obj.manifest_sha256:
            raise ContextManifestConflictError("context manifest hash mismatch")
        return obj

class ContextManifestStore:
    def __init__(self, project_store: ProjectStore):
        if not isinstance(project_store,ProjectStore): raise TypeError("project_store must be ProjectStore")
        self._projects=project_store
        self._runs=AgentRunStore(project_store)
        self._contexts=MinimalContextBuilder(project_store)

    def _dir(self, project_id):
        try: h=self._projects.open_project(project_id)
        except (ProjectNotFoundError,ProjectStoreConflictError) as exc:
            raise ContextManifestConflictError("project could not be opened") from exc
        base=h.layout.data_directories["ai_runs"]
        if base.is_symlink(): raise ContextManifestConflictError("ai_runs directory must not be symlink")
        d=base/CONTEXT_MANIFEST_DIRNAME
        if d.is_symlink(): raise ContextManifestConflictError("context manifest directory must not be symlink")
        d.mkdir(mode=0o700,parents=False,exist_ok=True)
        return d

    def create_manifest(self, run: AgentRunRecord, context: MinimalAgentContext) -> ContextManifest:
        if not isinstance(run,AgentRunRecord): raise TypeError("run must be AgentRunRecord")
        current=self._runs.open_run(run.project_id,run.run_id)
        if current != run: raise ContextManifestConflictError("run record changed before manifest creation")
        manifest=ContextManifest.build(run,context)
        # Rebuild selected context from persisted sources to prevent caller-only context.
        rebuilt=self._contexts.build(run.project_id,run.task_id,fact_keys=manifest.fact_keys)
        if _context_sha(rebuilt)!=manifest.context_sha256:
            raise ContextManifestConflictError("context no longer matches persisted sources")
        path=self._dir(run.project_id)/f"{run.run_id.value}.json"
        if path.is_symlink(): raise ContextManifestConflictError("manifest path must not be symlink")
        raw=_canonical_json(manifest.to_dict())
        if path.exists():
            existing=self.open_manifest(run.project_id,run.run_id)
            if existing==manifest: return existing
            raise ContextManifestConflictError("different manifest already exists for run")
        fd,tmp=tempfile.mkstemp(prefix=".context-manifest-",dir=str(path.parent))
        try:
            with os.fdopen(fd,"wb") as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
            os.chmod(tmp,0o600)
            os.link(tmp,path)
        except FileExistsError as exc:
            raise ContextManifestConflictError("manifest concurrently created") from exc
        finally:
            try: os.unlink(tmp)
            except FileNotFoundError: pass
        return manifest

    def open_manifest(self, project_id, run_id) -> ContextManifest:
        runid=_parse_run_id(run_id)
        run=self._runs.open_run(project_id,runid)
        path=self._dir(project_id)/f"{runid.value}.json"
        if not path.exists(): raise ContextManifestNotFoundError(f"context manifest does not exist: {runid.value}")
        if path.is_symlink() or not path.is_file(): raise ContextManifestConflictError("manifest path unsafe")
        raw=path.read_bytes()
        if len(raw)>MAX_CONTEXT_MANIFEST_BYTES: raise ContextManifestConflictError("manifest exceeds MAX_CONTEXT_MANIFEST_BYTES")
        def reject_const(x): raise ContextManifestConflictError("non-finite JSON constant")
        def no_dups(pairs):
            out={}
            for k,v in pairs:
                if k in out: raise ContextManifestConflictError(f"duplicate JSON key: {k}")
                out[k]=v
            return out
        try: data=json.loads(raw.decode("utf-8"),object_pairs_hook=no_dups,parse_constant=reject_const)
        except (UnicodeDecodeError,json.JSONDecodeError) as exc:
            raise ContextManifestConflictError("manifest is invalid JSON") from exc
        manifest=ContextManifest.from_dict(data)
        if (manifest.run_id!=run.run_id.value or manifest.project_id!=run.project_id.value or
            manifest.task_id!=run.task_id.value or manifest.role_id!=run.role_id or
            manifest.agent_identity!=run.agent_identity or manifest.schema_id!=run.schema_id):
            raise ContextManifestConflictError("manifest/run binding mismatch")
        rebuilt=self._contexts.build(run.project_id,run.task_id,fact_keys=manifest.fact_keys)
        if _context_sha(rebuilt)!=manifest.context_sha256:
            raise ContextManifestConflictError("persisted context inputs changed")
        return manifest
