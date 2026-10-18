import json
import unittest
from credentials import Credentials

class CredentialTests(unittest.TestCase):
    def test_missing_key_is_reported_without_fabricated_ready(self):
        c=Credentials()
        self.assertFalse(c.public_status()["configured"])
        with self.assertRaises(ValueError):c.get()
    def test_local_ipc_rotation_and_no_public_secret(self):
        c=Credentials("test-environment-key")
        c.from_keychain("test-keychain-key")
        self.assertEqual(c.get(),"test-keychain-key")
        self.assertEqual(c.revision,1)
        c.from_keychain("test-keychain-key")
        self.assertEqual(c.revision,1)
        self.assertNotIn("test-keychain-key",json.dumps(c.public_status()))
        c.from_keychain("test-replacement-key")
        self.assertEqual(c.revision,2)
    def test_rejects_multiline_or_invalid_credentials(self):
        c=Credentials()
        for value in [None,"short","test key with spaces","line\nbreak","x"*257]:
            with self.assertRaises(ValueError):c.from_keychain(value)

if __name__=="__main__":unittest.main()
