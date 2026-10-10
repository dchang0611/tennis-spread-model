import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import build_spread_site as site

class SnapshotPersistenceTests(unittest.TestCase):
    def test_failure_preserves_verified_snapshot_and_success_replaces_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            target = root / 'site' / 'data'
            good = {'generated_at': '2026-10-10T07:00:00Z', 'picks': [{'player':'A'}]}
            with patch.object(site, 'ROOT', root), patch.object(site, 'SITE_DATA', target):
                with patch.object(site, 'build_payload', return_value=dict(good)):
                    site.main()
                old = json.loads((root/'data'/'last_successful_snapshot.json').read_text())
                with patch.object(site, 'build_payload', return_value={'generated_at':'2026-10-11T07:00:00Z','picks':[], 'status':'closed'}):
                    site.main()
                board = json.loads((target/'board.json').read_text())
                self.assertEqual(board['snapshot'], old)
                self.assertEqual(board['picks'], [])
                self.assertEqual(board['status'], 'closed')
                self.assertEqual(board['snapshot']['generated_at'], good['generated_at'])
