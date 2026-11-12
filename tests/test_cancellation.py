import threading
import time
import unittest
from unittest.mock import Mock, patch

from lodestar.cancellation import interruptible
from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError


class CancellationTests(unittest.TestCase):
    def test_stalled_stream_stops_promptly_and_never_delivers_late_text(self):
        signal, delivered, release, finished = threading.Event(), threading.Event(), threading.Event(), threading.Event()
        owner = threading.get_ident()
        seen = []
        def check():
            if signal.is_set(): raise LLMError('cancelled',code='cancelled')
        def operation(emit):
            try:
                emit('first')
                release.wait(3)
                emit('late')
                return 'late result'
            finally:
                finished.set()
        def text(value):
            self.assertEqual(threading.get_ident(),owner)
            seen.append(value); delivered.set()
        stopper=threading.Thread(target=lambda:(delivered.wait(2),signal.set()),daemon=True)
        stopper.start()
        started=time.monotonic()
        try:
            with self.assertRaises(LLMError) as caught:
                interruptible(operation,check_cancel=check,on_text=text)
            self.assertEqual(caught.exception.code,'cancelled')
            self.assertLess(time.monotonic()-started,.8)
            self.assertEqual(seen,['first'])
        finally:
            release.set(); finished.wait(2); stopper.join(2)
        self.assertEqual(seen,['first'])

    def test_cancel_before_response_headers_defers_resource_close_without_blocking(self):
        client=LLMClient(Config(llm_mode='mock'))
        client.mode='live'; client._client=Mock(); client.cancel_signal=threading.Event()
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        def blocked(*args):
            entered.set(); release.wait(3)
            return {'role':'assistant','content':[{'type':'text','text':'late'}]}
        stopper=threading.Thread(target=lambda:(entered.wait(2),client.cancel_signal.set()),daemon=True)
        with patch('lodestar.providers.dialogue.step',side_effect=blocked):
            stopper.start(); started=time.monotonic()
            try:
                with self.assertRaises(LLMError) as caught:
                    client.dialogue_step('system',[],[])
                self.assertEqual(caught.exception.code,'cancelled')
                client.close()
                self.assertLess(time.monotonic()-started,.8)
                client._client.close.assert_not_called()
            finally:
                release.set(); stopper.join(2)
                deadline=time.monotonic()+2
                while not client._client.close.called and time.monotonic()<deadline: time.sleep(.01)
            client._client.close.assert_called_once()

    def test_pre_cancelled_call_never_starts_network(self):
        client=LLMClient(Config(llm_mode='mock'))
        client.mode='live'; client.cancel_signal=threading.Event(); client.cancel_signal.set()
        with patch('lodestar.providers.dialogue.step') as step:
            with self.assertRaises(LLMError): client.dialogue_step('system',[],[])
        step.assert_not_called(); client.close()


if __name__=='__main__': unittest.main()
