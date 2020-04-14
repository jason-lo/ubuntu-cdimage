#! /usr/bin/python

# Copyright (C) 2012 Canonical Ltd.
# Author: Colin Watson <cjwatson@ubuntu.com>

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""Unit tests for cdimage.sign."""

import os
import subprocess

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage.config import Config
from cdimage.sign import _gnupg_files, _signing_command, sign_cdimage
from cdimage.tests.helpers import TestCase, touch


class TestSign(TestCase):
    def test_gnupg_files(self):
        config = Config(read=False)
        config["GNUPG_DIR"] = "/path"
        gpgconf, secring, pubring, trustdb = _gnupg_files(config)
        self.assertEqual("/path/gpg.conf", gpgconf)
        self.assertEqual("/path/secring.gpg", secring)
        self.assertEqual("/path/pubring.gpg", pubring)
        self.assertEqual("/path/trustdb.gpg", trustdb)

    def test_signing_command(self):
        config = Config(read=False)
        config["GNUPG_DIR"] = "/path"
        config["SIGNING_KEYID"] = "01234567"
        command = _signing_command(config)
        self.assertEqual([
            "gpg", "--options", "/path/gpg.conf",
            "--no-default-keyring",
            "--secret-keyring", "/path/secring.gpg",
            "--keyring", "/path/pubring.gpg",
            "--trustdb-name", "/path/trustdb.gpg",
            "--no-options", "--batch", "--no-tty",
            "--armour", "--detach-sign",
            "--digest-algo", "SHA512",
            "-u", "01234567",
        ], command)

    def test_signing_command_two_keys(self):
        config = Config(read=False)
        config["GNUPG_DIR"] = "/path"
        config["SIGNING_KEYID"] = "01234567 89ABCDEF"
        command = _signing_command(config)
        self.assertEqual([
            "gpg", "--options", "/path/gpg.conf",
            "--no-default-keyring",
            "--secret-keyring", "/path/secring.gpg",
            "--keyring", "/path/pubring.gpg",
            "--trustdb-name", "/path/trustdb.gpg",
            "--no-options", "--batch", "--no-tty",
            "--armour", "--detach-sign",
            "--digest-algo", "SHA512",
            "-u", "01234567", "-u", "89ABCDEF",
        ], command)

    def test_sign_cdimage_missing_gnupg_files(self):
        config = Config(read=False)
        config["GNUPG_DIR"] = self.use_temp_dir()
        config["SIGNING_KEYID"] = "01234567"
        self.capture_logging()
        self.assertFalse(sign_cdimage(config, "dummy"))
        self.assertLogEqual(["No keys found; not signing images."])

    def test_sign_cdimage_missing_signing_keyid(self):
        config = Config(read=False)
        self.use_temp_dir()
        for tail in "secring.gpg", "pubring.gpg", "trustdb.gpg":
            touch(os.path.join(self.temp_dir, tail))
        config["GNUPG_DIR"] = self.temp_dir
        self.capture_logging()
        self.assertFalse(sign_cdimage(config, "dummy"))
        self.assertLogEqual(["No keys found; not signing images."])

    @mock.patch("subprocess.check_call")
    def test_sign_cdimage_configured(self, mock_check_call):
        config = Config(read=False)
        config["GNUPG_DIR"] = self.use_temp_dir()
        config["SIGNING_KEYID"] = "01234567"
        gpgconf, secring, pubring, trustdb = _gnupg_files(config)
        sign_path = os.path.join(self.temp_dir, "to-sign")
        for path in gpgconf, secring, pubring, trustdb, sign_path:
            touch(path)
        self.capture_logging()
        self.assertTrue(sign_cdimage(config, sign_path))
        self.assertLogEqual([])
        expected_command = [
            "gpg", "--options", gpgconf,
            "--no-default-keyring",
            "--secret-keyring", secring,
            "--keyring", pubring,
            "--trustdb-name", trustdb,
            "--no-options", "--batch", "--no-tty",
            "--armour", "--detach-sign",
            "--digest-algo", "SHA512",
            "-u", "01234567",
        ]
        mock_check_call.assert_called_once_with(
            expected_command, stdin=mock.ANY, stdout=mock.ANY)
        call = mock_check_call.call_args
        self.assertEqual(sign_path, call[1]["stdin"].name)
        self.assertEqual("%s.gpg" % sign_path, call[1]["stdout"].name)

    @mock.patch("subprocess.check_call")
    def test_sign_cdimage_subprocess_error(self, mock_check_call):
        mock_check_call.side_effect = subprocess.CalledProcessError(1, "")
        config = Config(read=False)
        config["GNUPG_DIR"] = self.use_temp_dir()
        config["SIGNING_KEYID"] = "01234567"
        gpgconf, secring, pubring, trustdb = _gnupg_files(config)
        sign_path = os.path.join(self.temp_dir, "to-sign")
        for path in gpgconf, secring, pubring, trustdb, sign_path:
            touch(path)
        touch("%s.gpg" % sign_path)
        self.capture_logging()
        self.assertRaises(
            subprocess.CalledProcessError, sign_cdimage, config, sign_path)
        self.assertLogEqual([])
        self.assertFalse(os.path.exists("%s.gpg" % sign_path))
