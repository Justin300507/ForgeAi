from pydantic import BaseModel, Field


class ApiEndpoint(BaseModel):
    method: str
    path: str
    description: str
    file: str = ""


class Column(BaseModel):
    name: str
    type: str
    is_primary_key: bool = False
    is_nullable: bool = True


class DatabaseTable(BaseModel):
    table_name: str
    columns: list[Column] = Field(default_factory=list)


class FolderStructure(BaseModel):
    backend: list[str] = Field(default_factory=list)


class FrontendStructure(BaseModel):
    pages: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)


class ArchitecturePlan(BaseModel):
    api_endpoints: list[ApiEndpoint] = Field(default_factory=list)

    database_schema: list[DatabaseTable] = Field(default_factory=list)

    folder_structure: FolderStructure

    frontend_structure: FrontendStructure