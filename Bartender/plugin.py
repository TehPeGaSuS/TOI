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

import os
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
# Database layer
# ---------------------------------------------------------------------------

class BartenderDB:
    """Global SQLite database for the drink menu."""

    def __init__(self, filename):
        self.filename = filename
        self._db = None

    def _get_db(self):
        if self._db is not None:
            return self._db
        db = sqlite3.connect(self.filename, check_same_thread=False)
        db.row_factory = sqlite3.Row
        cursor = db.cursor()
        cursor.executescript("""
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
        self._db = db
        return db

    def close(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    # -- resolution ----------------------------------------------------------

    def resolve(self, name):
        """Return the canonical drink name, following aliases. None if unknown."""
        db = self._get_db()
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

    def get_drink(self, name):
        """Return a dict with {name, serve_msg} or None."""
        canonical = self.resolve(name)
        if canonical is None:
            return None
        db = self._get_db()
        c = db.cursor()
        c.execute("SELECT name, serve_msg FROM drinks WHERE name = ?", (canonical,))
        row = c.fetchone()
        return dict(row) if row else None

    # -- CRUD ----------------------------------------------------------------

    def add_drink(self, name, serve_msg, added_by):
        db = self._get_db()
        try:
            db.execute(
                "INSERT INTO drinks (name, serve_msg, added_by) VALUES (?, ?, ?)",
                (name, serve_msg, added_by)
            )
            db.commit()
            return True
        except sqlite3.IntegrityError:
            return False  # already exists

    def remove_drink(self, name):
        canonical = self.resolve(name)
        if canonical is None:
            return False
        db = self._get_db()
        db.execute("DELETE FROM drinks WHERE name = ?", (canonical,))
        db.commit()
        return True

    def edit_drink(self, name, serve_msg):
        canonical = self.resolve(name)
        if canonical is None:
            return False
        db = self._get_db()
        db.execute(
            "UPDATE drinks SET serve_msg = ? WHERE name = ?",
            (serve_msg, canonical)
        )
        db.commit()
        return True

    def add_alias(self, drink_name, alias):
        canonical = self.resolve(drink_name)
        if canonical is None:
            return False, 'no_drink'
        db = self._get_db()
        try:
            db.execute(
                "INSERT INTO aliases (alias, drink_name) VALUES (?, ?)",
                (alias, canonical)
            )
            db.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, 'exists'

    def list_drinks(self):
        db = self._get_db()
        c = db.cursor()
        c.execute("SELECT name FROM drinks ORDER BY name COLLATE NOCASE")
        return [row['name'] for row in c.fetchall()]

    def get_aliases(self, drink_name):
        canonical = self.resolve(drink_name)
        if canonical is None:
            return None
        db = self._get_db()
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

    Enable the bar per channel with:
        config channel plugins.Bartender.enabled True
    """

    threaded = True

    def __init__(self, irc):
        super().__init__(irc)
        db_path = conf.supybot.directories.data.dirize('Bartender.db')
        self.db = BartenderDB(db_path)
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
            return False  # silent -- no message
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
        """<drink> [for <nick>]

        Orders a drink from the bar. If "for <nick>" is provided, the drink is
        served to that user instead. The target must be in the channel.
        Example: !order beer
        Example: !order beer for Alice
        """
        if not msg.channel:
            irc.error('This command must be used in a channel.', Raise=True)
        if not self._is_enabled(irc, msg):
            return

        channel = msg.channel
        cooldown = self.registryValue('cooldown', channel, irc.network)
        if not self._check_cooldown(irc, channel, self._order_cooldowns, cooldown):
            return  # silent on cooldown

        # Parse "drink [for nick]"
        if ' for ' in drink_arg:
            drink_name, target = [s.strip() for s in drink_arg.split(' for ', 1)]
        else:
            drink_name = drink_arg.strip()
            target = msg.nick

        # Validate target is in channel (only when explicitly given)
        if target != msg.nick and not self._nick_in_channel(irc, channel, target):
            irc.reply('%s is not in %s.' % (target, channel), prefixNick=False)
            return

        drink = self.db.get_drink(drink_name)
        if drink is None:
            irc.reply(
                "I don't know how to make that. Try !bartender list.",
                prefixNick=False
            )
            return

        text = _substitute(drink['serve_msg'], {
            'nick':    msg.nick,
            'target':  target,
            'drink':   drink['name'],
            'channel': channel,
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

        drink = self.db.get_drink(drink_name.strip())
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

            Adds a new drink to the menu. The serve message is optional -- if
            omitted, the global default is used
            (config plugins.Bartender.defaultServeMessage).
            Use $nick (who ordered), $target (recipient), $drink (drink name),
            and $channel in the message.
            Example: !bartender add beer
            Example: !bartender add beer slides a cold pint to $target
            """
            if not self.parent._require_admin(irc, msg):
                return
            if not serve_msg:
                serve_msg = self.parent.registryValue('defaultServeMessage')
            ok = self.parent.db.add_drink(name, serve_msg, msg.nick)
            if ok:
                irc.replySuccess()
            else:
                irc.error('A drink named "%s" already exists.' % name)

        add = wrap(add, ['something', optional('text')])

        def remove(self, irc, msg, args, name):
            """<n>

            Removes a drink (and all its aliases) from the menu.
            Example: !bartender remove beer
            """
            if not self.parent._require_admin(irc, msg):
                return
            ok = self.parent.db.remove_drink(name)
            if ok:
                irc.replySuccess()
            else:
                irc.error('No drink named "%s" found.' % name)

        remove = wrap(remove, ['something'])

        def edit(self, irc, msg, args, name, serve_msg):
            """<n> <new serve message>

            Edits the serve message for an existing drink.
            Example: !bartender edit beer slides an ice-cold pint to $target
            """
            if not self.parent._require_admin(irc, msg):
                return
            ok = self.parent.db.edit_drink(name, serve_msg)
            if ok:
                irc.replySuccess()
            else:
                irc.error('No drink named "%s" found.' % name)

        edit = wrap(edit, ['something', 'text'])

        def alias(self, irc, msg, args, drink_name, alias):
            """<drink name> <alias>

            Adds an alias for an existing drink so users can order it by
            another name.
            Example: !bartender alias beer lager
            """
            if not self.parent._require_admin(irc, msg):
                return
            ok, reason = self.parent.db.add_alias(drink_name, alias)
            if ok:
                irc.replySuccess()
            elif reason == 'no_drink':
                irc.error('No drink named "%s" found.' % drink_name)
            else:
                irc.error('The alias "%s" already exists.' % alias)

        alias = wrap(alias, ['something', 'something'])

        def show(self, irc, msg, args, name):
            """<n>

            Shows the serve message and aliases for a drink.
            Example: !bartender show beer
            """
            drink = self.parent.db.get_drink(name)
            if drink is None:
                irc.error('No drink named "%s" found.' % name)
                return
            aliases = self.parent.db.get_aliases(drink['name'])
            alias_str = (', aliases: ' + ', '.join(aliases)) if aliases else ''
            irc.reply(
                '[%s%s] %s' % (drink['name'], alias_str, drink['serve_msg']),
                prefixNick=False
            )

        show = wrap(show, ['something'])

        def list(self, irc, msg, args):
            """(takes no arguments)

            Lists all available drinks on the menu.
            Example: !bartender list
            """
            drinks = self.parent.db.list_drinks()
            if not drinks:
                irc.reply('The menu is empty. Ask an admin to add some drinks!')
            else:
                irc.reply('Available drinks: ' + ', '.join(drinks), prefixNick=False)

        list = wrap(list, [])


Class = Bartender

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
