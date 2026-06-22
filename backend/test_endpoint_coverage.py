from app.services.fixture_loader_service import (
    load_fixture
)

from app.services.endpoint_coverage_service import (
    calculate_endpoint_coverage
)

project_path = r"C:\Users\jerry\oneDrive\Desktop\forgeAi\generated_projects\librarianpro"

fixture = load_fixture(
    "library_system"
)

result = calculate_endpoint_coverage(
    fixture["architecture"],
    project_path
)

print(result)