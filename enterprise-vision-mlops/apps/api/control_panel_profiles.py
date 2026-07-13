from __future__ import annotations

from fastapi import APIRouter, HTTPException

from evm.control_panel.model_components import ModelComponentCatalog, read_model_components
from evm.control_panel.pipeline_profiles import (
    PipelineProfileLaunch,
    PipelineProfileLaunchRequest,
    PipelineProfileList,
    PipelineProfileRecord,
    PipelineProfileReplayValidation,
    PipelineProfileValidation,
    PipelineRunProfile,
    default_profile,
    get_profile,
    launch_profile,
    read_profiles,
    save_profile,
    validate_profile,
    validate_profile_replay,
)


router = APIRouter(prefix="/control-panel/v1", tags=["control-panel-pipeline-profiles"])


@router.get("/model-components", response_model=ModelComponentCatalog)
def list_model_components() -> ModelComponentCatalog:
    return read_model_components()


@router.get("/pipeline-profiles/default", response_model=PipelineRunProfile)
def get_default_pipeline_profile() -> PipelineRunProfile:
    return default_profile()


@router.get("/pipeline-profiles", response_model=PipelineProfileList)
def list_pipeline_profiles() -> PipelineProfileList:
    return read_profiles()


@router.get("/pipeline-profiles/{profile_id}", response_model=PipelineProfileRecord)
def read_pipeline_profile(profile_id: str, version: int | None = None) -> PipelineProfileRecord:
    record = get_profile(profile_id, version)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "pipeline_profile_not_found", "profile_id": profile_id},
        )
    return record


@router.get(
    "/pipeline-profiles/{profile_id}/replay-validation",
    response_model=PipelineProfileReplayValidation,
)
def validate_pipeline_profile_replay(
    profile_id: str,
    version: int | None = None,
) -> PipelineProfileReplayValidation:
    record = get_profile(profile_id, version)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "pipeline_profile_not_found", "profile_id": profile_id},
        )
    return validate_profile_replay(record)


@router.post("/pipeline-profiles/validate", response_model=PipelineProfileValidation)
def validate_pipeline_profile(profile: PipelineRunProfile) -> PipelineProfileValidation:
    return validate_profile(profile)


@router.post("/pipeline-profiles", response_model=PipelineProfileRecord, status_code=201)
def create_pipeline_profile(profile: PipelineRunProfile) -> PipelineProfileRecord:
    try:
        return save_profile(profile)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "pipeline_profile_invalid", "message": str(exc)},
        ) from exc


@router.post(
    "/pipeline-profiles/{profile_id}/launch",
    response_model=PipelineProfileLaunch,
    status_code=202,
)
def launch_pipeline_profile(
    profile_id: str,
    request: PipelineProfileLaunchRequest,
    version: int | None = None,
) -> PipelineProfileLaunch:
    record = get_profile(profile_id, version)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "pipeline_profile_not_found", "profile_id": profile_id},
        )
    result = launch_profile(record, request)
    if result.task is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pipeline_profile_not_executable",
                "profile_id": profile_id,
                "blockers": result.validation.blockers,
            },
        )
    return result
