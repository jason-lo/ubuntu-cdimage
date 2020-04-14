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

"""Logging for cdimage programs."""

import logging
import sys

__metaclass__ = type


class CDImageLogger(logging.Formatter):
    """Format messages in cdimage's preferred style."""

    def format(self, record):
        return record.getMessage()


def setup_logging():
    logger.setLevel(logging.INFO)
    # TODO: errors (and warnings?) should go to stderr
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CDImageLogger())
    logger.addHandler(handler)
    logger.propagate = False


def reset_logging():
    logger = logging.getLogger("cdimage")
    logger.handlers = []
    setup_logging()


logging.basicConfig()
logger = logging.getLogger("cdimage")
if not logger.handlers:
    setup_logging()
