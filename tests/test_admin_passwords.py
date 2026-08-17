import unittest

from api.passwords import hash_password, verify_password


class AdminPasswordTests(unittest.TestCase):
    def test_hash_verifies_only_the_original_password(self):
        password_hash = hash_password("correct horse battery staple")

        self.assertNotIn("correct horse battery staple", password_hash)
        self.assertTrue(verify_password("correct horse battery staple", password_hash))
        self.assertFalse(verify_password("wrong password", password_hash))

    def test_malformed_hash_is_rejected(self):
        self.assertFalse(verify_password("anything", "not-a-valid-password-hash"))
