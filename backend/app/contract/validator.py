"""
ContractConformanceValidator -- checks generated code against an AppContract
derived (via app/contract/adapter.py) from the architect's output.

Per docs/FORGEAI_VNEXT_REPORT.md #1/#2 (S5.2) and the risk mitigation in
S14 ("Adapter phase first... validator warn-only for 1 week of benchmarks
before enforcing"): every finding here is LOW severity and category
CONTRACT -- the same convention app/services/validator_service.py already
uses for non-blocking warnings (see its "warnings" list in
_run_static_validators). LOW severity can never trigger the runtime-skip
gate (which only counts CRITICAL) or the critical-stage deploy gate (which
checks specific stage names, not this one) -- it is purely observational
until enough real runs prove it's worth promoting to a harder gate.

Checks (per report S5.2's list):
  1. Every file in contract.files exists on disk. LIVE today: the adapter
     populates `files` from the architecture's planned file list, so this
     catches a planned file the backend/frontend generator silently never
     wrote.
  2. Every response schema's fields are a subset of its entity's fields
     (the completed/is_complete class of bug -- report failure pattern #9).
  3. Every frontend api_call references a declared endpoint.
  4. Every relationship's target entity exists.

Checks 2-4 are currently INERT in practice: the adapter (by design, see
its own docstring) leaves contract.schemas, contract.frontend.api_calls,
and entity.relationships empty, because the architecture stage doesn't
produce that data yet. They are implemented now, against the day a richer
adapter or a native contract-emitting architect populates those fields,
rather than left for a second pass -- but expect zero findings from them
until then.
"""
from __future__ import annotations

from pathlib import Path

from app.contract.models import AppContract
from app.core.context import Diagnostic, ErrorCategory, ErrorSeverity


def check_contract_conformance(contract: AppContract, project_path: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    diagnostics.extend(_check_files_exist(contract, project_path))
    diagnostics.extend(_check_schema_fields_subset(contract))
    diagnostics.extend(_check_api_calls_reference_endpoints(contract))
    diagnostics.extend(_check_relationship_targets_exist(contract))

    return diagnostics


def _diag(message: str, hint: str, file_path: str | None = None) -> Diagnostic:
    return Diagnostic(
        error_id=Diagnostic.make_id("contract", ErrorCategory.CONTRACT, message, file_path),
        category=ErrorCategory.CONTRACT,
        severity=ErrorSeverity.LOW,
        source="contract",
        message=message,
        file_path=file_path,
        fix_hint=hint,
    )


def _check_files_exist(contract: AppContract, project_path: Path) -> list[Diagnostic]:
    out = []
    for f in contract.files:
        if not (project_path / f.path).exists():
            out.append(_diag(
                f"Contract lists '{f.path}' ({f.kind}) but it was never written to the project.",
                f"Generate the missing {f.kind} file at {f.path}, or remove it from the plan "
                f"if it's no longer needed.",
                file_path=f.path,
            ))
    return out


def _check_schema_fields_subset(contract: AppContract) -> list[Diagnostic]:
    out = []
    for schema in contract.schemas:
        if schema.kind != "response" or not schema.entity:
            continue  # only response schemas are serialized directly from an ORM instance
        entity = contract.entity_by_name(schema.entity)
        if entity is None:
            continue
        entity_fields = entity.field_names()
        missing = [f for f in schema.fields if f not in entity_fields]
        if missing:
            out.append(_diag(
                f"Response schema '{schema.name}' declares field(s) {sorted(missing)} that "
                f"do not exist on its backing entity '{schema.entity}' "
                f"(fields: {sorted(entity_fields)}).",
                f"Add {missing} to the {schema.entity} model, or remove them from '{schema.name}' "
                f"if they were never meant to be serialized.",
            ))
    return out


def _check_api_calls_reference_endpoints(contract: AppContract) -> list[Diagnostic]:
    out = []
    for call in contract.frontend.api_calls:
        if contract.endpoint_by_ref(call.endpoint_ref) is None:
            out.append(_diag(
                f"Frontend page '{call.page}' calls '{call.endpoint_ref}', which is not a "
                f"declared endpoint.",
                f"Add '{call.endpoint_ref}' to the contract's endpoints, or fix the frontend "
                f"call to reference an endpoint that actually exists.",
            ))
    return out


def _check_relationship_targets_exist(contract: AppContract) -> list[Diagnostic]:
    out = []
    for entity in contract.entities:
        for rel in entity.relationships:
            if contract.entity_by_name(rel.target) is None:
                out.append(_diag(
                    f"Entity '{entity.name}' has a {rel.kind} relationship to '{rel.target}', "
                    f"which is not a declared entity.",
                    f"Add '{rel.target}' to the contract's entities, or fix the relationship "
                    f"target on '{entity.name}'.",
                ))
    return out
