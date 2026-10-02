"""Regressietests voor de begrensde, opnieuw maakbare schijfcache."""

import os
import time

import core


def test_lru_pruner_removes_oldest_files_first(tmp_path):
    old = tmp_path / "old.jpg"
    middle = tmp_path / "middle.jpg"
    newest = tmp_path / "newest.jpg"
    for path in (old, middle, newest):
        path.write_bytes(b"x" * 700_000)

    now = time.time()
    os.utime(old, (now - 30, now - 30))
    os.utime(middle, (now - 20, now - 20))
    os.utime(newest, (now - 10, now - 10))

    removed, freed = core._prune_lru_files(iter(tmp_path.glob("*.jpg")), max_mb=1)

    assert removed == 2
    assert freed == 1_400_000
    assert not old.exists()
    assert not middle.exists()
    assert newest.exists()


def test_ai_image_is_deleted_after_bytes_are_loaded(tmp_path):
    image_dir = tmp_path / "document" / "ai"
    image_dir.mkdir(parents=True)
    image = image_dir / "page_0.jpg"
    image.write_bytes(b"jpeg-bytes")

    part = core.image_part(image)

    assert part.image_bytes == b"jpeg-bytes"
    assert not image.exists()
