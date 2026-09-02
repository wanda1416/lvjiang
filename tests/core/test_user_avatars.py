"""Session avatar asset library."""

import base64

import pytest

from lvjiang.core.user_avatars import UserAvatarStore, is_safe_avatar_filename

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "/x8AAusB9Y9Z4x8AAAAASUVORK5CYII="
)


def test_save_is_content_addressed_and_listed(tmp_path):
    store = UserAvatarStore(tmp_path)

    first = store.save_png(_ONE_PIXEL_PNG)
    second = store.save_png(_ONE_PIXEL_PNG)

    assert first == second
    assert is_safe_avatar_filename(first)
    assert store.list_filenames() == (first,)
    assert store.path_for(first).read_bytes() == _ONE_PIXEL_PNG


def test_rejects_non_png_and_unsafe_filenames(tmp_path):
    store = UserAvatarStore(tmp_path)

    with pytest.raises(ValueError, match="PNG"):
        store.save_png(b"not-an-image")
    assert store.path_for("../avatar.png") is None
    assert not is_safe_avatar_filename("avatar.png")


def test_store_deletes_only_generated_avatar_files(tmp_path):
    store = UserAvatarStore(tmp_path)
    filename = store.save_png(_ONE_PIXEL_PNG)

    assert store.delete(filename) is True
    assert not (tmp_path / filename).exists()
    assert store.delete(filename) is False
    assert store.delete("../session.json") is False
