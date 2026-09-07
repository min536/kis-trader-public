from __future__ import annotations

import unittest

from app.auth.token_cache_identity import (
    APP_KEY_FINGERPRINT_FIELD,
    app_key_cache_fingerprint,
    cache_payload_matches_app_key,
    stamp_app_key_fingerprint,
)


class AppKeyFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_16_hex_chars_and_deterministic(self) -> None:
        first = app_key_cache_fingerprint("PSxxxxxxAPPKEY")
        second = app_key_cache_fingerprint("PSxxxxxxAPPKEY")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 16)
        self.assertTrue(all(character in "0123456789abcdef" for character in first))

    def test_distinct_keys_produce_distinct_fingerprints(self) -> None:
        self.assertNotEqual(
            app_key_cache_fingerprint("old-competition-key"),
            app_key_cache_fingerprint("new-competition-key"),
        )

    def test_fingerprint_does_not_leak_the_raw_key(self) -> None:
        app_key = "SUPER-SECRET-APP-KEY-0123456789"

        self.assertNotIn(app_key, app_key_cache_fingerprint(app_key))

    def test_empty_key_is_handled_deterministically(self) -> None:
        self.assertEqual(len(app_key_cache_fingerprint("")), 16)


class StampFingerprintTests(unittest.TestCase):
    def test_stamp_adds_fingerprint_field_and_preserves_others(self) -> None:
        payload = {"access_token": "tok", "issued_at": 123}

        stamped = stamp_app_key_fingerprint(payload, "key-A")

        self.assertEqual(stamped["access_token"], "tok")
        self.assertEqual(stamped["issued_at"], 123)
        self.assertEqual(
            stamped[APP_KEY_FINGERPRINT_FIELD],
            app_key_cache_fingerprint("key-A"),
        )

    def test_stamp_does_not_mutate_input(self) -> None:
        payload = {"access_token": "tok"}

        stamp_app_key_fingerprint(payload, "key-A")

        self.assertNotIn(APP_KEY_FINGERPRINT_FIELD, payload)


class CachePayloadMatchTests(unittest.TestCase):
    def test_matches_when_stamped_by_same_key(self) -> None:
        stamped = stamp_app_key_fingerprint({"access_token": "t"}, "key-A")

        self.assertTrue(cache_payload_matches_app_key(stamped, "key-A"))

    def test_rejects_when_key_rotated(self) -> None:
        stamped = stamp_app_key_fingerprint({"access_token": "t"}, "old-key")

        self.assertFalse(cache_payload_matches_app_key(stamped, "new-key"))

    def test_rejects_legacy_payload_without_fingerprint(self) -> None:
        legacy = {"access_token": "cached", "issued_at": 9_999_999_999}

        self.assertFalse(cache_payload_matches_app_key(legacy, "any-key"))

    def test_rejects_non_mapping_payload(self) -> None:
        self.assertFalse(cache_payload_matches_app_key(None, "key"))
        self.assertFalse(cache_payload_matches_app_key("not-a-dict", "key"))

    def test_rejects_blank_or_nonstring_fingerprint_field(self) -> None:
        self.assertFalse(
            cache_payload_matches_app_key({APP_KEY_FINGERPRINT_FIELD: ""}, "key")
        )
        self.assertFalse(
            cache_payload_matches_app_key({APP_KEY_FINGERPRINT_FIELD: 123}, "key")
        )


if __name__ == "__main__":
    unittest.main()
