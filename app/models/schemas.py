from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class ReplacementStyle(str, Enum):
    LITERAL = "literal"
    CREATIVE = "creative"
    PROFESSIONAL = "professional"


class MetaphorMatch(BaseModel):
    phrase: str = Field(..., description="The detected metaphorical phrase")
    category: str = Field(..., description="The thematic category of the metaphor")
    literal_replacement: str = Field(..., description="Plain-language literal interpretation")
    creative_replacement: Optional[str] = Field(None, description="Creative or figurative alternative")
    professional_replacement: Optional[str] = Field(None, description="Formal/business tone replacement")
    explanation: str = Field(..., description="Explanation of what the metaphor conveys")
    start_pos: int = Field(..., description="Character start position in the original text")
    end_pos: int = Field(..., description="Character end position in the original text")


class MetaphorAnalyzeRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Input text to analyze for metaphors")
    style: ReplacementStyle = Field(
        default=ReplacementStyle.LITERAL,
        description="Target style for suggested transformation"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "Starting this project was like opening a can of worms, but we have to bite the bullet.",
                "style": "literal"
            }
        }
    }


class MetaphorAnalyzeResponse(BaseModel):
    original_text: str
    target_style: ReplacementStyle
    metaphors_found: int
    matches: List[MetaphorMatch]
    transformed_text: str


class MetaphorReplaceRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Input text to transform")
    style: ReplacementStyle = Field(
        default=ReplacementStyle.LITERAL,
        description="Style of replacement to apply"
    )
    custom_overrides: Optional[dict[str, str]] = Field(
        default=None,
        description="Optional custom word/phrase mappings"
    )


class MetaphorReplaceResponse(BaseModel):
    original_text: str
    transformed_text: str
    replacements_applied: int
    style: ReplacementStyle


class CatalogItem(BaseModel):
    phrase: str
    category: str
    literal: str
    creative: str
    professional: str
    meaning: str


class CatalogResponse(BaseModel):
    total_count: int
    items: List[CatalogItem]
