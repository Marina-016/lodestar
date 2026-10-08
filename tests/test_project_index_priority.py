import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from lodestar.project_index import index_local_project


class ProjectIndexPriorityTests(unittest.TestCase):
    def test_core_evidence_survives_document_budget_growth(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('a.md','b.md','core.py'):
                (root/name).write_text('source evidence',encoding='utf-8')
            with patch('lodestar.project_index.MAX_FILES',2):
                documents=index_local_project(root,preferred_paths=('core.py','core.py'))
            self.assertEqual(len(documents),2)
            self.assertEqual(documents[0]['path'],'core.py')
            self.assertEqual(sum(d['path']=='core.py' for d in documents),1)

    def test_preferred_path_cannot_escape_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                index_local_project(temp,preferred_paths=('../outside.py',))
