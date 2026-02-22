###
# Copyright (c) 2024, Bartender Plugin Contributors
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#   * Redistributions of source code must retain the above copyright notice,
#     this list of conditions, and the following disclaimer.
#   * Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions, and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#   * Neither the name of the author of this software nor the name of
#     contributors to this software may be used to endorse or promote products
#     derived from this software without specific prior written consent.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
###

import time
import sqlite3
import string

import supybot.conf as conf
import supybot.ircdb as ircdb
import supybot.plugins as plugins
import supybot.ircutils as ircutils
from supybot.commands import *
import supybot.callbacks as callbacks
from supybot.i18n import PluginInternationalization

_ = PluginInternationalization('Bartender')


# ---------------------------------------------------------------------------
# Database layer — one SQLite file per channel
# ---------------------------------------------------------------------------

class BartenderDB:
    """Per-channel SQLite databases for the drink menu."""

    def __init__(self):
        self._dbs = {}  # {filename: connection}

    def _get_db(self, channel):
        filename = plugins.makeChannelFilename('Bartender.db', channel)
        if filename in self._dbs:
            return self._dbs[filename]
        db = sqlite3.connect(filename, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.cursor().executescript("""
            CREATE TABLE IF NOT EXISTS drinks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                serve_msg   TEXT    NOT NULL,
                added_by    TEXT,
                added_on    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS aliases (
                alias       TEXT    PRIMARY KEY COLLATE NOCASE,
                drink_name  TEXT    NOT NULL COLLATE NOCASE,
                FOREIGN KEY (drink_name) REFERENCES drinks(name)
                    ON DELETE CASCADE ON UPDATE CASCADE
            );
        """)
        db.commit()
        self._dbs[filename] = db
        return db

    def close(self):
        for db in self._dbs.values():
            db.close()
        self._dbs.clear()

    # -- resolution ----------------------------------------------------------

    def resolve(self, channel, name):
        """Return the canonical drink name, following aliases. None if unknown."""
        db = self._get_db(channel)
        c = db.cursor()
        c.execute("SELECT name FROM drinks WHERE name = ?", (name,))
        row = c.fetchone()
        if row:
            return row['name']
        c.execute("SELECT drink_name FROM aliases WHERE alias = ?", (name,))
        row = c.fetchone()
        if row:
            return row['drink_name']
        return None

    def get_drink(self, channel, name):
        """Return a dict with {name, serve_msg} or None."""
        canonical = self.resolve(channel, name)
        if canonical is None:
            return None
        db = self._get_db(channel)
        c = db.cursor()
        c.execute("SELECT name, serve_msg FROM drinks WHERE name = ?", (canonical,))
        row = c.fetchone()
        return dict(row) if row else None

    # -- CRUD ----------------------------------------------------------------

    def add_drink(self, channel, name, serve_msg, added_by):
        db = self._get_db(channel)
        try:
            db.execute(
                "INSERT INTO drinks (name, serve_msg, added_by) VALUES (?, ?, ?)",
                (name, serve_msg, added_by)
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def remove_drink(self, channel, name):
        canonical = self.resolve(channel, name)
        if canonical is None:
            return False
        db = self._get_db(channel)
        db.execute("DELETE FROM drinks WHERE name = ?", (canonical,))
        db.commit()
        return True

    def edit_drink(self, channel, name, serve_msg):
        canonical = self.resolve(channel, name)
        if canonical is None:
            return False
        db = self._get_db(channel)
        db.execute(
            "UPDATE drinks SET serve_msg = ? WHERE name = ?",
            (serve_msg, canonical)
        )
        db.commit()
        return True

    def add_alias(self, channel, drink_name, alias):
        canonical = self.resolve(channel, drink_name)
        if canonical is None:
            return False, 'no_drink'
        db = self._get_db(channel)
        try:
            db.execute(
                "INSERT INTO aliases (alias, drink_name) VALUES (?, ?)",
                (alias, canonical)
            )
            db.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, 'exists'

    def list_drinks(self, channel):
        db = self._get_db(channel)
        c = db.cursor()
        c.execute("SELECT name FROM drinks ORDER BY name COLLATE NOCASE")
        return [row['name'] for row in c.fetchall()]

    def get_aliases(self, channel, drink_name):
        canonical = self.resolve(channel, drink_name)
        if canonical is None:
            return None
        db = self._get_db(channel)
        c = db.cursor()
        c.execute(
            "SELECT alias FROM aliases WHERE drink_name = ? ORDER BY alias COLLATE NOCASE",
            (canonical,)
        )
        return [row['alias'] for row in c.fetchall()]


# ---------------------------------------------------------------------------
# Token substitution
# ---------------------------------------------------------------------------

def _substitute(template, mapping):
    """Safe $token substitution. Unknown tokens are left as-is."""
    return string.Template(template).safe_substitute(mapping)


# ---------------------------------------------------------------------------
# Plugin class
# ---------------------------------------------------------------------------

class Bartender(callbacks.Plugin):
    """
    A fun IRC bartender. Serve drinks to users with !order, buy the whole
    channel a round with !round, and manage the drink menu with
    !bartender add/remove/alias/edit/show/list.

    The drink menu is per-channel, so each channel can have its own drinks
    and serve messages in any language.

    Enable the bar per channel with:
        config channel plugins.Bartender.enabled True
    """

    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        self.db = BartenderDB()
        # Cooldown tracking: {channel: last_timestamp}
        self._order_cooldowns = {}
        self._round_cooldowns = {}

    def die(self):
        self.db.close()
        super().die()

    # -- helpers -------------------------------------------------------------

    def _is_enabled(self, irc, msg):
        """Return True if the plugin is enabled; else tell the user and return False."""
        channel = msg.channel
        if not self.registryValue('enabled', channel, irc.network):
            irc.reply(
                'The bar is closed in %s.' % channel,
                prefixNick=False
            )
            return False
        return True

    def _check_cooldown(self, irc, channel, cooldown_dict, cooldown_seconds):
        """Return True if the action is allowed. Return False silently if on cooldown."""
        if cooldown_seconds == 0:
            return True
        now = time.time()
        last = cooldown_dict.get(channel, 0)
        if now - last < cooldown_seconds:
            return False
        cooldown_dict[channel] = now
        return True

    def _require_admin(self, irc, msg):
        """Return True if the caller has the admin capability, else reply and return False."""
        try:
            if ircdb.checkCapability(msg.prefix, 'admin'):
                return True
        except Exception:
            pass
        irc.error('This command requires the admin capability.')
        return False

    def _nick_in_channel(self, irc, channel, nick):
        """Return True if nick is currently in the channel."""
        chan = irc.state.channels.get(channel)
        if chan is None:
            return False
        return nick.lower() in (n.lower() for n in chan.users)

    # -- public commands -----------------------------------------------------

    def order(self, irc, msg, args, drink_arg):
        """<drink> [<nick>]

        Orders a drink from the bar. If a nick is given as the last word, the
        drink is served to that user instead. The target must be in the channel.
        Example: !order beer
        Example: !order beer Alice
        Example: !order shot of tequila Alice
        """
        if not msg.channel:
            irc.error('This command must be used in a channel.', Raise=True)
        if not self._is_enabled(irc, msg):
            return

        channel = msg.channel
        cooldown = self.registryValue('cooldown', channel, irc.network)
        if not self._check_cooldown(irc, channel, self._order_cooldowns, cooldown):
            return  # silent on cooldown

        # If there are multiple words, the last word is always the target nick
        # and everything before it is the drink name. Single word = self-order.
        parts = drink_arg.strip().split(' ')
        if len(parts) == 1:
            drink_name = parts[0]
            target = msg.nick
        else:
            drink_name = ' '.join(parts[:-1])
            target = parts[-1]
            if not self._nick_in_channel(irc, channel, target):
                irc.reply(
                    "Sorry %s, I don't see a customer with the name %s in %s."
                    % (msg.nick, target, channel),
                    prefixNick=False
                )
                return

        drink = self.db.get_drink(channel, drink_name)
        if drink is None:
            irc.reply(
                "I don't know how to make that. Try !bartender list.",
                prefixNick=False
            )
            return

        # Use the drink's custom message if it differs from both defaults.
        # Otherwise pick the appropriate default based on self vs for-someone.
        default_self = self.registryValue('defaultServeMessage', channel, irc.network)
        default_for  = self.registryValue('defaultServeMessageFor', channel, irc.network)
        stored_msg   = drink['serve_msg']

        if stored_msg in (default_self, default_for):
            template = default_for if target != msg.nick else default_self
        else:
            template = stored_msg

        courtesy = ', courtesy of %s' % msg.nick if target != msg.nick else ''
        text = _substitute(template, {
            'nick':     msg.nick,
            'target':   target,
            'drink':    drink['name'],
            'channel':  channel,
            'courtesy': courtesy,
        })
        irc.reply(text, action=True, prefixNick=False)

    order = wrap(order, ['text'])

    def round(self, irc, msg, args, drink_name):
        """<drink>

        Buys a round of <drink> for everyone in the channel.
        Example: !round beer
        """
        if not msg.channel:
            irc.error('This command must be used in a channel.', Raise=True)
        if not self._is_enabled(irc, msg):
            return

        channel = msg.channel
        cooldown = self.registryValue('roundCooldown', channel, irc.network)
        if not self._check_cooldown(irc, channel, self._round_cooldowns, cooldown):
            return  # silent on cooldown

        drink = self.db.get_drink(channel, drink_name.strip())
        if drink is None:
            irc.reply(
                "I don't know how to make that. Try !bartender list.",
                prefixNick=False
            )
            return

        template = self.registryValue('roundMessage', channel, irc.network)
        text = _substitute(template, {
            'nick':    msg.nick,
            'drink':   drink['name'],
            'channel': channel,
        })
        irc.reply(text, action=True, prefixNick=False)

    round = wrap(round, ['text'])

    # -- admin subcommands ---------------------------------------------------

    class bartender(callbacks.Commands):

        def add(self, irc, msg, args, name, serve_msg):
            """<n> [<serve message>]

            Adds a new drink to this channel's menu. The serve message is
            optional -- if omitted, the channel default is used
            (config channel plugins.Bartender.defaultServeMessage).
            Use $nick (who ordered), $target (recipient), $drink (drink name),
            $channel, and $courtesy (expands to ', courtesy of $nick' when
            ordering for someone else, empty string otherwise) in the message.
            Example: !bartender add beer
            Example: !bartender add tequila pours $target a shot of tequila$courtesy.
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            if not self.parent._require_admin(irc, msg):
                return
            if not serve_msg:
                serve_msg = self.parent.registryValue(
                    'defaultServeMessage', msg.channel, irc.network)
            ok = self.parent.db.add_drink(msg.channel, name, serve_msg, msg.nick)
            if ok:
                irc.replySuccess()
            else:
                irc.error('A drink named "%s" already exists in %s.' % (name, msg.channel))

        add = wrap(add, ['something', optional('text')])

        def remove(self, irc, msg, args, name):
            """<n>

            Removes a drink (and all its aliases) from this channel's menu.
            Example: !bartender remove beer
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            if not self.parent._require_admin(irc, msg):
                return
            ok = self.parent.db.remove_drink(msg.channel, name)
            if ok:
                irc.replySuccess()
            else:
                irc.error('No drink named "%s" found.' % name)

        remove = wrap(remove, ['something'])

        def edit(self, irc, msg, args, name, serve_msg):
            """<n> <new serve message>

            Edits the serve message for a drink in this channel's menu.
            Example: !bartender edit beer slides an ice-cold pint to $target$courtesy.
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            if not self.parent._require_admin(irc, msg):
                return
            ok = self.parent.db.edit_drink(msg.channel, name, serve_msg)
            if ok:
                irc.replySuccess()
            else:
                irc.error('No drink named "%s" found.' % name)

        edit = wrap(edit, ['something', 'text'])

        def alias(self, irc, msg, args, drink_name, alias):
            """<drink name> <alias>

            Adds an alias for an existing drink in this channel's menu.
            Example: !bartender alias whiskey bourbon
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            if not self.parent._require_admin(irc, msg):
                return
            ok, reason = self.parent.db.add_alias(msg.channel, drink_name, alias)
            if ok:
                irc.replySuccess()
            elif reason == 'no_drink':
                irc.error('No drink named "%s" found.' % drink_name)
            else:
                irc.error('The alias "%s" already exists.' % alias)

        alias = wrap(alias, ['something', 'something'])

        def show(self, irc, msg, args, name):
            """<n>

            Shows the serve message and aliases for a drink in this channel's menu.
            Example: !bartender show beer
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            drink = self.parent.db.get_drink(msg.channel, name)
            if drink is None:
                irc.error('No drink named "%s" found.' % name)
                return
            aliases = self.parent.db.get_aliases(msg.channel, drink['name'])
            alias_str = (', aliases: ' + ', '.join(aliases)) if aliases else ''
            irc.reply(
                '[%s%s] %s' % (drink['name'], alias_str, drink['serve_msg']),
                prefixNick=False
            )

        show = wrap(show, ['something'])

        def list(self, irc, msg, args):
            """(takes no arguments)

            Lists all available drinks in this channel's menu.
            Example: !bartender list
            """
            if not msg.channel:
                irc.error('This command must be used in a channel.', Raise=True)
            drinks = self.parent.db.list_drinks(msg.channel)
            if not drinks:
                irc.reply("The menu is empty. Ask an admin to add some drinks!")
            else:
                irc.reply('Available drinks: ' + ', '.join(drinks), prefixNick=False)

        list = wrap(list, [])


Class = Bartender

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
