from app.services.fixture_loader_service import (
    load_fixture
)


fixture = load_fixture(
    "library_system"
)

architecture = fixture["architecture"]

print("\n=== ENDPOINTS ===")

for endpoint in architecture["api_endpoints"]:
    print(
        endpoint["method"],
        endpoint["path"]
    )