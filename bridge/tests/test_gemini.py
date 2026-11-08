import io
import json
import unittest
from unittest.mock import patch

from bridge.adapters import GRIPPER_ITEMS, GeminiPredictor, apply_gripper_policy, recognition_frame
from bridge.core import MATERIALS


class GeminiTests(unittest.TestCase):
    def response(self, result, finish='STOP'):
        return io.BytesIO(json.dumps({'candidates': [{'finishReason': finish,
            'content': {'parts': [{'text': json.dumps(result)}]}}]}).encode())

    def test_flash_lite_uses_low_latency_settings(self):
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='PLASTIC')
        with patch('urllib.request.urlopen', return_value=self.response(raw)) as request:
            GeminiPredictor(key='test-key', model='gemini-3.5-flash-lite').predict((b'jpeg', 'image/jpeg'))
        config = json.loads(request.call_args.args[0].data)['generationConfig']
        self.assertEqual(config['thinkingConfig'], {'thinkingLevel': 'MINIMAL'})
        self.assertEqual(config['mediaResolution'], 'MEDIA_RESOLUTION_LOW')

    def test_cloud_image_is_smaller_without_changing_preview(self):
        try:
            import cv2
            import numpy as np
        except ImportError:
            self.skipTest('Optional camera dependencies unavailable')
        image = np.random.default_rng(7).integers(0, 256, (720, 960, 3), dtype=np.uint8)
        _, encoded = cv2.imencode('.jpg', image)
        preview = (encoded.tobytes(), 'image/jpeg')
        result = recognition_frame(preview)
        decoded = cv2.imdecode(np.frombuffer(result[0], dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(max(decoded.shape[:2]), 512)
        self.assertLess(len(result[0]), len(preview[0]))
        self.assertEqual(cv2.imdecode(np.frombuffer(preview[0], dtype=np.uint8), cv2.IMREAD_COLOR).shape[:2], (720, 960))

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'test-model'})
    def test_image_schema_and_secret_header_with_no_fabricated_confidence(self):
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='PLASTIC')
        with patch('urllib.request.urlopen', return_value=self.response(raw)) as request:
            result = GeminiPredictor().predict((b'jpeg', 'image/jpeg'))
        self.assertIsNone(result['confidence'])
        sent = request.call_args.args[0]
        self.assertNotIn('test-only-key', sent.full_url)
        self.assertEqual(sent.get_header('X-goog-api-key'), 'test-only-key')
        payload = json.loads(sent.data)
        self.assertEqual(payload['contents'][0]['parts'][1]['inline_data']['mime_type'], 'image/jpeg')
        self.assertEqual(payload['generationConfig']['responseMimeType'], 'application/json')

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'test-model'})
    def test_incomplete_and_material_mismatched_responses_are_rejected(self):
        raw = answer(label='Plastic bottle', material='PLASTIC', demo_item='Plastic bottle', bin='PAPER')
        for response in (self.response(raw), self.response(answer(), finish='MAX_TOKENS')):
            with patch('urllib.request.urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    GeminiPredictor().predict((b'jpeg', 'image/jpeg'))

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'test-model'})
    def test_schema_asks_for_free_label_material_and_demo_item(self):
        with patch('urllib.request.urlopen', return_value=self.response(answer())) as request:
            GeminiPredictor().predict((b'jpeg', 'image/jpeg'))
        payload = json.loads(request.call_args.args[0].data)
        schema = payload['generationConfig']['responseSchema']
        self.assertNotIn('enum', schema['properties']['label'])
        self.assertEqual(schema['properties']['material']['enum'], list(MATERIALS))
        self.assertEqual(schema['properties']['demo_item']['enum'], list(GRIPPER_ITEMS) + ['NONE'])
        self.assertTrue({'material', 'demo_item'} <= set(schema['required']))
        prompt = payload['contents'][0]['parts'][0]['text']
        for item in GRIPPER_ITEMS:
            self.assertIn(item, prompt)


def answer(**changes):
    return dict(dict(label='clear plastic water bottle', material='PLASTIC', demo_item='Plastic bottle',
                     item_present=True, needs_review=False, bin='PLASTIC'), **changes)


class GripperPolicyTests(unittest.TestCase):
    def test_gripper_item_keeps_its_bin_and_fixed_name(self):
        for item, bin_name in GRIPPER_ITEMS.items():
            result = apply_gripper_policy(answer(label='a ' + item.lower(), material=bin_name,
                                                 demo_item=item, bin=bin_name))
            self.assertEqual((result['bin'], result['material'], result['item']), (bin_name, bin_name, item))
            self.assertFalse(result['needs_review'])
            self.assertEqual(result['label'], 'a ' + item.lower())

    def test_recognised_item_off_the_gripper_list_is_named_but_not_sorted(self):
        result = apply_gripper_policy(answer(label='apple core', material='ORGANIC', demo_item='NONE', bin='ORGANIC'))
        self.assertEqual((result['label'], result['material'], result['bin'], result['item']),
                         ('apple core', 'ORGANIC', None, None))
        self.assertTrue(result['needs_review'])

    def test_demo_item_must_match_the_bin(self):
        result = apply_gripper_policy(answer(label='steel tin', material='METAL', demo_item='Plastic bottle', bin='METAL'))
        self.assertIsNone(result['bin'])
        self.assertTrue(result['needs_review'])

    def test_mixed_other_and_unclear_items_have_no_bin(self):
        for raw in (answer(label='foil-wrapped burrito', material='MIXED', demo_item='NONE', bin=None),
                    answer(label='glass jar', material='OTHER', demo_item='NONE', bin=None),
                    answer(label='UNKNOWN', material='OTHER', demo_item='NONE', bin=None, needs_review=True),
                    answer(needs_review=True)):
            result = apply_gripper_policy(raw)
            self.assertTrue(result['needs_review'], raw)
            self.assertIsNone(result['item'], raw)
        self.assertEqual(apply_gripper_policy(answer(needs_review=True))['bin'], 'PLASTIC')  # core refuses SORT

    def test_bin_for_mixed_or_other_material_is_rejected(self):
        for material in ('MIXED', 'OTHER'):
            with self.assertRaises(ValueError):
                apply_gripper_policy(answer(material=material, bin='PLASTIC'))
        with self.assertRaises(ValueError):
            apply_gripper_policy(answer(material='GLASS'))

    def test_empty_area_has_no_material(self):
        result = apply_gripper_policy(answer(label='EMPTY', material=None, demo_item='NONE',
                                             item_present=False, bin=None))
        self.assertEqual((result['material'], result['item'], result['bin']), (None, None, None))
        self.assertFalse(result['needs_review'])

class DiagnosticTests(unittest.TestCase):
    def test_http_errors_do_not_expose_credentials(self):
        from urllib.error import HTTPError
        from bridge.adapters import prediction_error_message
        error = HTTPError('https://example.test?key=secret', 429, 'secret', {}, None)
        message = prediction_error_message(error)
        self.assertIn('429', message)
        self.assertNotIn('secret', message)

    def test_invalid_key_and_timeout_are_distinct(self):
        from urllib.error import HTTPError
        from bridge.adapters import prediction_error_message
        error = HTTPError('https://example.test', 400, '', {},
                          io.BytesIO(b'{"error":{"message":"API key not valid"}}'))
        self.assertIn('API key', prediction_error_message(error))
        self.assertIn('30 seconds', prediction_error_message(TimeoutError()))
