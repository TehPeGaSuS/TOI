###
# Resilience - A Limnoria plugin for automatic channel recovery and
# nick/op self-maintenance across multiple networks.
#
# Features:
#   - Indefinite retry on join errors (banned, full, invite-only, bad key)
#   - Auto-unban self via MODE before rejoining when op is available elsewhere
#   - Auto-rejoin after kick with configurable delay
#   - Auto-reop self if deopped in channel
#   - Per-connect perform commands (like ZNC *perform), per-network
#   - Nick recovery: reclaim desired nick when it becomes available
###

"""
Automatic channel/nick resilience for Limnoria.  Retries joining channels
indefinitely on ban/full/invite-only/bad-key, auto-reops, performs on
connect, and reclaims nicks — all per-network and per-channel.
"""

import supybot
import supybot.world as world

__version__ = "1.0.0"
__author__ = supybot.Author('You', 'you', 'you@example.com')
__contributors__ = {}

from . import config
from . import plugin
from importlib import reload
reload(plugin)

if world.testing:
    from . import test

Class = plugin.Class
configure = config.configure

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
