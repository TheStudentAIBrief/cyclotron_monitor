"""N2 hardening: concurrent EUR-form imports must not drop manifest entries.

The manifest is the NNR audit index; a lost entry is a compliance gap. Before the
lock, the read-modify-write of manifest.json could clobber concurrent entries.
"""
import json
import os
import threading

from monitor.gauge_archive import archive_import, MANIFEST_NAME


def test_concurrent_imports_all_recorded(tmp_path):
    archive_dir = str(tmp_path / 'ga_concurrent')
    n = 40

    def worker(i):
        archive_import(f'form_{i}.jpg', b'\xff\xd8\xff', '{}', [], archive_dir)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    with open(os.path.join(archive_dir, MANIFEST_NAME), encoding='utf-8') as f:
        manifest = json.load(f)

    assert len(manifest) == n                       # no lost manifest entries
    entry_dirs = {e['entry_dir'] for e in manifest}
    assert len(entry_dirs) == n                     # all unique
    for d in entry_dirs:
        assert os.path.isdir(os.path.join(archive_dir, d))
