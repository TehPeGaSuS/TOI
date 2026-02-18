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
        # Database structure: {network: {channel: {hostmask: [nicks]}}}
        # Example: {"freenode": {"#python": {"user@host.com": ["Alice", "Bob"]}}}
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

    def _get_patterns_for_host(self, irc, channel, nick, user, host):
        """Get tracking patterns for a specific hostmask, checking special patterns first"""
        # Check if this hostmask matches any special patterns
        special_patterns_list = self.registryValue("specialPatterns", channel, irc.network)
        
        if special_patterns_list:
            import fnmatch
            # Build full hostmask for matching
            full_hostmask = f"{nick}!{user}@{host}"
            
            # Parse the special patterns (format: "pattern:replacement pattern2:replacement2")
            for pattern_pair in special_patterns_list:
                if ':' not in pattern_pair:
                    self.log.warning(f"Invalid specialPattern format (missing colon): {pattern_pair}")
                    continue
                
                hostmask_pattern, tracking_pattern = pattern_pair.split(':', 1)
                
                if fnmatch.fnmatch(full_hostmask, hostmask_pattern):
                    # Return the special pattern as a list
                    return [tracking_pattern]
        
        # Use default pattern
        default_pattern = self.registryValue("defaultPattern", channel, irc.network)
        return [default_pattern]

    def _add_record(self, network, channel, nick, user, host):
        """Add a nick to the host's list"""
        network = str(network)
        channel = str(channel)
        hostmask = f"{user}@{host}"
        
        if network not in self.db:
            self.db[network] = {}
        if channel not in self.db[network]:
            self.db[network][channel] = {}
        if hostmask not in self.db[network][channel]:
            self.db[network][channel][hostmask] = []

        # Add nick if not already in list
        if nick not in self.db[network][channel][hostmask]:
            self.db[network][channel][hostmask].append(nick)
            self._dbWrite()

    def _get_nicks_for_patterns(self, network, channel, patterns, nick, user, host):
        """Get all nicks matching the given patterns"""
        network = str(network)
        channel = str(channel)
        
        if network not in self.db or channel not in self.db[network]:
            return []

        # What to look for in the database
        search_terms = {
            pattern.safe_substitute(nick=nick, user=user, host=host)
            for pattern in patterns
        }

        # Collect all matching nicks
        all_nicks = set()
        for hostmask, nicks in self.db[network][channel].items():
            # Split hostmask back into user@host
            if "@" in hostmask:
                db_user, db_host = hostmask.split("@", 1)
            else:
                continue

            # Check if this hostmask matches any search pattern
            for pattern in patterns:
                pattern_result = pattern.safe_substitute(
                    nick="*", user=db_user, host=db_host
                )
                if pattern_result in search_terms:
                    all_nicks.update(nicks)
                    break

        # Remove current nick
        all_nicks.discard(nick)
        
        return list(all_nicks)

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
        """Find matching nicks and announce to targets"""
        targets = self.registryValue("targets", channel, irc.network)
        
        # Get patterns specific to this hostmask (checks special patterns first)
        patterns = self._get_patterns_for_host(irc, channel, new_nick, user, host)

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

        # Get matching nicks from database
        matching_nicks = self._get_nicks_for_patterns(
            irc.network, channel, patterns, new_nick, user, host
        )

        # If there are no other nicks, exit early
        if not matching_nicks:
            return

        # Announce to each target
        for target in targets:
            self._announce_join_to_target(
                irc, new_nick, target, channel, matching_nicks
            )

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
