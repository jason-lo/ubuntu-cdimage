#! /usr/bin/python

# Copyright (C) 2013 Canonical Ltd.
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

"""Unit tests for cdimage.build."""

from __future__ import print_function

from functools import partial
import gzip
import optparse
import os
import signal
import stat
import subprocess
import sys
from textwrap import dedent
import time
import traceback

try:
    from unittest import mock
except ImportError:
    import mock

from cdimage import osextras
from cdimage.build import (
    UnknownLocale,
    _anonftpsync_config_path,
    _anonftpsync_options,
    _debootstrap_script,
    anonftpsync,
    build_britney,
    build_image_set,
    build_image_set_locked,
    build_livecd_base,
    build_ubuntu_defaults_locale,
    configure_for_project,
    configure_splash,
    extract_debootstrap,
    fix_permissions,
    lock_build_image_set,
    log_marker,
    notify_failure,
    open_log,
    run_debian_cd,
    update_local_indices,
    sync_local_mirror,
    want_live_builds,
)
from cdimage.config import Config, Touch
from cdimage.log import logger
from cdimage.mail import text_file_type
from cdimage.tests.helpers import TestCase, mkfile, touch

__metaclass__ = type


class TestUpdateLocalIndices(TestCase):
    def setUp(self):
        super(TestUpdateLocalIndices, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["DIST"] = "trusty"
        self.config["CPUARCHES"] = "i386"
        self.packages = os.path.join(self.temp_dir, "local", "packages")
        self.database = os.path.join(self.temp_dir, "local", "database")
        self.dists = os.path.join(self.database, "dists")
        self.indices = os.path.join(self.database, "indices")
        self.pool = os.path.join(self.packages, "pool", "local")

    @mock.patch("subprocess.call")
    def test_no_local_packages(self, mock_call):
        self.assertFalse(os.path.exists(self.packages))
        mock_call.side_effect = Exception(
            "subprocess.call called when it should not have been")
        update_local_indices(self.config)

    def test_lists_and_overrides(self):
        fake_dir = os.path.join(self.pool, "f", "fake")
        self.make_deb(
            os.path.join(fake_dir, "fake_1_i386.deb"), "misc", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake_1_unknown.deb"), "misc", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-nf_1_all.deb"),
            "non-free/admin", "extra")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb_1_i386.udeb"),
            "debian-installer", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb_1_unknown.udeb"),
            "debian-installer", "optional")
        self.make_deb(
            os.path.join(fake_dir, "fake-udeb-indep_1_all.udeb"),
            "debian-installer", "extra")
        touch(os.path.join(fake_dir, "random-file"))

        with mock.patch("subprocess.call", return_value=0) as mock_call:
            update_local_indices(self.config)

            expected_command = [
                "apt-ftparchive", "generate", "apt-ftparchive.conf"]
            mock_call.assert_called_once_with(
                expected_command, cwd=self.packages)

        self.assertCountEqual([
            "trusty_local_binary-i386.list",
            "trusty_local_debian-installer_binary-i386.list",
        ], os.listdir(self.dists))
        with open(os.path.join(
                self.dists, "trusty_local_binary-i386.list")) as f:
            self.assertCountEqual([
                "pool/local/f/fake/fake_1_i386.deb",
                "pool/local/f/fake/fake-nf_1_all.deb",
            ], f.read().splitlines())
        with open(os.path.join(
                self.dists,
                "trusty_local_debian-installer_binary-i386.list")) as f:
            self.assertCountEqual([
                "pool/local/f/fake/fake-udeb_1_i386.udeb",
                "pool/local/f/fake/fake-udeb-indep_1_all.udeb",
            ], f.read().splitlines())

        self.assertCountEqual([
            "override.trusty.local.i386",
            "override.trusty.local.debian-installer.i386",
        ], os.listdir(self.indices))
        with open(os.path.join(
                self.indices, "override.trusty.local.i386")) as f:
            self.assertCountEqual([
                "fake\toptional\tlocal/misc",
                "fake-nf\textra\tlocal/admin",
            ], f.read().splitlines())
        with open(os.path.join(
                self.indices,
                "override.trusty.local.debian-installer.i386")) as f:
            self.assertCountEqual([
                "fake-udeb\toptional\tlocal/debian-installer",
                "fake-udeb-indep\textra\tlocal/debian-installer",
            ], f.read().splitlines())

        self.assertTrue(os.path.exists(os.path.join(
            self.packages, "dists", "trusty", "local", "binary-i386")))
        self.assertTrue(os.path.exists(os.path.join(
            self.packages, "dists", "trusty", "local", "debian-installer",
            "binary-i386")))


class TestBuildUbuntuDefaultsLocale(TestCase):
    def setUp(self):
        super(TestBuildUbuntuDefaultsLocale, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["PROJECT"] = "ubuntu"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.config["CDIMAGE_LIVE"] = "1"
        with mkfile(os.path.join(
                self.temp_dir, "production", "livefs-builders")) as f:
            print("* * * mock-builder", file=f)

    def test_requires_chinese_locale(self):
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "en"
        self.assertRaises(
            UnknownLocale, build_ubuntu_defaults_locale, self.config)

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.osextras.fetch")
    def test_modern(self, mock_fetch, mock_check_call):
        def fetch_side_effect(config, source, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in ("iso", "manifest", "manifest-remove", "size"):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["DIST"] = "precise"
        self.config["ARCHES"] = "i386"
        build_ubuntu_defaults_locale(self.config)
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-zh_CN", "precise", "daily-live",
            "live")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "precise-desktop-i386.iso",
            "precise-desktop-i386.list",
            "precise-desktop-i386.manifest",
            "precise-desktop-i386.manifest-remove",
            "precise-desktop-i386.size",
        ], os.listdir(output_dir))
        mock_check_call.assert_called_once_with([
            os.path.join(self.temp_dir, "debian-cd", "tools", "pi-makelist"),
            os.path.join(output_dir, "precise-desktop-i386.iso"),
        ], stdout=mock.ANY)


class TestBuildLiveCDBase(TestCase):
    def setUp(self):
        super(TestBuildLiveCDBase, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.config["CDIMAGE_LIVE"] = "1"
        with mkfile(os.path.join(
                self.temp_dir, "production", "livefs-builders")) as f:
            print("* * * mock-builder", file=f)
        mock_gmtime = mock.patch("time.gmtime", return_value=time.gmtime(0))
        mock_gmtime.start()
        self.addCleanup(mock_gmtime.stop)
        self.epoch_date = "Thu Jan  1 00:00:00 UTC 1970"

    @mock.patch("cdimage.sign.sign_cdimage")
    @mock.patch("cdimage.osextras.fetch")
    def test_livecd_base(self, mock_fetch, mock_sign):
        def fetch_side_effect(config, source, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in ("manifest", "squashfs"):
                touch(target)
            else:
                raise osextras.FetchError

        def sign_side_effect(config, target):
            tail = os.path.basename(target).split(".", 1)[1]
            if tail in ("manifest", "squashfs"):
                touch(target + ".gpg")
            else:
                return False

        mock_fetch.side_effect = fetch_side_effect
        mock_sign.side_effect = sign_side_effect
        self.config["PROJECT"] = "livecd-base"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "livecd-base"
        self.config["ARCHES"] = "i386"
        self.capture_logging()
        build_livecd_base(self.config)
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
        ])
        live_dir = os.path.join(
            self.temp_dir, "scratch", "livecd-base", "trusty", "livecd-base",
            "live")
        self.assertTrue(os.path.isdir(live_dir))
        self.assertCountEqual(
            ["i386.manifest", "i386.squashfs", "i386.squashfs.gpg"],
            os.listdir(live_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_base(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".manifest") or
                    target.endswith(".rootfs.tar.gz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["PROJECT"] = "ubuntu-base"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "i386"
        self.capture_logging()
        build_livecd_base(self.config)
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-base", "trusty", "daily",
            "debian-cd", "i386")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "trusty-base-i386.manifest",
            "trusty-base-i386.raw",
            "trusty-base-i386.type",
        ], os.listdir(output_dir))
        with open(os.path.join(output_dir, "trusty-base-i386.type")) as f:
            self.assertEqual("tar archive\n", f.read())

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_server_preinstalled_raspi2(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".manifest") or
                    target.endswith(".disk1.img.xz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_PREINSTALLED"] = "1"
        self.config["PROJECT"] = "ubuntu-server"
        self.config["DIST"] = "xenial"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.config["ARCHES"] = "armhf+raspi2"
        self.capture_logging()
        build_livecd_base(self.config)
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-server", "xenial",
            "daily-preinstalled", "debian-cd", "armhf+raspi2")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "xenial-preinstalled-server-armhf+raspi2.manifest",
            "xenial-preinstalled-server-armhf+raspi2.raw",
            "xenial-preinstalled-server-armhf+raspi2.type",
        ], os.listdir(output_dir))

    @mock.patch("cdimage.osextras.fetch")
    def test_ubuntu_core_raspi3(self, mock_fetch):
        def fetch_side_effect(config, source, target):
            if (target.endswith(".model-assertion") or
                    target.endswith(".manifest") or
                    target.endswith(".img.xz")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_LIVE"] = "1"
        self.config["PROJECT"] = "ubuntu-core"
        self.config["DIST"] = "xenial"
        self.config["IMAGE_TYPE"] = "daily-live"
        self.config["ARCHES"] = "armhf+raspi3"
        self.capture_logging()
        build_livecd_base(self.config)
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu-core", "xenial",
            "daily-live", "live")
        self.assertTrue(os.path.isdir(output_dir))
        self.assertCountEqual([
            "armhf+raspi3.img.xz",
            "armhf+raspi3.model-assertion",
            "armhf+raspi3.manifest",
        ], os.listdir(output_dir))

    @mock.patch("cdimage.osextras.fetch")
    def _perform_ubuntu_touch_testing(self, project, mock_fetch):
        '''Convenience function for testing ubuntu-touch* builds.'''
        def fetch_side_effect(config, source, target):
            if (target.endswith(".manifest") or
                    target.endswith(".rootfs.tar.gz") or
                    target.endswith(".custom.tar.gz") or
                    target.endswith(".img")):
                touch(target)
            else:
                raise osextras.FetchError

        mock_fetch.side_effect = fetch_side_effect
        self.config["CDIMAGE_PREINSTALLED"] = "1"
        self.config["PROJECT"] = project
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily-preinstalled"
        self.config["ARCHES"] = "armhf"
        self.capture_logging()
        build_livecd_base(self.config)
        self.assertLogEqual([
            "===== Downloading live filesystem images =====",
            self.epoch_date,
            "===== Copying images to debian-cd output directory =====",
            self.epoch_date,
        ])
        output_dir = os.path.join(
            self.temp_dir, "scratch", project, "trusty",
            "daily-preinstalled", "debian-cd", "armhf")
        self.assertTrue(os.path.isdir(output_dir))
        touch_files = ["trusty-preinstalled-boot-%s+%s.img" % (
            touch_target.ubuntu_arch, touch_target.subarch)
            for touch_target in Touch.list_targets_by_ubuntu_arch("armhf")]
        touch_files.extend(["trusty-preinstalled-recovery-%s+%s.img" % (
            touch_target.android_arch, touch_target.subarch)
            for touch_target in Touch.list_targets_by_ubuntu_arch("armhf")])
        touch_files.extend(["trusty-preinstalled-system-%s+%s.img" % (
            touch_target.android_arch, touch_target.subarch)
            for touch_target in Touch.list_targets_by_ubuntu_arch("armhf")])
        touch_files.extend([
            "trusty-preinstalled-touch-armhf.manifest",
            "trusty-preinstalled-touch-armhf.raw",
            "trusty-preinstalled-touch-armhf.type",
            "trusty-preinstalled-touch-armhf.tar.gz",
            "trusty-preinstalled-touch-armhf.custom.tar.gz",
            ])
        self.assertCountEqual(touch_files, os.listdir(output_dir))
        with open(os.path.join(
            output_dir, "trusty-preinstalled-touch-armhf.type")
        ) as f:
            self.assertEqual("tar archive\n", f.read())
        for touch_target in Touch.list_targets_by_ubuntu_arch("armhf"):
            system_img = "trusty-preinstalled-system-%s+%s.img" % (
                touch_target.android_arch, touch_target.subarch)
            recovery_img = "trusty-preinstalled-recovery-%s+%s.img" % (
                touch_target.android_arch, touch_target.subarch)
            self.assertTrue(os.path.exists(
                os.path.join(output_dir, system_img)))
            self.assertTrue(os.path.exists(
                os.path.join(output_dir, recovery_img)))

    def test_ubuntu_touch(self):
        self._perform_ubuntu_touch_testing("ubuntu-touch")


class TestExtractDebootstrap(TestCase):
    def setUp(self):
        super(TestExtractDebootstrap, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()

    def test_debootstrap_script(self):
        for series, script in (
            ("precise", "usr/share/debootstrap/scripts/precise"),
            ("bionic", "usr/share/debootstrap/scripts/bionic"),
        ):
            self.config["DIST"] = series
            self.assertEqual(script, _debootstrap_script(self.config))

    def test_extract_debootstrap(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64+mac"
        mirror_dir = os.path.join(self.temp_dir, "ftp")
        packages_path = os.path.join(
            mirror_dir, "dists", "trusty", "main", "debian-installer",
            "binary-amd64", "Packages.gz")
        udeb_path = os.path.join(
            mirror_dir, "pool", "main", "d", "debootstrap",
            "debootstrap-udeb_1_all.udeb")
        self.make_deb(
            udeb_path, "debian-installer", "extra",
            files={"/usr/share/debootstrap/scripts/trusty": b"sentinel"})
        os.makedirs(os.path.dirname(packages_path))
        with gzip.GzipFile(packages_path, "wb") as packages:
            ftparchive = subprocess.Popen(
                ["apt-ftparchive", "packages", "pool"],
                stdout=subprocess.PIPE, cwd=mirror_dir)
            data, _ = ftparchive.communicate()
            packages.write(data)
            self.assertEqual(0, ftparchive.returncode)
        extract_debootstrap(self.config)
        output_path = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "trusty", "daily",
            "debootstrap", "trusty-amd64+mac")
        self.assertTrue(os.path.exists(output_path))
        with open(output_path, "rb") as output:
            self.assertEqual(b"sentinel", output.read())


class TestBuildImageSet(TestCase):
    def setUp(self):
        super(TestBuildImageSet, self).setUp()
        self.config = Config(read=False)
        self.config.root = self.use_temp_dir()
        self.expected_sync_lock = os.path.join(
            self.temp_dir, "etc", ".lock-archive-sync")
        mock_gmtime = mock.patch("time.gmtime", return_value=time.gmtime(0))
        mock_gmtime.start()
        self.addCleanup(mock_gmtime.stop)
        self.epoch_date = "Thu Jan  1 00:00:00 UTC 1970"

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.osextras.unlink_force")
    def test_lock_build_image_set(self, mock_unlink_force, mock_check_call):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        expected_lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-trusty-daily")
        self.assertFalse(os.path.exists(expected_lock_path))
        with lock_build_image_set(self.config):
            mock_check_call.assert_called_once_with([
                "lockfile", "-l", "7200", "-r", "0", expected_lock_path])
            self.assertEqual(0, mock_unlink_force.call_count)
        mock_unlink_force.assert_called_once_with(expected_lock_path)

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.osextras.unlink_force")
    def test_lock_build_image_set_chinese(self, mock_unlink_force,
                                          mock_check_call):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        expected_lock_path = os.path.join(
            self.temp_dir, "etc",
            ".lock-build-image-set-ubuntu-chinese-edition-trusty-daily")
        self.assertFalse(os.path.exists(expected_lock_path))
        with lock_build_image_set(self.config):
            mock_check_call.assert_called_once_with([
                "lockfile", "-l", "7200", "-r", "0", expected_lock_path])
            self.assertEqual(0, mock_unlink_force.call_count)
        mock_unlink_force.assert_called_once_with(expected_lock_path)

    def test_configure_onlyfree_unsupported(self):
        for project, series, onlyfree, unsupported in (
            ("ubuntu", "trusty", False, False),
            ("gobuntu", "hardy", True, False),
            ("edubuntu", "precise", False, True),
            ("xubuntu", "precise", False, True),
            ("kubuntu", "precise", False, False),
            ("kubuntu", "trusty", False, True),
            ("kubuntu-active", "trusty", False, True),
            ("ubuntustudio", "trusty", False, True),
            ("mythbuntu", "trusty", False, True),
            ("lubuntu", "trusty", False, True),
            ("ubuntukylin", "trusty", False, True),
            ("ubuntu-gnome", "trusty", False, True),
            ("ubuntu-budgie", "bionic", False, True),
            ("ubuntu-mate", "xenial", False, True),
        ):
            config = Config(read=False)
            config["PROJECT"] = project
            config["DIST"] = series
            configure_for_project(config)
            if onlyfree:
                self.assertEqual("1", config["CDIMAGE_ONLYFREE"])
            else:
                self.assertNotIn("CDIMAGE_ONLYFREE", config)
            if unsupported:
                self.assertEqual("1", config["CDIMAGE_UNSUPPORTED"])
            else:
                self.assertNotIn("CDIMAGE_UNSUPPORTED", config)

    def test_configure_install_base(self):
        config = Config(read=False)
        configure_for_project(config)
        self.assertNotIn("CDIMAGE_INSTALL_BASE", config)

        config = Config(read=False)
        config["CDIMAGE_INSTALL"] = "1"
        configure_for_project(config)
        self.assertEqual("1", config["CDIMAGE_INSTALL_BASE"])

    @mock.patch("os.open")
    def test_open_log_debug(self, mock_open):
        self.config["DEBUG"] = "1"
        self.assertIsNone(open_log(self.config))
        self.assertEqual(0, mock_open.call_count)

    def test_open_log_writes_log(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130224"
        pid = os.fork()
        if pid == 0:  # child
            log_path = open_log(self.config)
            print("Log path: %s" % log_path)
            print("VERBOSE: %s" % self.config["VERBOSE"])
            sys.stdout.flush()
            print("Standard error", file=sys.stderr)
            sys.stderr.flush()
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            expected_log_path = os.path.join(
                self.temp_dir, "log", "ubuntu", "trusty", "daily-20130224.log")
            self.assertTrue(os.path.exists(expected_log_path))
            with open(expected_log_path) as log:
                self.assertEqual([
                    "Log path: %s" % expected_log_path,
                    "VERBOSE: 3",
                    "Standard error",
                ], log.read().splitlines())

    def test_open_log_chinese(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.config["CDIMAGE_DATE"] = "20130224"
        pid = os.fork()
        if pid == 0:  # child
            log_path = open_log(self.config)
            print("Log path: %s" % log_path)
            print("VERBOSE: %s" % self.config["VERBOSE"])
            sys.stdout.flush()
            print("Standard error", file=sys.stderr)
            sys.stderr.flush()
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            expected_log_path = os.path.join(
                self.temp_dir, "log", "ubuntu-chinese-edition", "trusty",
                "daily-20130224.log")
            self.assertTrue(os.path.exists(expected_log_path))
            with open(expected_log_path) as log:
                self.assertEqual([
                    "Log path: %s" % expected_log_path,
                    "VERBOSE: 3",
                    "Standard error",
                ], log.read().splitlines())

    def test_log_marker(self):
        self.capture_logging()
        log_marker("Testing")
        self.assertLogEqual(["===== Testing =====", self.epoch_date])

    def test_want_live_builds_no_options(self):
        self.assertFalse(want_live_builds(None))

    def test_want_live_builds_irrelevant_options(self):
        self.assertFalse(want_live_builds(optparse.Values()))

    def test_want_live_builds_option_false(self):
        options = optparse.Values({"live": False})
        self.assertFalse(want_live_builds(options))

    def test_want_live_builds_option_true(self):
        options = optparse.Values({"live": True})
        self.assertTrue(want_live_builds(options))

    def test_anonftpsync_config_path(self):
        self.assertIsNone(_anonftpsync_config_path(self.config))
        path = os.path.join(self.temp_dir, "etc", "anonftpsync")
        touch(path)
        self.assertEqual(path, _anonftpsync_config_path(self.config))
        path = os.path.join(self.temp_dir, "production", "anonftpsync")
        touch(path)
        self.assertEqual(path, _anonftpsync_config_path(self.config))
        self.config["ANONFTPSYNC_CONF"] = "sentinel"
        self.assertEqual("sentinel", _anonftpsync_config_path(self.config))

    def test_anonftpsync_options(self):
        for key in list(os.environ.keys()):
            if key.startswith("RSYNC_"):
                os.environ.pop(key, None)
        self.assertRaisesRegex(
            Exception, "RSYNC_SRC not configured.*",
            _anonftpsync_options, self.config)
        path = os.path.join(self.temp_dir, "etc", "anonftpsync")
        with mkfile(path) as anonftpsync_config:
            print("RSYNC_PASSWORD='secret'", file=anonftpsync_config)
        self.assertRaisesRegex(
            Exception, "RSYNC_SRC not configured.*",
            _anonftpsync_options, self.config)
        with open(path, "a") as anonftpsync_config:
            print(
                "RSYNC_SRC=rsync.example.org::ubuntu", file=anonftpsync_config)
        self.assertEqual({
            "RSYNC_PASSWORD": "secret",
            "RSYNC_SRC": "rsync.example.org::ubuntu",
        }, _anonftpsync_options(self.config))
        self.config["RSYNC_RSH"] = "ssh"
        self.assertEqual({
            "RSYNC_PASSWORD": "secret",
            "RSYNC_RSH": "ssh",
            "RSYNC_SRC": "rsync.example.org::ubuntu",
        }, _anonftpsync_options(self.config))

    @mock.patch("socket.getfqdn", return_value="cdimage.example.org")
    @mock.patch("subprocess.call")
    def test_anonftpsync(self, mock_call, *args):
        def call_side_effect(command, *args, **kwargs):
            if command[0] == "lockfile":
                return 1
            else:
                return 0

        mock_call.side_effect = call_side_effect
        path = os.path.join(self.temp_dir, "etc", "anonftpsync")
        with mkfile(path) as anonftpsync_config:
            print("RSYNC_PASSWORD=secret", file=anonftpsync_config)
            print(
                "RSYNC_SRC=rsync.example.org::ubuntu", file=anonftpsync_config)
            print("RSYNC_EXCLUDE='--exclude foo'", file=anonftpsync_config)
        target = os.path.join(self.temp_dir, "ftp")
        touch(os.path.join(target, "dir", "file"))
        os.symlink("file", os.path.join(target, "dir", "valid-link"))
        os.symlink("nonexistent", os.path.join(target, "dir", "broken-link"))
        lock = os.path.join(
            target, "Archive-Update-in-Progress-cdimage.example.org")
        trace = os.path.join(target, "project", "trace", "cdimage.example.org")
        log = os.path.join(self.temp_dir, "log", "rsync.log")
        anonftpsync(self.config)
        self.assertEqual(5, mock_call.call_count)
        expected_rsync_base = [
            "rsync", "--recursive", "--links", "--hard-links", "--times",
            "--verbose", "--stats", "--chmod=Dg+s,g+rwX",
            "--exclude", os.path.basename(lock),
            "--exclude", "project/trace/cdimage.example.org",
        ]
        mock_call.assert_has_calls([
            mock.call(["lockfile", "-!", "-l", "43200", "-r", "0", lock]),
            mock.call(expected_rsync_base + [
                "--exclude", "Packages*", "--exclude", "Sources*",
                "--exclude", "Release*", "--exclude", "InRelease",
                "--include", "i18n/by-hash/**", "--exclude", "i18n/*",
                "--exclude", "foo",
                "rsync.example.org::ubuntu/", "%s/" % target,
            ], stdout=mock.ANY, stderr=subprocess.STDOUT, env=mock.ANY),
            mock.call(expected_rsync_base + [
                "--delay-updates", "--delete", "--delete-after",
                "--exclude", "foo",
                "rsync.example.org::ubuntu/", "%s/" % target,
            ], stdout=mock.ANY, stderr=subprocess.STDOUT, env=mock.ANY),
            mock.call(["date", "-u"], stdout=mock.ANY),
            mock.call(["savelog", log], stdout=mock.ANY, stderr=mock.ANY),
        ])
        self.assertEqual(
            "secret", mock_call.call_args_list[1][1]["env"]["RSYNC_PASSWORD"])
        self.assertEqual(
            "secret", mock_call.call_args_list[2][1]["env"]["RSYNC_PASSWORD"])
        self.assertFalse(os.path.exists(lock))
        self.assertTrue(os.path.exists(trace))
        self.assertTrue(os.path.exists(log))
        self.assertTrue(os.path.exists(os.path.join(target, "dir", "file")))
        self.assertTrue(
            os.path.lexists(os.path.join(target, "dir", "valid-link")))
        self.assertFalse(
            os.path.lexists(os.path.join(target, "dir", "broken-link")))

    def check_call_make_sync_lock(self, mock_check_call, *args, **kwargs):
        if mock_check_call.call_count == 1:
            self.assertEqual("lockfile", args[0][0])
            touch(self.expected_sync_lock)

    def anonftpsync_sync_lock_exists(self, *args, **kwargs):
        self.assertTrue(os.path.exists(self.expected_sync_lock))

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.build.anonftpsync")
    def test_config_nosync(self, mock_anonftpsync, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["CDIMAGE_NOSYNC"] = "1"
        self.capture_logging()
        sync_local_mirror(self.config, 0)
        self.assertLogEqual([])
        self.assertEqual(0, mock_check_call.call_count)
        self.assertEqual(0, mock_anonftpsync.call_count)

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.build.anonftpsync")
    def test_sync(self, mock_anonftpsync, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        mock_check_call.side_effect = partial(
            self.check_call_make_sync_lock, mock_check_call)
        mock_anonftpsync.side_effect = self.anonftpsync_sync_lock_exists
        self.capture_logging()
        sync_local_mirror(self.config, 0)
        self.assertLogEqual([
            "===== Syncing Ubuntu mirror =====",
            self.epoch_date,
        ])
        mock_check_call.assert_called_once_with(
            ["lockfile", "-r", "4", self.expected_sync_lock])
        mock_anonftpsync.assert_called_once_with(self.config)
        self.assertFalse(os.path.exists(self.expected_sync_lock))

    @mock.patch("subprocess.check_call")
    @mock.patch("cdimage.build.anonftpsync")
    def test_sync_lock_failure(self, mock_anonftpsync, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        mock_check_call.side_effect = subprocess.CalledProcessError(1, "")
        self.capture_logging()
        self.assertRaises(
            subprocess.CalledProcessError, sync_local_mirror, self.config, 0)
        self.assertLogEqual([
            "===== Syncing Ubuntu mirror =====",
            self.epoch_date,
            "Couldn't acquire archive sync lock!"
        ])
        mock_check_call.assert_called_once_with(
            ["lockfile", "-r", "4", self.expected_sync_lock])
        self.assertEqual(0, mock_anonftpsync.call_count)
        self.assertFalse(os.path.exists(self.expected_sync_lock))

    @mock.patch("subprocess.check_call")
    def test_parallel(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        mock_check_call.side_effect = partial(
            self.check_call_make_sync_lock, mock_check_call)
        self.capture_logging()
        sync_local_mirror(self.config, 1)
        self.assertLogEqual([
            "===== Parallel build; waiting for Ubuntu mirror to sync =====",
            self.epoch_date,
        ])
        mock_check_call.assert_called_once_with(
            ["lockfile", "-8", "-r", "450", self.expected_sync_lock])
        self.assertFalse(os.path.exists(self.expected_sync_lock))

    @mock.patch("subprocess.check_call")
    def test_parallel_lock_failure(self, mock_check_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        mock_check_call.side_effect = subprocess.CalledProcessError(1, "")
        self.capture_logging()
        self.assertRaises(
            subprocess.CalledProcessError, sync_local_mirror, self.config, 1)
        self.assertLogEqual([
            "===== Parallel build; waiting for Ubuntu mirror to sync =====",
            self.epoch_date,
            "Timed out waiting for archive sync lock!"
        ])
        mock_check_call.assert_called_once_with(
            ["lockfile", "-8", "-r", "450", self.expected_sync_lock])
        self.assertFalse(os.path.exists(self.expected_sync_lock))

    @mock.patch("subprocess.check_call")
    def test_build_britney_no_makefile(self, mock_check_call):
        self.capture_logging()
        build_britney(self.config)
        self.assertLogEqual([])
        self.assertEqual(0, mock_check_call.call_count)

    @mock.patch("subprocess.check_call")
    def test_build_britney_with_makefile(self, mock_check_call):
        path = os.path.join(self.temp_dir, "britney", "update_out", "Makefile")
        touch(path)
        self.capture_logging()
        build_britney(self.config)
        self.assertLogEqual(["===== Building britney =====", self.epoch_date])
        mock_check_call.assert_called_once_with(
            ["make", "-C", os.path.dirname(path)])

    def test_configure_splash(self):
        data_dir = os.path.join(self.temp_dir, "debian-cd", "data", "trusty")
        for key, extension in (
            ("SPLASHRLE", "rle"),
            ("GFXSPLASH", "pcx"),
            ("SPLASHPNG", "png"),
        ):
            for project_specific in True, False:
                config = Config(read=False)
                config.root = self.temp_dir
                config["PROJECT"] = "kubuntu"
                config["DIST"] = "trusty"
                path = os.path.join(
                    data_dir, "%s.%s" % (
                        "kubuntu" if project_specific else "splash",
                        extension))
                touch(path)
                configure_splash(config)
                self.assertEqual(path, config[key])
                osextras.unlink_force(path)

    @mock.patch("subprocess.call", return_value=0)
    def test_run_debian_cd(self, mock_call):
        self.config["CAPPROJECT"] = "Ubuntu"
        self.capture_logging()
        run_debian_cd(self.config)
        self.assertLogEqual([
            "===== Building Ubuntu daily CDs =====",
            self.epoch_date,
        ])
        expected_cwd = os.path.join(self.temp_dir, "debian-cd")
        mock_call.assert_called_once_with(
            ["./build_all.sh"], cwd=expected_cwd, env=mock.ANY)

    @mock.patch("subprocess.call", return_value=0)
    def test_run_debian_cd_reexports_config(self, mock_call):
        # We need to re-export configuration to debian-cd even if we didn't
        # get it in our environment, since debian-cd won't read etc/config
        # for itself.
        with mkfile(os.path.join(self.temp_dir, "etc", "config")) as f:
            print(dedent("""\
                #! /bin/sh
                PROJECT=ubuntu
                CAPPROJECT=Ubuntu
                ARCHES="amd64 powerpc"
                """), file=f)
        os.environ["CDIMAGE_ROOT"] = self.temp_dir
        config = Config()
        self.capture_logging()
        run_debian_cd(config)
        self.assertLogEqual([
            "===== Building Ubuntu daily CDs =====",
            self.epoch_date,
        ])
        expected_cwd = os.path.join(self.temp_dir, "debian-cd")
        mock_call.assert_called_once_with(
            ["./build_all.sh"], cwd=expected_cwd, env=mock.ANY)
        self.assertEqual(
            "amd64 powerpc", mock_call.call_args[1]["env"]["ARCHES"])

    def test_fix_permissions(self):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        scratch_dir = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "trusty", "daily")
        subdir = os.path.join(scratch_dir, "x")
        dir_one = os.path.join(subdir, "1")
        file_two = os.path.join(subdir, "2")
        file_three = os.path.join(subdir, "3")
        osextras.ensuredir(dir_one)
        touch(file_two)
        touch(file_three)
        for path, perm in (
            (scratch_dir, 0o755),
            (subdir, 0o2775),
            (dir_one, 0o700),
            (file_two, 0o664),
            (file_three, 0o600),
        ):
            os.chmod(path, perm)
        fix_permissions(self.config)
        for path, perm in (
            (scratch_dir, 0o2775),
            (subdir, 0o2775),
            (dir_one, 0o2770),
            (file_two, 0o664),
            (file_three, 0o660),
        ):
            self.assertEqual(perm, stat.S_IMODE(os.stat(path).st_mode))

    @mock.patch("cdimage.build.get_notify_addresses")
    def test_notify_failure_debug(self, mock_notify_addresses):
        self.config["DEBUG"] = "1"
        notify_failure(self.config, None)
        self.assertEqual(0, mock_notify_addresses.call_count)

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_no_recipients(self, mock_send_mail):
        self.config["DIST"] = "trusty"
        notify_failure(self.config, None)
        self.assertEqual(0, mock_send_mail.call_count)

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_no_log(self, mock_send_mail):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        notify_failure(self.config, None)
        mock_send_mail.assert_called_once_with(
            "CD image ubuntu/trusty/daily failed to build on 20130225",
            "build-image-set", ["foo@example.org"], "")

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_log(self, mock_send_mail):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        log_path = os.path.join(self.temp_dir, "log")
        with mkfile(log_path) as log:
            print("Log", file=log)
        notify_failure(self.config, log_path)
        mock_send_mail.assert_called_once_with(
            "CD image ubuntu/trusty/daily failed to build on 20130225",
            "build-image-set", ["foo@example.org"], mock.ANY)
        self.assertEqual(log_path, mock_send_mail.call_args[0][3].name)

    @mock.patch("cdimage.build.send_mail")
    def test_notify_failure_chinese(self, mock_send_mail):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["UBUNTU_DEFAULTS_LOCALE"] = "zh_CN"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path) as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        log_path = os.path.join(self.temp_dir, "log")
        with mkfile(log_path) as log:
            print("Log", file=log)
        notify_failure(self.config, log_path)
        mock_send_mail.assert_called_once_with(
            "CD image ubuntu-chinese-edition/trusty/daily failed to build on "
            "20130225",
            "build-image-set", ["foo@example.org"], mock.ANY)
        self.assertEqual(log_path, mock_send_mail.call_args[0][3].name)

    def send_mail_to_file(self, path, subject, generator, recipients, body,
                          dry_run=False):
        with mkfile(path) as f:
            print("To: %s" % ", ".join(recipients), file=f)
            print("Subject: %s" % subject, file=f)
            print("X-Generated-By: %s" % generator, file=f)
            print("", file=f)
            if isinstance(body, text_file_type):
                for line in body:
                    print(line.rstrip("\n"), file=f)
            else:
                for line in body.splitlines():
                    print(line, file=f)

    @mock.patch("time.strftime", return_value="20130225")
    @mock.patch("cdimage.build.tracker_set_rebuild_status")
    @mock.patch("cdimage.build.sync_local_mirror")
    @mock.patch("cdimage.build.send_mail")
    def test_build_image_set_locked_notifies_on_failure(
            self, mock_send_mail, mock_sync_local_mirror, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["CDIMAGE_DATE"] = "20130225"
        path = os.path.join(self.temp_dir, "production", "notify-addresses")
        with mkfile(path, "w") as notify_addresses:
            print("ALL\tfoo@example.org", file=notify_addresses)
        log_path = os.path.join(
            self.temp_dir, "log", "ubuntu", "trusty", "daily-20130225.log")
        os.makedirs(os.path.join(self.temp_dir, "etc"))

        def force_failure(*args):
            logger.error("Forced image build failure")
            raise Exception("Artificial exception")

        mock_sync_local_mirror.side_effect = force_failure
        mock_send_mail.side_effect = partial(
            self.send_mail_to_file, os.path.join(self.temp_dir, "mail"))
        pid = os.fork()
        if pid == 0:  # child
            original_stderr = os.dup(sys.stderr.fileno())
            try:
                self.assertFalse(build_image_set_locked(self.config, None, 0))
            except AssertionError:
                stderr = os.fdopen(original_stderr, "w", 1)
                try:
                    with open(log_path) as log:
                        stderr.write(log.read())
                except IOError:
                    pass
                traceback.print_exc(file=stderr)
                stderr.flush()
                os._exit(1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            with open(log_path) as log:
                self.assertEqual(
                    "Forced image build failure\n", log.readline())
                self.assertEqual(
                    "Traceback (most recent call last):\n", log.readline())
                self.assertIn("Exception: Artificial exception", log.read())

    @mock.patch("subprocess.call", return_value=0)
    @mock.patch("cdimage.build.tracker_set_rebuild_status")
    @mock.patch("cdimage.build.anonftpsync")
    @mock.patch("cdimage.build.extract_debootstrap")
    @mock.patch("cdimage.germinate.GerminateOutput.write_tasks")
    @mock.patch("cdimage.germinate.GerminateOutput.update_tasks")
    @mock.patch("cdimage.tree.DailyTreePublisher.publish")
    @mock.patch("cdimage.tree.DailyTreePublisher.purge")
    def test_build_image_set_locked(
            self, mock_purge, mock_publish, mock_update_tasks,
            mock_write_tasks, mock_extract_debootstrap, mock_anonftpsync,
            mock_tracker_set_rebuild_status, mock_call):
        self.config["PROJECT"] = "ubuntu"
        self.config["CAPPROJECT"] = "Ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        self.config["ARCHES"] = "amd64 i386"
        self.config["CPUARCHES"] = "amd64 i386"

        britney_makefile = os.path.join(
            self.temp_dir, "britney", "update_out", "Makefile")
        touch(britney_makefile)
        os.makedirs(os.path.join(self.temp_dir, "etc"))
        germinate_path = os.path.join(
            self.temp_dir, "germinate", "bin", "germinate")
        touch(germinate_path)
        os.chmod(germinate_path, 0o755)
        germinate_output = os.path.join(
            self.temp_dir, "scratch", "ubuntu", "trusty", "daily", "germinate")
        log_dir = os.path.join(self.temp_dir, "log", "ubuntu", "trusty")

        def side_effect(command, *args, **kwargs):
            if command[0] == germinate_path:
                for arch in self.config.arches:
                    touch(os.path.join(germinate_output, arch, "structure"))

        mock_call.side_effect = side_effect

        pid = os.fork()
        if pid == 0:  # child
            original_stderr = os.dup(sys.stderr.fileno())
            try:
                self.assertTrue(build_image_set_locked(self.config, None, 0))
                date = self.config["CDIMAGE_DATE"]
                debian_cd_dir = os.path.join(self.temp_dir, "debian-cd")

                def germinate_command(arch):
                    return mock.call([
                        germinate_path,
                        "--seed-source", mock.ANY,
                        "--mirror", "file://%s/" % germinate_output,
                        "--seed-dist", "ubuntu.trusty",
                        "--dist", "trusty,trusty-security,trusty-updates",
                        "--arch", arch,
                        "--components", "main",
                        "--no-rdepends",
                        "--vcs=auto",
                    ], cwd=os.path.join(germinate_output, arch), env=mock.ANY)

                mock_call.assert_has_calls([
                    mock.call([
                        "make", "-C", os.path.dirname(britney_makefile)]),
                    germinate_command("amd64"),
                    germinate_command("i386"),
                    mock.call(
                        ["./build_all.sh"], cwd=debian_cd_dir, env=mock.ANY),
                ])
                mock_tracker_set_rebuild_status.assert_called_once_with(
                    self.config, [0, 1], 2)
                mock_anonftpsync.assert_called_once_with(self.config)
                mock_extract_debootstrap.assert_called_once_with(self.config)
                mock_write_tasks.assert_called_once_with()
                mock_update_tasks.assert_called_once_with(date)
                mock_publish.assert_called_once_with(date)
                mock_purge.assert_called_once_with()
            except AssertionError:
                stderr = os.fdopen(original_stderr, "w", 1)
                try:
                    for entry in os.listdir(log_dir):
                        with open(os.path.join(log_dir, entry)) as log:
                            stderr.write(log.read())
                except IOError:
                    pass
                traceback.print_exc(file=stderr)
                stderr.flush()
                os._exit(1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:  # parent
            self.wait_for_pid(pid, 0)
            self.assertTrue(os.path.isdir(log_dir))
            log_entries = os.listdir(log_dir)
            self.assertEqual(1, len(log_entries))
            log_path = os.path.join(log_dir, log_entries[0])
            with open(log_path) as log:
                self.assertEqual(dedent("""\
                    ===== Syncing Ubuntu mirror =====
                    DATE
                    ===== Building britney =====
                    DATE
                    ===== Extracting debootstrap scripts =====
                    DATE
                    ===== Germinating =====
                    DATE
                    Germinating for trusty/amd64 ...
                    Germinating for trusty/i386 ...
                    ===== Generating new task lists =====
                    DATE
                    ===== Checking for other task changes =====
                    DATE
                    ===== Building Ubuntu daily CDs =====
                    DATE
                    ===== Publishing =====
                    DATE
                    ===== Purging old images =====
                    DATE
                    ===== Triggering mirrors =====
                    DATE
                    ===== Finished =====
                    DATE
                    """.replace("DATE", self.epoch_date)), log.read())

    @mock.patch(
        "cdimage.build.build_image_set_locked", side_effect=KeyboardInterrupt)
    def test_build_image_set_interrupted(self, *args):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-trusty-daily")
        multipidfile_path = os.path.join(
            self.temp_dir, "etc", ".build-image-set-pids")
        os.makedirs(os.path.dirname(lock_path))
        self.assertRaises(
            KeyboardInterrupt, build_image_set, self.config, None)
        self.assertFalse(os.path.exists(lock_path))
        self.assertFalse(os.path.exists(multipidfile_path))

    @mock.patch("cdimage.build.build_image_set_locked")
    def test_build_image_set_terminated(self, mock_build_image_set_locked):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-trusty-daily")
        multipidfile_path = os.path.join(
            self.temp_dir, "etc", ".build-image-set-pids")
        os.makedirs(os.path.dirname(lock_path))

        def side_effect(config, options, multipidfile_state):
            os.kill(os.getpid(), signal.SIGTERM)

        mock_build_image_set_locked.side_effect = side_effect
        pid = os.fork()
        if pid == 0:  # child
            build_image_set(self.config, None)
            os._exit(1)
        else:  # parent
            self.wait_for_pid(pid, signal.SIGTERM)
            self.assertFalse(os.path.exists(multipidfile_path))

    @mock.patch("cdimage.build.build_image_set_locked")
    def test_build_image_set(self, mock_build_image_set_locked):
        self.config["PROJECT"] = "ubuntu"
        self.config["DIST"] = "trusty"
        self.config["IMAGE_TYPE"] = "daily"
        lock_path = os.path.join(
            self.temp_dir, "etc", ".lock-build-image-set-ubuntu-trusty-daily")
        multipidfile_path = os.path.join(
            self.temp_dir, "etc", ".build-image-set-pids")
        os.makedirs(os.path.dirname(lock_path))

        def side_effect(config, options, multipidfile_state):
            self.assertTrue(os.path.exists(lock_path))
            self.assertIsNone(options)
            self.assertEqual(set(), multipidfile_state)
            with open(multipidfile_path) as multipidfile:
                self.assertEqual("%d\n" % os.getpid(), multipidfile.read())

        mock_build_image_set_locked.side_effect = side_effect
        build_image_set(self.config, None)
