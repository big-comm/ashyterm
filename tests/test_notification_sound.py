"""Tests for the bundled tab-attention notification sounds."""

from unittest.mock import MagicMock, patch

from ashyterm.utils.sound import (
    NOTIFICATION_SOUNDS,
    SOUND_NONE,
    SOUNDS_DIR,
    play_notification_sound,
    sound_path,
)


class TestBundledSounds:
    def test_every_listed_sound_ships_a_file(self):
        missing = [name for name in NOTIFICATION_SOUNDS if sound_path(name) is None]
        assert missing == []

    def test_ten_sounds_are_offered(self):
        assert len(NOTIFICATION_SOUNDS) == 10

    def test_names_are_unique(self):
        assert len(set(NOTIFICATION_SOUNDS)) == len(NOTIFICATION_SOUNDS)

    def test_no_stray_wav_files_shipped(self):
        # The generator writes .wav; only the encoded .oga belongs in the package.
        assert list(SOUNDS_DIR.glob("*.wav")) == []

    def test_sounds_stay_small(self):
        # Guards against dropping an unencoded or overlong file into the package.
        for name in NOTIFICATION_SOUNDS:
            assert sound_path(name).stat().st_size < 64 * 1024


class TestSoundPathSafety:
    def test_none_resolves_to_nothing(self):
        assert sound_path(SOUND_NONE) is None

    def test_empty_name_resolves_to_nothing(self):
        assert sound_path("") is None

    def test_unknown_name_is_rejected(self):
        assert sound_path("does-not-exist") is None

    def test_path_traversal_is_rejected(self):
        # Only names on the allowlist resolve, so a crafted setting value cannot
        # point playback at an arbitrary file.
        assert sound_path("../../../etc/passwd") is None
        assert sound_path("/etc/passwd") is None


class TestPlayback:
    def test_playing_none_is_a_silent_noop(self):
        assert play_notification_sound(SOUND_NONE) is False

    def test_missing_backend_does_not_raise(self):
        with patch("ashyterm.utils.sound._get_context", return_value=None):
            assert play_notification_sound("bell") is False

    def test_backend_failure_is_swallowed(self):
        context = MagicMock()
        context.play_simple.side_effect = RuntimeError("no audio device")
        with patch("ashyterm.utils.sound._get_context", return_value=context):
            assert play_notification_sound("bell") is False
