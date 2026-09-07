from __future__ import annotations

import unittest

from app.core.kis_rate_limits import resolve_rest_rate_limit


class ResolveRestRateLimitTests(unittest.TestCase):
    def test_mock_clamps_gateway_to_one_request_per_second(self) -> None:
        limit = resolve_rest_rate_limit(
            environment="mock",
            configured_max_requests_per_second=4,
            configured_min_inter_request_seconds=0.8,
        )

        self.assertEqual(limit.max_requests_per_second, 1)
        self.assertGreaterEqual(limit.min_inter_request_seconds, 1.05)

    def test_live_clamps_gateway_to_eighteen_and_recommended_spacing(self) -> None:
        limit = resolve_rest_rate_limit(
            environment="live",
            configured_max_requests_per_second=20,
            configured_min_inter_request_seconds=0.0,
        )

        self.assertEqual(limit.max_requests_per_second, 18)
        self.assertGreaterEqual(limit.min_inter_request_seconds, 0.1)

    def test_stricter_operator_pacing_is_preserved(self) -> None:
        limit = resolve_rest_rate_limit(
            environment="mock",
            configured_max_requests_per_second=1,
            configured_min_inter_request_seconds=1.1,
        )

        self.assertEqual(limit.max_requests_per_second, 1)
        self.assertEqual(limit.min_inter_request_seconds, 1.1)


if __name__ == "__main__":
    unittest.main()
