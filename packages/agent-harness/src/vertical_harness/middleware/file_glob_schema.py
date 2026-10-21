from deepagents.middleware.filesystem import GlobSchema
from pydantic import Field


class FileGlobSchema(GlobSchema):
    hidden: bool = Field(
        default=False, description="Include hidden files, excluding .git directories."
    )
    limit: int = Field(default=100, gt=0, description="Maximum number of file paths to return.")
