# Copyright (c) 2021, Valentin Lorentz
# Modified to use standalone database
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

###

"""
NickTracker: Keeps track of nicknames used by people from the same hosts
"""

import supybot
from supybot import world

__version__ = "2.0.0"

__author__ = supybot.Author("Valentin Lorentz", "progval", "progval@progval.net")
__maintainer__ = supybot.Author("Modified for standalone use")

__contributors__ = {}

__url__ = ""

from . import config
from . import plugin
from importlib import reload

reload(config)
reload(plugin)

if world.testing:
    from . import test

Class = plugin.Class
configure = config.configure

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
