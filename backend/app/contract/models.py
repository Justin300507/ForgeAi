"""
AppContract — the typed IR proposed in docs/FORGEAI_VNEXT_REPORT.md (S5.2).

This is the single source of truth for names and shapes across a generation
run: the architect stage will (eventually) emit this instead of prose, both
generators will consume it instead of free-form architecture JSON, and a
static ContractConformanceValidator will check generated code against it
before anything boots. That is a separate, later PR (see
docs/FORGEAI_VNEXT_REPORT.md #1/#2) -- this file only defines the shape.

Migration path (per the report): a ContractAdapter derives an AppContract
from the CURRENT architect output (ArchitecturePlan in
app/models/architecture_models.py) as a best-effort projection, so every
field here that the current architect doesn't produce yet has a safe
default and is allowed to come back empty rather than failing adapter
construction. Tightening the architect prompt to emit richer contracts
natively happens once the adapter and conformance validator are proven out.

Nothing in the live pipeline imports this module yet.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ContractField(BaseModel):
    name:     str
    type:     str                    # e.g. "str", "int", "datetime", "bool"
    nullable: bool = False
    default:  Optional[str] = None   # stringified default expression, if any


class ContractRelationship(BaseModel):
    kind:           str              # "one_to_many" | "many_to_one" | "many_to_many" | "one_to_one"
    target:         str              # target entity name
    back_populates: Optional[str] = None
    cascade:        Optional[str] = None


class ContractEntity(BaseModel):
    name:          str
    table_name:    str
    fields:        list[ContractField]        = Field(default_factory=list)
    relationships: list[ContractRelationship]  = Field(default_factory=list)

    def field_names(self) -> set[str]:
        return {f.name for f in self.fields}


class ContractEndpoint(BaseModel):
    method:          str
    path:            str
    name:            str                     # operation name, e.g. "list_tasks"
    entity:          Optional[str] = None
    request_schema:  Optional[str] = None
    response_schema: Optional[str] = None
    auth_required:   bool = False
    status_codes:    list[int] = Field(default_factory=lambda: [200])

    @property
    def ref(self) -> str:
        """Stable identifier for cross-referencing from frontend.api_calls."""
        return f"{self.method.upper()} {self.path}"


class ContractSchema(BaseModel):
    name:     str
    entity:   Optional[str] = None
    # Field names this schema exposes. For a response schema, every field
    # here MUST exist on the backing entity -- that is exactly the
    # PydanticSerializationError/ResponseValidationError/ModelFieldMismatch
    # class of failure (report S3, ranked #9). Request/input schemas
    # legitimately have fields with no matching column (e.g. a plaintext
    # `password` on a UserCreate) and are not held to that constraint.
    fields:   list[str] = Field(default_factory=list)
    orm_mode: bool = True
    kind:     str = "response"   # "response" | "request"


class ContractRoute(BaseModel):
    path:   str
    page:   str
    guards: list[str] = Field(default_factory=list)   # e.g. ["auth_required"]


class ContractApiCall(BaseModel):
    endpoint_ref: str   # must match a declared ContractEndpoint.ref exactly
    page:         str


class ContractFrontend(BaseModel):
    routes:            list[ContractRoute]   = Field(default_factory=list)
    api_calls:         list[ContractApiCall] = Field(default_factory=list)
    components:        list[str]             = Field(default_factory=list)
    design_tokens_ref: Optional[str] = None


class ContractFile(BaseModel):
    path:    str
    kind:    str                              # "model"|"schema"|"route"|"service"|"component"|"page"|"util"|"config"
    exports: list[str] = Field(default_factory=list)


class ContractDeps(BaseModel):
    python: list[str] = Field(default_factory=list)
    npm:    list[str] = Field(default_factory=list)


class ContractApp(BaseModel):
    name:        str
    category:    str = ""
    description: str = ""


class AppContract(BaseModel):
    app:       ContractApp
    entities:  list[ContractEntity]   = Field(default_factory=list)
    endpoints: list[ContractEndpoint] = Field(default_factory=list)
    schemas:   list[ContractSchema]   = Field(default_factory=list)
    frontend:  ContractFrontend       = Field(default_factory=ContractFrontend)
    files:     list[ContractFile]     = Field(default_factory=list)
    deps:      ContractDeps           = Field(default_factory=ContractDeps)

    # ── Lookups (used by the future adapter/validator/generators) ──────────

    def entity_by_name(self, name: str) -> Optional[ContractEntity]:
        return next((e for e in self.entities if e.name == name), None)

    def endpoint_by_ref(self, ref: str) -> Optional[ContractEndpoint]:
        return next((e for e in self.endpoints if e.ref == ref), None)

    def schema_by_name(self, name: str) -> Optional[ContractSchema]:
        return next((s for s in self.schemas if s.name == name), None)

    def file_by_path(self, path: str) -> Optional[ContractFile]:
        return next((f for f in self.files if f.path == path), None)
