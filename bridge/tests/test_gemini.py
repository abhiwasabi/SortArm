import io
import json
import unittest
from unittest.mock import patch

from bridge.adapters import GeminiPredictor, recognition_frame


class GeminiTests(unittest.TestCase):
    def response(self, result, finish='STOP'):
        return io.BytesIO(json.dumps({'candidates': [{'finishReason': finish,
            'content': {'parts': [{'text': json.dumps(result)}]}}]}).encode())

    def test_flash_lite_uses_low_latency_settings(self):
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='RECYCLE')
        with patch('urllib.request.urlopen', return_value=self.response(raw)) as request:
            GeminiPredictor(key='test-key', model='gemini-3.5-flash-lite').predict((b'jpeg', 'image/jpeg'))
        config = json.loads(request.call_args.args[0].data)['generationConfig']
        self.assertEqual(config['thinkingConfig'], {'thinkingLevel': 'MINIMAL'})
        self.assertEqual(config['mediaResolution'], 'MEDIA_RESOLUTION_LOW')

    def test_gemini_38_flash_uses_low_thinking(self):
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='RECYCLE')
        with patch('urllib.request.urlopen', return_value=self.response(raw)) as request:
            GeminiPredictor(key='test-key', model='gemini-3.8-flash').predict((b'jpeg', 'image/jpeg'))
        config = json.loads(request.call_args.args[0].data)['generationConfig']
        self.assertEqual(config['thinkingConfig'], {'thinkingLevel': 'LOW'})
        self.assertNotIn('mediaResolution', config)

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
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='RECYCLE')
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
    def test_named_objects_keep_a_dominant_material_bin(self):
        cases = (
            ('Cracker Jack bag', 'RECYCLE'),
            ('cardboard shipping box', 'RECYCLE'),
            ('aluminum soda can', 'RECYCLE'),
            ('apple core', 'TRASH'),
        )
        for label, bin_name in cases:
            raw = dict(label=label, item_present=True, needs_review=False, bin=bin_name)
            with patch('urllib.request.urlopen', return_value=self.response(raw)) as request:
                result = GeminiPredictor().predict((b'jpeg', 'image/jpeg'))
            self.assertEqual((result['label'], result['bin']), (label, bin_name))
            prompt = json.loads(request.call_args.args[0].data)['contents'][0]['parts'][0]['text']
            schema = json.loads(request.call_args.args[0].data)['generationConfig']['responseSchema']
        self.assertNotIn('enum', schema['properties']['label'])
        for rule in ('Cracker Jack bag', 'RECYCLE', 'TRASH', 'clearly dominant'):
            self.assertIn(rule, prompt)

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'test-model'})
    def test_a_named_object_without_a_bin_is_rejected(self):
        raw = dict(label='glass jar', item_present=True, needs_review=False, bin=None)
        with patch('urllib.request.urlopen', return_value=self.response(raw)):
            with self.assertRaises(ValueError):
                GeminiPredictor().predict((b'jpeg', 'image/jpeg'))

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-only-key', 'GEMINI_MODEL': 'test-model'})
    def test_incomplete_and_unclear_binned_responses_are_rejected(self):
        unclear = dict(label='UNKNOWN', item_present=True, needs_review=True, bin='RECYCLE')
        named = dict(label='Cracker Jack bag', item_present=True, needs_review=False, bin='RECYCLE')
        for response in (self.response(unclear), self.response(named, finish='MAX_TOKENS')):
            with patch('urllib.request.urlopen', return_value=response):
                with self.assertRaises(ValueError):
                    GeminiPredictor().predict((b'jpeg', 'image/jpeg'))

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
