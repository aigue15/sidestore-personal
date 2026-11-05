"""Validate the official schema's used keywords with a small offline walker.
This is intentionally not a general JSON Schema implementation.
"""
import json
from pathlib import Path
import unittest
import test_repo
import github_publish

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / 'vendor/sidestore-schema.json').read_text())

def validate(value, schema):
    if '$ref' in schema:
        schema = SCHEMA['definitions'][schema['$ref'].rsplit('/', 1)[1]]
    kinds = {'object': dict, 'array': list, 'string': str, 'number': (int, float), 'boolean': bool}
    if 'type' in schema:
        assert isinstance(value, kinds[schema['type']]), 'Wrong type'
    if 'enum' in schema:
        assert value in schema['enum'], 'Wrong enum'
    if isinstance(value, dict):
        assert set(schema.get('required', [])) <= value.keys(), 'Missing field'
        if schema.get('additionalProperties') is False:
            assert value.keys() <= schema['properties'].keys(), 'Unknown field'
        for key, item in value.items():
            if key in schema.get('properties', {}): validate(item, schema['properties'][key])
    if isinstance(value, list):
        for item in value: validate(item, schema['items'])

class SchemaTests(unittest.TestCase):
    def test_fixture_and_checked_in_catalogue(self):
        fixture = test_repo.Tests(); fixture.setUp()
        try:
            fixture.add(fixture.ipa())
            validate(fixture.source(), SCHEMA)
            published, _ = github_publish.prepare(fixture.store, 'example/apps')
            for app in published['apps']:
                for version in app['versions']:
                    # Explicit current-decoder extension absent from older published schema.
                    self.assertRegex(version.pop('sha256'), r'^[0-9a-f]{64}$')
            validate(published, SCHEMA)
            source = json.loads((Path(__file__).resolve().parents[1] / 'source.json').read_text())
            validate(source, SCHEMA)
        finally: fixture.doCleanups()
