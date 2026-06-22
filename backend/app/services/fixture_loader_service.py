import json
import os


BASE_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        ".."
    )
)

FIXTURE_DIR = os.path.join(
    BASE_DIR,
    "test_fixtures"
)


def load_fixture(name):

    path = os.path.join(
        FIXTURE_DIR,
        f"{name}.json"
    )

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)