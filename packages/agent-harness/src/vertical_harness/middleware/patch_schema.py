from pydantic import BaseModel, Field


class PatchSchema(BaseModel):
    patch_text: str = Field(description="Complete *** Begin Patch / *** End Patch text.")
