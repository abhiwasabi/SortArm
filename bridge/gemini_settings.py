"""Runtime Gemini credentials, held only in this server process."""
import json
import re
import urllib.error
import urllib.request
from .adapters import GeminiPredictor, prediction_error_message


def connect_gemini(manager, body):
    key = body.get('key')
    model = body.get('model', '').strip() if isinstance(body.get('model', ''), str) else None
    if not isinstance(key, str) or not 10 <= len(key.strip()) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key.strip()):
        raise ValueError('Enter a valid Gemini API key.')
    key = key.strip()
    if model is None or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._' for c in model):
        raise ValueError('Enter a model ID without a models/ prefix.')
    request = urllib.request.Request(
        'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000',
        headers={'x-goog-api-key': key})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            available = json.load(response).get('models', [])
    except Exception as error:
        raise ValueError(prediction_error_message(error).replace('in the launch terminal', 'in Settings')) from None
    names = [m.get('name', '').removeprefix('models/') for m in available
             if 'generateContent' in m.get('supportedGenerationMethods', [])]
    if model and model not in names:
        raise ValueError('That model is not available to this API key. Check the model ID or leave it blank.')
    candidates = [model] if model else [n for n in names if 'flash' in n
        and not any(tag in n for tag in ('image', 'audio', 'tts', 'live', 'exp', 'preview'))]
    def rank(name):
        version = re.search(r'gemini-(\d+)\.(\d+)', name)
        version = tuple(-int(v) for v in version.groups()) if version else (0, 0)
        return ('lite' not in name, 'latest' in name, version, name)
    if not model:
        candidates.sort(key=rank)
    if not candidates:
        raise ValueError('No supported Gemini Flash model is available to this project.')
    last_error = None
    predictor = None
    # Google can list retired models. Probe alternatives instead of trusting the list.
    for candidate in candidates[:3]:
        probe = urllib.request.Request(
            'https://generativelanguage.googleapis.com/v1beta/models/' + candidate + ':generateContent',
            data=json.dumps({'contents': [{'parts': [{'text': 'Reply with OK.'}]}],
                             'generationConfig': {'maxOutputTokens': 64}}).encode(),
            headers={'Content-Type': 'application/json', 'x-goog-api-key': key})
        try:
            with urllib.request.urlopen(probe, timeout=10) as response:
                result = json.load(response)
            if not result.get('candidates'):
                raise ValueError('Model returned no result')
            predictor = GeminiPredictor(key=key, model=candidate)
            break
        except Exception as error:
            last_error = prediction_error_message(error)
            if isinstance(error, urllib.error.HTTPError) and error.code not in (404, 500, 502, 503, 504):
                break
    if predictor is None:
        if last_error and '404' in last_error:
            raise ValueError('The models listed for this project could not serve requests. Check the project API access and try again.')
        raise ValueError('Gemini generation check failed. ' + (last_error or 'No model responded.'))
    with manager.bridge.lock:
        manager.predictor = predictor
        manager.bridge.vision_ready = True
        manager.bridge.enabled = (manager.bridge.auto_sort_armed and manager.bridge.home_confirmed
                                  and manager.name != 'off')
        manager.bridge.last_evaluated_frame = None
        manager.bridge.invalidate('Gemini connected. Automatic sorting ready.' if manager.bridge.enabled
                                  else 'Gemini connected. Sorting remains paused.')
    return gemini_status(manager)


def gemini_status(manager):
    with manager.bridge.lock:
        predictor = manager.predictor
        return {'configured': predictor is not None,
                'model': predictor.model if predictor is not None else None}


def disconnect_gemini(manager):
    with manager.bridge.lock:
        manager.predictor = None
        manager.bridge.enabled = False
        manager.bridge.vision_ready = manager.name == 'mock'
        manager.bridge.invalidate('Gemini disconnected. Sorting paused.')
    return gemini_status(manager)
