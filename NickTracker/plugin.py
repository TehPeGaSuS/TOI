# Copyright (c) 2021, Valentin Lorentz
# Modified to use standalone JSON database instead of ChannelLogger
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

import json
import os
import string
import threading
import time

from supybot import conf, callbacks, ircmsgs, ircutils, utils

from supybot.i18n import PluginInternationalization

_ = PluginInternationalization("NickTracker")


class NickTracker(callbacks.Plugin):
    """Keeps track of the nicknames used by people connecting from the same hosts
    
    This is a standalone version that uses its own JSON database and does not
    require the ChannelLogger plugin.
    """

    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        self.dbfile = os.path.join(
            str(conf.supybot.directories.data), "NickTracker", "nicktracker.json"
        )
        # Database structure: {network: {channel: [{timestamp, nick, user, host}, ...]}}
        self.db = {}
        self._initdb()

    def _initdb(self):
        """Initialize database from file"""
        try:
            with open(self.dbfile, "r") as f:
                self.db = json.load(f)
        except (IOError, ValueError):
            self._dbWrite()

    def _write(self, lock):
        """Write database to file with lock"""
        if not os.path.exists(os.path.dirname(self.dbfile)):
            os.makedirs(os.path.dirname(self.dbfile))
        with lock, open(self.dbfile, "w") as f:
            json.dump(self.db, f, indent=2)

    def _dbWrite(self):
        """Thread-safe database write"""
        lock = threading.Lock()
        threading.Thread(target=self._write, args=(lock,)).start()

    def _add_record(self, network, channel, nick, user, host):
        """Add a record to the database"""
        network = str(network)
        channel = str(channel)
        
        if network not in self.db:
            self.db[network] = {}
        if channel not in self.db[network]:
            self.db[network][channel] = []

        record = {
            "timestamp": int(time.time()),
            "nick": nick,
            "user": user,
            "host": host,
        }

        self.db[network][channel].append(record)

        # Prune old records if we exceed maxRecords (unless maxRecords is 0 = unlimited)
        max_records = self.registryValue("maxRecords", channel, network)
        if max_records > 0 and len(self.db[network][channel]) > max_records:
            # Keep only the most recent records
            self.db[network][channel] = sorted(
                self.db[network][channel], key=lambda x: x["timestamp"], reverse=True
            )[: max_records]

        self._dbWrite()

    def _get_records(self, network, channel):
        """Get all records for a channel"""
        network = str(network)
        channel = str(channel)
        
        if network not in self.db or channel not in self.db[network]:
            return []
        return self.db[network][channel]

    def doJoin(self, irc, msg):
        """Handle JOIN events"""
        if msg.channel is None:
            return
        if msg.nick == irc.nick:
            return
        self._handle_new_nick(
            irc,
            ircutils.IrcString(msg.channel),
            msg.nick,
            msg.user,
            msg.host,
        )

    def doNick(self, irc, msg):
        """Handle NICK changes"""
        new_nick = msg.args[0]
        if msg.nick == irc.nick or new_nick == irc.nick:
            return
        for channel in msg.tagged("channels"):
            self._handle_new_nick(
                irc, ircutils.IrcString(channel), new_nick, msg.user, msg.host
            )

    def _handle_new_nick(self, irc, channel, new_nick, user, host):
        """Handle a new nick (from JOIN or NICK change)"""
        # Announce before adding the new record
        self._announce(irc, channel, new_nick, user, host)
        
        # Add the new record to database
        self._add_record(irc.network, channel, new_nick, user, host)

    def _announce(self, irc, channel, new_nick, user, host):
        """Find matching records and announce to targets"""
        targets = self.registryValue("targets", channel, irc.network)
        patterns = self.registryValue("patterns", channel, irc.network)

        # Discard any pattern that has no variable at all
        patterns = [
            pattern
            for pattern in patterns
            if any(var in pattern for var in ("$nick", "$user", "$host"))
        ]

        if not targets or not patterns:
            return

        # Compile patterns
        patterns = [string.Template(pattern) for pattern in patterns]

        # What to look for in the history
        search_terms = {
            pattern.safe_substitute(nick=new_nick, user=user, host=host)
            for pattern in patterns
        }

        # Get all records for this channel
        all_records = self._get_records(irc.network, channel)

        # Find matching records
        matching_records = []
        for record in all_records:
            # Check if this record matches any of our search patterns
            record_patterns = {
                pattern.safe_substitute(
                    nick=record["nick"], user=record["user"], host=record["host"]
                )
                for pattern in patterns
            }
            if record_patterns & search_terms:
                matching_records.append(record)

        # Sort by timestamp, most recent first
        matching_records.sort(key=lambda x: x["timestamp"], reverse=True)

        # Get the last occurrence of each nick
        nicks = {}
        for record in matching_records:
            if record["nick"] in nicks:
                continue
            nicks[record["nick"]] = record["timestamp"]

        # Remove the current nick from the list
        if new_nick in nicks:
            del nicks[new_nick]

        # If there are no other nicks, exit early
        if not nicks:
            return

        # Sort nicks by timestamp (most recent first) and get just the nick names
        latest_nicks = [
            nick
            for (timestamp, nick) in sorted(
                ((timestamp, nick) for (nick, timestamp) in nicks.items()), reverse=True
            )
        ]

        # Announce to each target
        for target in targets:
            self._announce_join_to_target(irc, new_nick, target, channel, latest_nicks)

    def _announce_join_to_target(self, irc, new_nick, target, source, nicks):
        """Announce the given list of nicks to a target"""
        separator = self.registryValue("announce.nicks.separator", target, irc.network)
        nick_string = separator.join(nicks)
        prefix = f"[{source}] {new_nick} also used nicks: "

        # Roughly wrap to make sure it doesn't exceed 512 byte lines
        max_payload_size = (
            512
            - len(irc.prefix)
            - len(target)
            - len(": PRIVMSG  :\r\n")
            - len(prefix.encode())
        )
        nick_lines = utils.str.byteTextWrap(
            nick_string,
            max_payload_size - 50,  # just to be safe
        )

        max_lines = self.registryValue("announce.nicks.lines", target, irc.network)
        nick_lines = nick_lines[0:max_lines]

        for nick_line in nick_lines:
            irc.queueMsg(
                ircmsgs.privmsg(
                    target,
                    prefix + nick_line,
                )
            )


Class = NickTracker

# vim:set shiftwidth=4 tabstop=4 expandtab textwidth=79:
