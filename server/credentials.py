"""Secrets stay in memory. The native app owns macOS Keychain access.

Development may supply GEMINI_API_KEY in a local .env or process environment.
Portable bundles never contain that file. No HTTP route accepts or returns keys.
"""
import secrets


class Credentials:
    def __init__(self, environment_key=None):
        self._key = (environment_key or "").strip()
        self.source = "environment" if self._key else "missing"
        self.revision = 0

    @property
    def configured(self):
        return bool(self._key)

    def get(self):
        if not self._key:
            raise ValueError("Enter your Gemini API key in the Mac companion")
        return self._key

    def from_keychain(self, key):
        if not isinstance(key, str) or not 10 <= len(key.strip()) <= 256 or any(c.isspace() for c in key.strip()):
            raise ValueError("Invalid API key format")
        value = key.strip()
        if not secrets.compare_digest(value, self._key):
            self.revision += 1
        self._key = value
        self.source = "macOS Keychain"

    def public_status(self):
        return dict(configured=self.configured, source=self.source)
