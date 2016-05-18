# -*- coding: utf-8 -*-
#  Copyright (c) 2015 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import base64
import os

import unittest

from swift.common import swob
from swift.common.middleware import keymaster
from swift.common.middleware.crypto_utils import CRYPTO_KEY_CALLBACK
from swift.common.swob import Request
from test.unit.common.middleware.helpers import FakeSwift, FakeAppThatExcepts
from test.unit.common.middleware.crypto_helpers import TEST_KEYMASTER_CONF


def capture_start_response():
    calls = []

    def start_response(*args):
        calls.append(args)
    return start_response, calls


class TestKeymaster(unittest.TestCase):

    def setUp(self):
        super(TestKeymaster, self).setUp()
        self.swift = FakeSwift()
        self.app = keymaster.KeyMaster(self.swift, TEST_KEYMASTER_CONF)

    def test_object_path(self):
        self.verify_keys_for_path(
            '/a/c/o', expected_keys=('object', 'container'),
            key_id=base64.b64encode('/a/c/o'))

    def test_container_path(self):
        self.verify_keys_for_path(
            '/a/c', expected_keys=('container',))

    def verify_keys_for_path(self, path, expected_keys, key_id=None):
        put_keys = None
        for method, resp_class, status in (
                ('PUT', swob.HTTPCreated, '201'),
                ('POST', swob.HTTPAccepted, '202'),
                ('GET', swob.HTTPOk, '200'),
                ('HEAD', swob.HTTPNoContent, '204')):
            resp_headers = {}
            if key_id is not None:
                resp_headers.update({'X-Object-Sysmeta-Crypto-Id': key_id})
            self.swift.register(
                method, '/v1' + path, resp_class, resp_headers, '')
            req = Request.blank(
                '/v1' + path, environ={'REQUEST_METHOD': method})
            start_response, calls = capture_start_response()
            self.app(req.environ, start_response)
            self.assertEqual(1, len(calls))
            self.assertTrue(calls[0][0].startswith(status))
            self.assertNotIn('swift.crypto.override', req.environ)
            self.assertIn(CRYPTO_KEY_CALLBACK, req.environ,
                          '%s not set in env' % CRYPTO_KEY_CALLBACK)
            keys = req.environ.get(CRYPTO_KEY_CALLBACK)()
            self.assertIn('id', keys)
            id = keys.pop('id')
            self.assertEqual(base64.b64encode(path), id['path'])
            self.assertEqual('1', id['v'])
            self.assertListEqual(sorted(expected_keys), sorted(keys.keys()),
                                 '%s %s got keys %r, but expected %r'
                                 % (method, path, keys.keys(), expected_keys))
            if put_keys is not None:
                # check all key sets were consistent for this path
                self.assertDictEqual(put_keys, keys)
            else:
                put_keys = keys
        return put_keys

    def test_key_uniqueness(self):
        # a rudimentary check that different keys are made for different paths
        ref_path_parts = ('a1', 'c1', 'o1')
        path = '/' + '/'.join(ref_path_parts)
        ref_keys = self.verify_keys_for_path(
            path, expected_keys=('object', 'container'),
            key_id=base64.b64encode(path))

        # for same path and for each differing path check that keys are unique
        # when path to object or container is unique and vice-versa
        for path_parts in [(a, c, o) for a in ('a1', 'a2')
                           for c in ('c1', 'c2')
                           for o in ('o1', 'o2')]:
            path = '/' + '/'.join(path_parts)
            keys = self.verify_keys_for_path(
                path, expected_keys=('object', 'container'),
                key_id=base64.b64encode(path))
            # object keys should only be equal when complete paths are equal
            self.assertEqual(path_parts == ref_path_parts,
                             keys['object'] == ref_keys['object'],
                             'Path %s keys:\n%s\npath %s keys\n%s' %
                             (ref_path_parts, ref_keys, path_parts, keys))
            # container keys should only be equal when paths to container are
            # equal
            self.assertEqual(path_parts[:2] == ref_path_parts[:2],
                             keys['container'] == ref_keys['container'],
                             'Path %s keys:\n%s\npath %s keys\n%s' %
                             (ref_path_parts, ref_keys, path_parts, keys))

    def test_filter(self):
        factory = keymaster.filter_factory(TEST_KEYMASTER_CONF)
        self.assertTrue(callable(factory))
        self.assertTrue(callable(factory(self.swift)))

    def test_app_exception(self):
        app = keymaster.KeyMaster(
            FakeAppThatExcepts(), TEST_KEYMASTER_CONF)
        req = Request.blank('/', environ={'REQUEST_METHOD': 'PUT'})
        start_response, _ = capture_start_response()
        self.assertRaises(Exception, app, req.environ, start_response)

    def test_root_secret(self):
        for secret in (os.urandom(32), os.urandom(33), os.urandom(50)):
            encoded_secret = base64.b64encode(secret)
            try:
                app = keymaster.KeyMaster(
                    self.swift, {'encryption_root_secret':
                                 bytes(encoded_secret)})
                self.assertEqual(secret, app.root_secret)
            except AssertionError as err:
                self.fail(str(err) + ' for secret %s' % secret)
            try:
                app = keymaster.KeyMaster(
                    self.swift, {'encryption_root_secret':
                                 unicode(encoded_secret)})
                self.assertEqual(secret, app.root_secret)
            except AssertionError as err:
                self.fail(str(err) + ' for secret %s' % secret)

    def test_invalid_root_secret(self):
        for secret in (bytes(base64.b64encode(os.urandom(31))),  # too short
                       unicode(base64.b64encode(os.urandom(31))),
                       u'?' * 44, b'?' * 44,  # not base64
                       u'a' * 45, b'a' * 45,  # bad padding
                       99, None):
            try:
                conf = {'encryption_root_secret': secret}
                with self.assertRaises(ValueError) as err:
                    keymaster.KeyMaster(self.swift, conf)
                self.assertEqual(
                    'encryption_root_secret option in proxy-server.conf '
                    'must be a base64 encoding of at least 32 raw bytes',
                    err.exception.message)
            except AssertionError as err:
                self.fail(str(err) + ' for conf %s' % str(conf))


if __name__ == '__main__':
    unittest.main()
