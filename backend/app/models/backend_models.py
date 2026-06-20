from pydantic import BaseModel, Field


class GeneratedFile(BaseModel):
    path: str = Field(
        ...,
        min_length=1
    )

    content: str = Field(
        ...,
        min_length=1
    )


class BackendPlan(BaseModel):
    files: list[GeneratedFile] = Field(
        ...,
        min_length=1
    )