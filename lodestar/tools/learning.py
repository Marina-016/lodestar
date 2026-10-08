"""Learning tools shared by the deterministic Agent and MCP clients."""
from lodestar.memory import learning
from lodestar.tools.registry import register


def read_profile(ws, user_id='default', technology=None, limit=100, query=None):
    if query:
        return {'profiles': learning.recall(ws.conn, query, user_id, limit)}
    return {'profiles': learning.profile(ws.conn, user_id, technology, limit)}


def record_evidence(ws, technology, event, evidence, method='', paper_url='',
                    user_id='default', actor='user', task_id=None):
    return learning.record(ws.conn, technology=technology, event=event,
        evidence=evidence, method=method, paper_url=paper_url, user_id=user_id,
        actor=actor, task_id=task_id)


register('read_learning_profile', 'Read user technology/method exposure and evidence-based mastery.',
         read_profile, {'user_id': {'type': 'string'}, 'technology': {'type': 'string'},
                        'limit': {'type': 'integer'}, 'query': {'type': 'string'}})
register('record_learning_evidence', 'Append a learning observation; exposure never establishes mastery.',
         record_evidence, {'technology': {'type': 'string', 'required': True},
          'event': {'type': 'string', 'required': True},
          'evidence': {'type': 'string', 'required': True}, 'method': {'type': 'string'},
          'paper_url': {'type': 'string'}, 'user_id': {'type': 'string'},
          'actor': {'type': 'string'}, 'task_id': {'type': 'string'}})
