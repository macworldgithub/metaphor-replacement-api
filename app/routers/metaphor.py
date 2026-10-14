from fastapi import APIRouter, status
from app.models.schemas import (
    MetaphorAnalyzeRequest,
    MetaphorAnalyzeResponse,
    MetaphorReplaceRequest,
    MetaphorReplaceResponse,
    CatalogResponse,
)
from app.services.metaphor_service import metaphor_service

router = APIRouter(prefix="/metaphors", tags=["Metaphors"])


@router.post(
    "/analyze",
    response_model=MetaphorAnalyzeResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze text for metaphors and generate replacements",
)
async def analyze_metaphors(request: MetaphorAnalyzeRequest):
    """Detects metaphorical expressions in input text and provides literal, creative,

    or professional replacements alongside full explanations.
    """
    matches, transformed = metaphor_service.analyze(request.text, request.style)
    return MetaphorAnalyzeResponse(
        original_text=request.text,
        target_style=request.style,
        metaphors_found=len(matches),
        matches=matches,
        transformed_text=transformed,
    )


@router.post(
    "/replace",
    response_model=MetaphorReplaceResponse,
    status_code=status.HTTP_200_OK,
    summary="Perform instant replacement of metaphors in text",
)
async def replace_metaphors(request: MetaphorReplaceRequest):
    """Substitutes metaphors with chosen style variants, supporting custom overrides."""
    transformed, count = metaphor_service.replace(
        text=request.text,
        style=request.style,
        custom_overrides=request.custom_overrides,
    )
    return MetaphorReplaceResponse(
        original_text=request.text,
        transformed_text=transformed,
        replacements_applied=count,
        style=request.style,
    )


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    status_code=status.HTTP_200_OK,
    summary="List all cataloged metaphors and their replacement options",
)
async def get_catalog():
    """Retrieves the built-in dictionary of recognized metaphors and their mappings."""
    items = metaphor_service.get_catalog()
    return CatalogResponse(total_count=len(items), items=items)
