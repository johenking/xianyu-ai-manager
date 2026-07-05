import json
from pathlib import Path
import unittest

from app_factory import create_app


SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "openapi_methods.json"


def openapi_method_contract() -> dict[str, list[str]]:
    schema = create_app().openapi()
    return {
        path: sorted(
            method.upper()
            for method in definition
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}
        )
        for path, definition in sorted(schema.get("paths", {}).items())
    }


class OpenAPIContractTests(unittest.TestCase):
    def test_path_and_method_contract_matches_snapshot(self):
        expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(openapi_method_contract(), expected)


if __name__ == "__main__":
    unittest.main()
