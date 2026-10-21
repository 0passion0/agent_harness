from deepagents.middleware.filesystem import ReadFileSchema
from pydantic import Field


class NativeReadSchema(ReadFileSchema):
    native_media: bool = Field(
        default=False,
        description="Send PDFs directly to the model instead of extracting Markdown. Images are detected by signature.",
    )
