import io
import json
import unittest
from unittest.mock import patch

from bridge.swarms import review_pickup


def prediction(**changes):
    base = dict(label='Cracker Jack bag', bin='PLASTIC', confidence=None,
                item_present=True, needs_review=False)
    base.update(changes)
    return base


def response(text):
    body = {'output': [{'role': 'Gripper safety', 'content': 'ordinary snack bag'},
                       {'role': 'Decision', 'content': text}]}
    return io.BytesIO(json.dumps(body).encode())


class SwarmsPickupTests(unittest.TestCase):
    def test_no_key_leaves_gemini_unchanged_and_makes_no_request(self):
        seen = []
        with patch.dict('os.environ', {}, clear=True):
            result = review_pickup(prediction(), opener=lambda *args, **kwargs: seen.append(args))
        self.assertEqual(result['bin'], 'PLASTIC')
        self.assertFalse(result['needs_review'])
        self.assertEqual(seen, [])

    def test_unsafe_item_is_held(self):
        with patch('urllib.request.urlopen', return_value=response('{"safe": false, "reason": "broken glass"}')):
            result = review_pickup(prediction(label='broken glass jar', bin='PLASTIC'), key='test-key')
        self.assertIsNone(result['bin'])
        self.assertTrue(result['needs_review'])
        self.assertEqual(result['label'], 'broken glass jar')
        self.assertEqual(result['hold_reason'], 'broken glass')

    def test_safe_item_keeps_its_bin(self):
        with patch('urllib.request.urlopen', return_value=response('{"safe": true}')) as request:
            result = review_pickup(prediction(), key='test-key')
        self.assertEqual((result['bin'], result['needs_review']), ('PLASTIC', False))
        self.assertNotIn('approve_reason', result)
        sent = request.call_args.args[0]
        self.assertNotIn('test-key', sent.full_url)
        self.assertEqual(sent.get_header('X-api-key'), 'test-key')
        payload = json.loads(sent.data)
        self.assertEqual(payload['swarm_type'], 'SequentialWorkflow')
        self.assertIn('Cracker Jack bag', payload['task'])
        self.assertEqual([agent['agent_name'] for agent in payload['agents']], ['Gripper safety', 'Decision'])

    def test_safe_item_keeps_its_bin_and_the_approval_reason(self):
        with patch('urllib.request.urlopen', return_value=response('{"safe": true, "reason": "it is a dry snack bag"}')):
            result = review_pickup(prediction(), key='test-key')
        self.assertEqual(result['bin'], 'PLASTIC')
        self.assertFalse(result['needs_review'])
        self.assertEqual(result['approve_reason'], 'it is a dry snack bag')

    def test_junk_and_errors_leave_gemini_in_charge(self):
        cases = (
            response('not json'),
            response('{"safe": "maybe"}'),
            io.BytesIO(b'{"output": "plain text"}'),
        )
        for body in cases:
            with patch('urllib.request.urlopen', return_value=body):
                result = review_pickup(prediction(), key='test-key')
            self.assertEqual(result['bin'], 'PLASTIC', body)

        def explode(*args, **kwargs):
            raise TimeoutError('slow')

        result = review_pickup(prediction(), key='test-key', opener=explode)
        self.assertEqual(result['bin'], 'PLASTIC')

    def test_empty_and_unknown_skip_the_call(self):
        seen = []

        def opener(*args, **kwargs):
            seen.append(args)

        for raw in (prediction(item_present=False, label='EMPTY', bin=None),
                    prediction(label='UNKNOWN', needs_review=True, bin=None)):
            review_pickup(raw, key='test-key', opener=opener)
        self.assertEqual(seen, [])

    def test_named_item_without_a_bin_can_still_be_held(self):
        with patch('urllib.request.urlopen', return_value=response('{"safe": false, "reason": "battery"}')):
            result = review_pickup(prediction(label='Duracell AAA battery', bin=None, needs_review=True), key='test-key')
        self.assertIsNone(result['bin'])
        self.assertTrue(result['needs_review'])
        self.assertEqual(result['hold_reason'], 'battery')
