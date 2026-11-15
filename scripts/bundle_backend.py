"""Short-lived GitHub identity; never persists or logs bearer tokens."""
import json
import os
import urllib.request
from urllib.parse import urlencode


def call(path, payload=None):
    query = urlencode({'audience': 'instapilot-bundle-worker'})
    base = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    url = base + ('&' if '?' in base else '?') + query
    request = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with urllib.request.urlopen(request, timeout=30) as response:
        token = json.load(response)['value']
    request = urllib.request.Request(os.environ['BACKEND_URL'].rstrip('/') + '/api/bundle-worker/' + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)
