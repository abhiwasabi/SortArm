import io
import json
import unittest
from unittest.mock import patch

from bridge.adapters import GeminiPredictor


class GeminiTests(unittest.TestCase):
    def response(self, result, finish='STOP'):
        return io.BytesIO(json.dumps({'candidates': [{'finishReason': finish,
            'content': {'parts': [{'text': json.dumps(result)}]}}]}).encode())

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
    def test_incomplete_and_policy_mismatched_responses_are_rejected(self):
        raw = dict(label='Plastic bottle', item_present=True, needs_review=False, bin='PAPER')
        for response in (self.response(raw), self.response(raw, finish='MAX_TOKENS')):
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
