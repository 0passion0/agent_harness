from deepagents.middleware.filesystem import GrepSchema
from pydantic import Field


class SearchSchema(GrepSchema):
    pattern: str = Field(min_length=1, description="Literal text or a ripgrep regular expression.")
    literal: bool = Field(
        default=True, description="Set false for ripgrep regular expression syntax."
    )
    case_sensitive: bool = Field(default=True, description="Set false for case-insensitive search.")
