import io
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from bridge.gemini_settings import connect_gemini, disconnect_gemini, gemini_status

class GeminiSettingsTests(unittest.TestCase):
    def setUp(self):
        self.bridge = SimpleNamespace(lock=threading.RLock(), enabled=True, auto_sort_armed=False,
                                      home_confirmed=False, vision_ready=False,
                                      last_evaluated_frame=None, invalidate=lambda message: None)
        self.manager = SimpleNamespace(bridge=self.bridge, predictor=None, name='esp32')
        self.key = 'test-secret-key-do-not-expose'

    def response(self):
        return io.BytesIO(json.dumps({'models': [{'name': 'models/gemini-flash-latest',
            'supportedGenerationMethods': ['generateContent']}]}).encode())

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_connect_verifies_access_and_never_returns_key(self, request):
        request.side_effect = [self.response(), io.BytesIO(b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}')]
        result = connect_gemini(self.manager, {'key': self.key})
        self.assertTrue(result['configured'])
        self.assertNotIn(self.key, json.dumps(result))
        self.assertEqual(self.manager.predictor.key, self.key)
        self.assertFalse(self.bridge.enabled)
        self.assertTrue(self.bridge.vision_ready)
        result = disconnect_gemini(self.manager)
        self.assertFalse(result['configured'])
        self.assertIsNone(self.manager.predictor)
        self.assertFalse(self.bridge.vision_ready)

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_connect_resumes_armed_simulated_sorting(self, request):
        self.bridge.auto_sort_armed = True
        self.bridge.home_confirmed = True
        request.side_effect = [self.response(), io.BytesIO(b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}')]
        connect_gemini(self.manager, {'key': self.key})
        self.assertTrue(self.bridge.enabled)

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_failure_does_not_replace_previous_connection_or_echo_error(self, request):
        previous = SimpleNamespace(model='previous')
        self.manager.predictor = previous
        request.side_effect = RuntimeError(self.key)
        with self.assertRaises(ValueError) as error:
            connect_gemini(self.manager, {'key': self.key})
        self.assertNotIn(self.key, str(error.exception))
        self.assertIs(self.manager.predictor, previous)

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_unavailable_model_is_rejected(self, request):
        request.return_value = self.response()
        with self.assertRaises(ValueError):
            connect_gemini(self.manager, {'key': self.key, 'model': 'not-available'})
        self.assertFalse(gemini_status(self.manager)['configured'])

    def test_header_injection_is_rejected_before_request(self):
        with self.assertRaises(ValueError):
            connect_gemini(self.manager, {'key': self.key + '\r\nInjected: true'})

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_retired_model_404_tries_next_available_model(self, request):
        import urllib.error
        listing = io.BytesIO(json.dumps({'models': [
            {'name':'models/gemini-3.5-flash-lite', 'supportedGenerationMethods':['generateContent']},
            {'name':'models/gemini-3.1-flash-lite', 'supportedGenerationMethods':['generateContent']}
        ]}).encode())
        request.side_effect = [listing, urllib.error.HTTPError('https://example.com',404,'not found',{},None),
                               io.BytesIO(b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}')]
        result = connect_gemini(self.manager, {'key':self.key})
        self.assertTrue(result['configured'])
        self.assertEqual(result['model'],'gemini-3.1-flash-lite')
        self.assertNotIn(self.key, json.dumps(result))

    @patch('bridge.gemini_settings.urllib.request.urlopen')
    def test_connect_prefers_gemini_38_flash(self, request):
        listing = io.BytesIO(json.dumps({'models': [
            {'name':'models/gemini-3.5-flash-lite', 'supportedGenerationMethods':['generateContent']},
            {'name':'models/gemini-3.8-flash', 'supportedGenerationMethods':['generateContent']}
        ]}).encode())
        request.side_effect = [listing, io.BytesIO(b'{"candidates":[{"content":{"parts":[{"text":"OK"}]}}]}')]
        result = connect_gemini(self.manager, {'key': self.key})
        self.assertEqual(result['model'], 'gemini-3.8-flash')
