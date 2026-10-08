import tempfile
import unittest
from pathlib import Path

from lodestar.retrieval import select_excerpt
from lodestar.memory import learning
from lodestar.memory.db import open_db


class RetrievalTests(unittest.TestCase):
    def test_target_after_intro_is_selected(self):
        text = ('Introduction background. ' * 500) + 'Dependency-Scoped Propagation uses ancestor records.'
        result = select_excerpt(text, 'Dependency-Scoped Propagation', 2000)
        self.assertIn('ancestor records', result['text'])
        self.assertTrue(result['query_matched'])
        self.assertTrue(result['truncated'])

    def test_recall_excludes_unrelated_and_other_users(self):
        with tempfile.TemporaryDirectory() as temp:
            conn = open_db(Path(temp)/'db')
            try:
                learning.record(conn,technology='Harness',method='Tool routing',event='discussed',evidence='User discussion')
                learning.record(conn,technology='Diffusion',event='discussed',evidence='Other topic')
                learning.record(conn,user_id='other',technology='Harness',event='discussed',evidence='Other user')
                result = learning.recall(conn,'Harness tool routing')
                self.assertEqual(len(result),1)
                self.assertEqual(result[0]['technology'],'Harness')
                self.assertEqual(result[0]['mastery'],'unknown')
                self.assertEqual(learning.recall(conn,'unrecorded topic'),[])
            finally:
                conn.close()
