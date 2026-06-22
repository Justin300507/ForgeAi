from app.services.fixture_loader_service import (
    load_fixture
)


fixture = load_fixture(
    "library_system"
)

print(
    fixture["plan"]["project_name"]
)