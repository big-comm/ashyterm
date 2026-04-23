# tests/test_crypto.py
"""
Tests for ashyterm.utils.crypto.

Simulates libsecret via sys.modules hooks so the real GNOME Keyring
stack is not touched during CI.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _install_mock_secret(monkeypatch, available=True):
    """Install a mock Secret module and reset ashyterm.utils.crypto state."""
    gi_mod = sys.modules.get("gi") or MagicMock()
    gi_repo = sys.modules.get("gi.repository") or MagicMock()

    if available:
        mock_secret = MagicMock()

        class FakeSchema:
            pass

        class FakeSchemaFlags:
            NONE = 0

        class FakeAttrType:
            STRING = "string"

        mock_secret.Schema.new = MagicMock(return_value=FakeSchema())
        mock_secret.SchemaFlags = FakeSchemaFlags
        mock_secret.SchemaAttributeType = FakeAttrType
        mock_secret.COLLECTION_DEFAULT = "default"

        # Simple in-memory keyring backed by a dict so tests can assert it.
        storage = {}

        def store(schema, attrs, coll, label, password, cancellable):
            storage[attrs["session_name"]] = password
            return True

        def lookup(schema, attrs, cancellable):
            return storage.get(attrs["session_name"])

        def clear(schema, attrs, cancellable):
            return storage.pop(attrs["session_name"], None) is not None

        mock_secret.password_store_sync = MagicMock(side_effect=store)
        mock_secret.password_lookup_sync = MagicMock(side_effect=lookup)
        mock_secret.password_clear_sync = MagicMock(side_effect=clear)
        mock_secret._ashy_storage = storage

        gi_repo.Secret = mock_secret
        monkeypatch.setitem(sys.modules, "gi", gi_mod)
        monkeypatch.setitem(sys.modules, "gi.repository", gi_repo)

    # Force re-import of crypto so the module-level probe runs with our mock.
    sys.modules.pop("ashyterm.utils.crypto", None)
    import ashyterm.utils.crypto as crypto

    if available:
        # Override availability flag if the real Secret wasn't importable.
        crypto._SECRET_AVAILABLE = True
        crypto.Secret = gi_repo.Secret
        crypto.SECRET_SCHEMA = object()
    else:
        crypto._SECRET_AVAILABLE = False
        crypto.Secret = None
        crypto.SECRET_SCHEMA = None

    return crypto


class TestIsEncryptionAvailable:
    def test_returns_true_when_secret_loaded(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        assert crypto.is_encryption_available() is True

    def test_returns_false_when_secret_unavailable(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=False)
        assert crypto.is_encryption_available() is False


class TestStorePassword:
    def test_store_round_trip(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        assert crypto.store_password("mybox", "s3cret") is True
        assert crypto.lookup_password("mybox") == "s3cret"

    def test_store_without_encryption_raises(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=False)
        with pytest.raises(Exception):
            crypto.store_password("foo", "bar")

    def test_store_forwards_backend_errors(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        crypto.Secret.password_store_sync = MagicMock(side_effect=RuntimeError("nope"))
        with pytest.raises(Exception):
            crypto.store_password("mybox", "pw")


class TestLookupPassword:
    def test_lookup_unknown_returns_none(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        assert crypto.lookup_password("nonexistent") is None

    def test_lookup_without_encryption_raises(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=False)
        with pytest.raises(Exception):
            crypto.lookup_password("any")


class TestClearPassword:
    def test_clear_existing_password(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        crypto.store_password("to-clear", "pw")
        assert crypto.clear_password("to-clear") is True
        assert crypto.lookup_password("to-clear") is None

    def test_clear_missing_password_returns_false(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        assert crypto.clear_password("never-stored") is False

    def test_clear_without_encryption_returns_false(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=False)
        assert crypto.clear_password("any") is False


class TestExportAllPasswords:
    def test_export_skips_non_password_sessions(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=True)
        crypto.store_password("session1", "pw1")

        # Shared base so isinstance(...) passes inside export_all_passwords.
        class FakeSessionItem:
            def __init__(self, name, uses_password):
                self.name = name
                self._uses_password = uses_password

            def uses_password_auth(self):
                return self._uses_password

        # Replace the SessionItem import inside crypto.
        fake_models = types.ModuleType("ashyterm.sessions.models")
        fake_models.SessionItem = FakeSessionItem
        monkeypatch.setitem(sys.modules, "ashyterm.sessions.models", fake_models)

        items = [
            FakeSessionItem("session1", uses_password=True),
            FakeSessionItem("session2", uses_password=False),
        ]
        store = MagicMock()
        store.get_n_items = MagicMock(return_value=len(items))
        store.get_item = MagicMock(side_effect=lambda i: items[i])

        result = crypto.export_all_passwords(store)
        assert result == {"session1": "pw1"}

    def test_export_when_encryption_unavailable(self, monkeypatch):
        crypto = _install_mock_secret(monkeypatch, available=False)
        store = MagicMock()
        # Should return empty dict without raising.
        assert crypto.export_all_passwords(store) == {}
