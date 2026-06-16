from pydantic import BaseModel
from typing import List


class ApiEndpoint(BaseModel):
    method: str
    path: str
    description: str


class Column(BaseModel):
    name: str
    type: str
    is_primary_key: bool = False
    is_nullable: bool = True


class DatabaseTable(BaseModel):
    table_name: str
    columns: List[Column]

class FolderStructure(BaseModel):
    backend: List[str]


class ArchitecturePlan(BaseModel):
    api_endpoints: List[ApiEndpoint]
    database_schema: List[DatabaseTable]
    folder_structure: FolderStructure