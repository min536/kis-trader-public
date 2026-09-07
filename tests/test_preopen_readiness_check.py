import unittest
from pathlib import Path

from app.tools.preopen_readiness_check import _file_readiness


class PreopenReadinessCheckTests(unittest.TestCase):
    def test_missing_file_is_fail(self) -> None:
        payload = _file_readiness(Path("/tmp/definitely_missing_readiness_file.json"), stale_minutes=30)

        self.assertEqual(payload["status"], "FAIL")
        self.assertFalse(payload["exists"])


if __name__ == "__main__":
    unittest.main()
