###
# Resilience - plugin.py
#
# Automatic IRC self-maintenance:
#   - Indefinite join retry (ban / full / invite-only / bad-key)
#   - ChanServ UNBAN / INVITE before rejoining (Anope / Atheme)
#   - ChanServ UP on join (Anope)
#   - MODE -b self-unban fallback (when bot retains ops)
#   - Auto-rejoin after kick
#   - Auto-reop after deop (halfop self-op or ChanServ UP)
#   - Per-network perform commands on connect (comma-separated, $substitutions)
#   - Per-network nick-recovery commands when on fallback nick
#   - Nick password storage ($password substitution)
#   - Claim desired nick on QUIT/NICK events + periodic polling
###

import re
import time

import supybot.conf as conf
import supybot.world as world
import supybot.ircmsgs as ircmsgs
import supybot.ircutils as ircutils
import supybot.schedule as schedule
import supybot.callbacks as callbacks
from supybot.commands import wrap, additional
from supybot.i18n import PluginInternationalization
_ = PluginInternationalization('Resilience')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _desired_nick(irc):
    """Return the configured nick for this network (or global fallback)."""
    n = conf.supybot.networks.get(irc.network).nick()
    return n if n else conf.supybot.nick()


# Matches an escaped comma (\,) so we can split on real commas only
_ESCAPED_COMMA = re.compile(r'\\,')
_REAL_COMMA    = re.compile(r'(?<!\\),')


def _split_commands(s):
    """
    Split a comma-separated command string into individual commands.
    Escaped commas (\\,) are kept; surrounding whitespace is stripped.
    Returns a list of non-empty strings.
    """
    parts = _REAL_COMMA.split(s)
    return [_ESCAPED_COMMA.sub(',', p).strip() for p in parts if p.strip()]


def _substitute(template, irc, desired_nick, password):
    """
    Apply $-substitutions to a perform/recover command string.

    Variables:
      $nick / $botnick  — desired (configured) nick
      $currentnick      — the nick the bot is currently using
      $network          — network name
      $password         — nickPassword config value
    """
    import string
    t = string.Template(template)
    t.idpattern = r'[a-zA-Z][a-zA-Z0-9]*'
    return t.safe_substitute(
        nick=desired_nick,
        botnick=desired_nick,
        currentnick=irc.nick,
        network=irc.network,
        password=password,
    )


def _parse_irc_command(cmd_str):
    """
    Parse a raw IRC command string like 'PRIVMSG NickServ :IDENTIFY pass'
    into an IrcMsg.  Returns None if the string is empty/invalid.
    """
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return None
    parts = cmd_str.split(None, 1)
    command = parts[0].upper()
    rest = parts[1] if len(parts) > 1 else ''

    if ':' in rest:
        colon_idx = rest.index(':')
        positional = rest[:colon_idx].split()
        trailing   = rest[colon_idx + 1:]
        args = tuple(positional) + (trailing,)
    else:
        args = tuple(rest.split()) if rest else ()

    return ircmsgs.IrcMsg(command=command, args=args)


class Resilience(callbacks.Plugin):
    """
    Automatic channel and nick resilience for Limnoria.

    - Retries joining banned/full/invite-only/locked channels indefinitely.
    - Asks ChanServ to unban or invite before each retry (configurable).
    - Falls back to MODE -b self-unban when the bot has ops.
    - Rejoins after kicks and reops after deop.
    - Sends per-network perform commands on connect (comma-separated with
      $nick/$password/$network substitutions).
    - Sends nick-recovery commands when on a fallback nick.
    - Reclaims the configured nick on QUIT/NICK events and via polling.
    - Asks ChanServ UP on join for Anope-based networks.

    Configuration lives under supybot.plugins.Resilience.*
    """

    def __init__(self, irc):
        self.__parent = super(Resilience, self)
        self.__parent.__init__(irc)

        # (network, channel) -> event name  — pending join retries
        self._joinRetryEvents    = {}
        # (network, channel) -> event name  — pending reop
        self._reopEvents         = {}
        # (network, channel) -> event name  — pending kick-rejoin
        self._kickRejoinEvents   = {}
        # network -> event name             — periodic nick poll
        self._nickRecoverEvents  = {}
        # network -> event name             — pending perform delay
        self._performEvents      = {}

        # Channels where we're waiting for a ChanServ UNBAN confirmation
        # before rejoining.  (network, channel) -> True
        self._waitingCsUnban     = {}
        # Channels where we're waiting for a ChanServ INVITE.
        self._waitingCsInvite    = {}

    def die(self):
        for mapping in (self._joinRetryEvents, self._reopEvents,
                        self._kickRejoinEvents, self._nickRecoverEvents,
                        self._performEvents):
            for name in list(mapping.values()):
                try:
                    schedule.removeEvent(name)
                except KeyError:
                    pass
            mapping.clear()
        self.__parent.die()

    # -----------------------------------------------------------------------
    # Scheduling helpers
    # -----------------------------------------------------------------------

    def _safe_event(self, func, when, name):
        try:
            schedule.removeEvent(name)
        except KeyError:
            pass
        return schedule.addEvent(func, when, name=name)

    def _safe_periodic(self, func, interval, name):
        try:
            schedule.removeEvent(name)
        except KeyError:
            pass
        return schedule.addPeriodicEvent(func, interval, name=name, now=False)

    # -----------------------------------------------------------------------
    # ChanServ helpers
    # -----------------------------------------------------------------------

    def _chanserv(self, irc):
        """Return the configured ChanServ nick, or None if disabled."""
        cs = self.registryValue('ChanServ', network=irc.network)
        return cs if cs else None

    def _cs_command(self, irc, command, channel):
        """Send a command to ChanServ (e.g. 'UNBAN #chan' or 'UP #chan')."""
        cs = self._chanserv(irc)
        if not cs:
            self.log.debug('Resilience: ChanServ not configured on %s, '
                           'skipping %s %s.', irc.network, command, channel)
            return False
        irc.queueMsg(ircmsgs.privmsg(cs, '%s %s' % (command, channel)))
        self.log.info('Resilience: sent to %s on %s: %s %s',
                      cs, irc.network, command, channel)
        return True

    # -----------------------------------------------------------------------
    # Substitution helpers
    # -----------------------------------------------------------------------

    def _password(self, network):
        return self.registryValue('nickPassword', network=network)

    def _do_substitution(self, template, irc):
        desired = _desired_nick(irc)
        password = self._password(irc.network)
        return _substitute(template, irc, desired, password)

    def _send_command_list(self, irc, raw_string, label='perform'):
        """
        Parse, substitute, and send a comma-separated command string.
        `label` is used only for log messages.
        """
        cmds = _split_commands(raw_string)
        if not cmds:
            return
        for cmd_str in cmds:
            substituted = self._do_substitution(cmd_str, irc)
            msg = _parse_irc_command(substituted)
            if msg is None:
                continue
            self.log.info('Resilience: %s → %s on %s',
                          label, substituted.strip(), irc.network)
            irc.sendMsg(msg)

    # -----------------------------------------------------------------------
    # Perform on connect
    # -----------------------------------------------------------------------

    def _schedulePerform(self, irc):
        network = irc.network
        delay = self.registryValue('performDelay', network=network)
        event_name = 'Resilience_perform_%s' % network

        raw = self.registryValue('perform', network=network).strip()
        if not raw:
            return

        def _fire():
            self._performEvents.pop(network, None)
            self._send_command_list(irc, raw, label='perform')

        if delay == 0:
            _fire()
            return
        name = self._safe_event(_fire, time.time() + delay, event_name)
        self._performEvents[network] = name

    # -----------------------------------------------------------------------
    # Nick recovery commands
    # -----------------------------------------------------------------------

    def _send_nick_recover_commands(self, irc):
        """Send the configured nick-recovery command sequence."""
        raw = self.registryValue('nickRecoverCommands',
                                 network=irc.network).strip()
        if not raw:
            return
        self._send_command_list(irc, raw, label='nickRecover')

    def _startNickRecovery(self, irc):
        """Start the periodic nick-recovery polling loop."""
        network = irc.network
        if network in self._nickRecoverEvents:
            return
        delay = self.registryValue('recoverNickDelay', network=network)
        if delay == 0:
            return
        event_name = 'Resilience_nick_%s' % network

        def _poll():
            desired = _desired_nick(irc)
            if ircutils.strEqual(irc.nick, desired):
                self._stopNickRecovery(irc)
                return
            if desired not in irc.state.nicksToHostmasks:
                self.log.info('Resilience: polling — trying to claim %s on %s.',
                              desired, network)
                irc.queueMsg(ircmsgs.nick(desired))

        name = self._safe_periodic(_poll, delay, event_name)
        self._nickRecoverEvents[network] = name
        self.log.info('Resilience: started nick recovery poll for %s on %s '
                      '(every %ds).', _desired_nick(irc), network, delay)

    def _stopNickRecovery(self, irc):
        name = self._nickRecoverEvents.pop(irc.network, None)
        if name is not None:
            try:
                schedule.removeEvent(name)
            except KeyError:
                pass

    # -----------------------------------------------------------------------
    # Join retry
    # -----------------------------------------------------------------------

    def _scheduleJoinRetry(self, irc, channel):
        key = (irc.network, channel)
        delay = self.registryValue('retryJoinDelay', channel, irc.network)
        event_name = 'Resilience_join_%s_%s' % (irc.network, channel)

        def _retry():
            self._joinRetryEvents.pop(key, None)
            if channel not in irc.state.channels:
                self.log.info('Resilience: retrying JOIN %s on %s.',
                              channel, irc.network)
                networkGroup = conf.supybot.networks.get(irc.network)
                irc.queueMsg(networkGroup.channels.join(channel))

        name = self._safe_event(_retry, time.time() + delay, event_name)
        self._joinRetryEvents[key] = name
        self.log.info('Resilience: JOIN retry for %s on %s in %ds.',
                      channel, irc.network, delay)

    def _cancelJoinRetry(self, irc, channel):
        key = (irc.network, channel)
        name = self._joinRetryEvents.pop(key, None)
        if name is not None:
            try:
                schedule.removeEvent(name)
            except KeyError:
                pass

    def _handleJoinError(self, irc, msg, error_label, config_key):
        channel = msg.args[1]
        if not self.registryValue('retryJoin', channel, irc.network):
            return
        if not self.registryValue(config_key, channel, irc.network):
            return

        delay = self.registryValue('retryJoinDelay', channel, irc.network)
        self.log.info('Resilience: cannot join %s on %s (%s), retry in %ds.',
                      channel, irc.network, error_label, delay)

        if error_label == 'banned':
            # 1. Try MODE -b if we somehow still have ops
            if self.registryValue('selfUnban', channel, irc.network):
                self._trySelfUnban(irc, channel)
            # 2. Try ChanServ UNBAN — if ChanServ is configured, wait for its
            #    confirmation notice before scheduling the retry join, so we
            #    don't attempt to rejoin before the ban is actually lifted.
            if self.registryValue('useChanServUnban', channel, irc.network):
                if self._cs_command(irc, 'UNBAN', channel):
                    self._waitingCsUnban[(irc.network, channel)] = True
                    # Still schedule a fallback retry in case ChanServ is slow
                    # or doesn't respond.
                    self._scheduleJoinRetry(irc, channel)
                    return

        elif error_label == 'invite-only':
            if self.registryValue('useChanServInvite', channel, irc.network):
                if self._cs_command(irc, 'INVITE', channel):
                    self._waitingCsInvite[(irc.network, channel)] = True
                    # ChanServ will send us an INVITE; doInvite handles it.
                    # We also schedule a fallback in case it doesn't come.
                    self._scheduleJoinRetry(irc, channel)
                    return

        self._scheduleJoinRetry(irc, channel)

    # -----------------------------------------------------------------------
    # Self-unban via MODE -b
    # -----------------------------------------------------------------------

    def _trySelfUnban(self, irc, channel):
        if channel not in irc.state.channels:
            return False
        chanstate = irc.state.channels[channel]
        if irc.nick not in chanstate.ops:
            return False
        try:
            hostmask = irc.state.nickToHostmask(irc.nick)
        except KeyError:
            hostmask = irc.prefix

        matching = [bm for bm in chanstate.bans
                    if ircutils.hostmaskPatternEqual(bm, hostmask)]
        if not matching:
            return False

        self.log.info('Resilience: MODE -b self-unban in %s on %s: %s',
                      channel, irc.network, ', '.join(matching))
        num_modes = irc.state.supported.get('modes', 1) or 1
        for i in range(0, len(matching), num_modes):
            chunk = matching[i:i + num_modes]
            irc.queueMsg(ircmsgs.mode(channel,
                                      ['-' + 'b' * len(chunk)] + chunk))
        return True

    # -----------------------------------------------------------------------
    # Kick rejoin
    # -----------------------------------------------------------------------

    def _scheduleKickRejoin(self, irc, channel):
        key = (irc.network, channel)
        delay = self.registryValue('rejoinKickDelay', channel, irc.network)
        event_name = 'Resilience_kick_%s_%s' % (irc.network, channel)

        def _rejoin():
            self._kickRejoinEvents.pop(key, None)
            if channel not in irc.state.channels:
                self.log.info('Resilience: rejoining %s on %s after kick.',
                              channel, irc.network)
                networkGroup = conf.supybot.networks.get(irc.network)
                irc.queueMsg(networkGroup.channels.join(channel))

        if delay == 0:
            _rejoin()
            return
        name = self._safe_event(_rejoin, time.time() + delay, event_name)
        self._kickRejoinEvents[key] = name

    # -----------------------------------------------------------------------
    # Reop
    # -----------------------------------------------------------------------

    def _scheduleReop(self, irc, channel):
        key = (irc.network, channel)
        delay = self.registryValue('autoReopDelay', channel, irc.network)
        event_name = 'Resilience_reop_%s_%s' % (irc.network, channel)

        def _reop():
            self._reopEvents.pop(key, None)
            if channel not in irc.state.channels:
                return
            chanstate = irc.state.channels[channel]
            if irc.nick in chanstate.ops:
                return  # already opped

            # Try halfop self-op first
            if irc.nick in chanstate.halfops:
                self.log.info('Resilience: reop via halfop in %s on %s.',
                              channel, irc.network)
                irc.queueMsg(ircmsgs.op(channel, irc.nick))
                return

            # Try ChanServ UP
            if self.registryValue('useChanServUp', channel, irc.network):
                if self._cs_command(irc, 'UP', channel):
                    return

            self.log.warning('Resilience: deopped in %s on %s, no way to '
                             'self-reop (no halfop, no ChanServ UP).',
                             channel, irc.network)

        name = self._safe_event(_reop, time.time() + delay, event_name)
        self._reopEvents[key] = name

    # -----------------------------------------------------------------------
    # IRC event handlers
    # -----------------------------------------------------------------------

    def do001(self, irc, msg):
        """Reset stale state on fresh connect."""
        for key in [k for k in self._joinRetryEvents if k[0] == irc.network]:
            name = self._joinRetryEvents.pop(key)
            try:
                schedule.removeEvent(name)
            except KeyError:
                pass
        self._stopNickRecovery(irc)
        self._waitingCsUnban.clear()
        self._waitingCsInvite.clear()

    def do376(self, irc, msg):
        """End of MOTD: fire perform, start nick recovery if needed."""
        self._schedulePerform(irc)
        desired = _desired_nick(irc)
        if not ircutils.strEqual(irc.nick, desired):
            if self.registryValue('recoverNick', network=irc.network):
                self.log.info('Resilience: connected as %s, want %s on %s — '
                              'starting nick recovery.',
                              irc.nick, desired, irc.network)
                self._send_nick_recover_commands(irc)
                self._startNickRecovery(irc)

    do422 = do377 = do376

    # --- Join errors ---

    def do471(self, irc, msg):
        self._handleJoinError(irc, msg, 'full', 'retryOnFull')

    def do473(self, irc, msg):
        self._handleJoinError(irc, msg, 'invite-only', 'retryOnInviteOnly')

    def do474(self, irc, msg):
        self._handleJoinError(irc, msg, 'banned', 'retryOnBan')

    def do475(self, irc, msg):
        self._handleJoinError(irc, msg, 'bad-key', 'retryOnBadKey')

    def doJoin(self, irc, msg):
        if ircutils.strEqual(msg.nick, irc.nick):
            channel = msg.channel
            self._cancelJoinRetry(irc, channel)
            self._waitingCsUnban.pop((irc.network, channel), None)
            self._waitingCsInvite.pop((irc.network, channel), None)
            # ChanServ UP on join
            if self.registryValue('useChanServUp', channel, irc.network):
                self._cs_command(irc, 'UP', channel)

    def doInvite(self, irc, msg):
        """If ChanServ invites us to a channel we're waiting on, join it."""
        channel = msg.args[1]
        cs = self._chanserv(irc)
        if cs and ircutils.strEqual(msg.nick, cs):
            key = (irc.network, channel)
            if key in self._waitingCsInvite:
                self._waitingCsInvite.pop(key)
                self._cancelJoinRetry(irc, channel)
                self.log.info('Resilience: ChanServ invited us to %s on %s, '
                              'joining.', channel, irc.network)
                networkGroup = conf.supybot.networks.get(irc.network)
                irc.queueMsg(networkGroup.channels.join(channel))

    # --- ChanServ NOTICE handler: detect successful unban and rejoin ---

    # Patterns that various ChanServ implementations send after an unban
    _CS_UNBAN_RE = re.compile(
        r'unbanned|all bans|ban.*remov|removed.*ban',
        re.IGNORECASE)
    # Channel name in bold (Anope/Atheme standard)
    _CS_CHAN_RE  = re.compile(r'(\x02)?(#[^\x02\s,]+)(\x02)?')

    def doNotice(self, irc, msg):
        cs = self._chanserv(irc)
        if not cs:
            return
        if not ircutils.strEqual(msg.nick, cs):
            return

        text = msg.args[1]
        # Look for an unban confirmation
        if self._CS_UNBAN_RE.search(ircutils.stripFormatting(text)):
            m = self._CS_CHAN_RE.search(text)
            if m:
                channel = m.group(2)
                key = (irc.network, channel)
                if key in self._waitingCsUnban:
                    self._waitingCsUnban.pop(key)
                    # Cancel the fallback timer and join immediately
                    self._cancelJoinRetry(irc, channel)
                    self.log.info(
                        'Resilience: ChanServ confirmed unban in %s on %s, '
                        'rejoining now.', channel, irc.network)
                    networkGroup = conf.supybot.networks.get(irc.network)
                    irc.queueMsg(networkGroup.channels.join(channel))

    # --- Kick ---

    def doKick(self, irc, msg):
        channel = msg.channel
        if not ircutils.strEqual(msg.args[1], irc.nick):
            return
        if not self.registryValue('rejoinOnKick', channel, irc.network):
            self.log.info('Resilience: kicked from %s on %s, rejoin off.',
                          channel, irc.network)
            return
        self.log.info('Resilience: kicked from %s on %s by %s.',
                      channel, irc.network, msg.nick)
        self._scheduleKickRejoin(irc, channel)

    # --- Deop ---

    def doMode(self, irc, msg):
        channel = msg.args[0]
        if not irc.isChannel(channel):
            return
        if not self.registryValue('autoReop', channel, irc.network):
            return
        for (mode, value) in ircutils.separateModes(msg.args[1:]):
            if mode == '-o' and value and ircutils.strEqual(value, irc.nick):
                self.log.info('Resilience: deopped in %s on %s by %s.',
                              channel, irc.network, msg.nick)
                self._scheduleReop(irc, channel)

    # --- Nick events ---

    def doNick(self, irc, msg):
        network = irc.network
        desired = _desired_nick(irc)

        # Did WE just get our desired nick?
        if ircutils.strEqual(msg.nick, irc.nick):
            if ircutils.strEqual(msg.args[0], desired):
                self._stopNickRecovery(irc)
            return

        # Did someone else vacate our desired nick?
        if ircutils.strEqual(msg.nick, desired) and \
                not ircutils.strEqual(irc.nick, desired):
            if self.registryValue('recoverNick', network=network):
                self.log.info('Resilience: %s freed %s on %s, claiming.',
                              msg.nick, desired, network)
                irc.queueMsg(ircmsgs.nick(desired))

    def doQuit(self, irc, msg):
        network = irc.network
        desired = _desired_nick(irc)
        if ircutils.strEqual(msg.nick, desired) and \
                not ircutils.strEqual(irc.nick, desired):
            if self.registryValue('recoverNick', network=network):
                self.log.info('Resilience: %s quit on %s, reclaiming nick.',
                              desired, network)
                irc.queueMsg(ircmsgs.nick(desired))

    def do433(self, irc, msg):
        """Nick in use — make sure recovery loop is running."""
        if not ircutils.strEqual(irc.nick, _desired_nick(irc)):
            if self.registryValue('recoverNick', network=irc.network):
                self._startNickRecovery(irc)

    # -----------------------------------------------------------------------
    # User-facing commands
    # -----------------------------------------------------------------------

    # --- Perform management ------------------------------------------------

    class perform(callbacks.Commands):
        """
        Manage per-network perform lists.

        The perform list is a comma-separated string of raw IRC commands.
        Substitutions: $nick (desired nick), $botnick, $currentnick,
        $network, $password (from nickPassword config).

        Subcommands: set, show, run, clear
        """

        def set(self, irc, msg, args, network, commands):
            """<network> <command1>, <command2>, ...

            Set the perform list for <network>.  Commands are separated by
            commas.  Use \\, to include a literal comma inside a command.

            Example:
              perform set libera PRIVMSG NickServ :IDENTIFY $password, MODE $nick +ix
            """
            plugin = irc.getCallback('Resilience')
            plugin.setRegistryValue('perform', commands, network=network)
            count = len(_split_commands(commands))
            irc.reply(_('Perform list for %s set (%d command(s)).') % (
                network, count))
        set = wrap(set, ['admin', 'somethingWithoutSpaces', 'text'])

        def show(self, irc, msg, args, network):
            """<network>

            Show the current perform list for <network>.
            """
            plugin = irc.getCallback('Resilience')
            raw = plugin.registryValue('perform', network=network).strip()
            if not raw:
                irc.reply(_('No perform commands set for %s.') % network)
                return
            cmds = _split_commands(raw)
            lines = ['%d: %s' % (i + 1, c) for i, c in enumerate(cmds)]
            irc.reply('; '.join(lines))
        show = wrap(show, ['admin', 'somethingWithoutSpaces'])

        def run(self, irc, msg, args, network):
            """<network>

            Immediately send the perform list for <network> without
            reconnecting.  Useful for testing.
            """
            plugin = irc.getCallback('Resilience')
            target_irc = None
            for obj in world.ircs:
                if obj.network == network:
                    target_irc = obj
                    break
            if target_irc is None:
                irc.error(_('Not connected to %s.') % network, Raise=True)
            raw = plugin.registryValue('perform', network=network).strip()
            if not raw:
                irc.error(_('No perform commands set for %s.') % network,
                          Raise=True)
            plugin._send_command_list(target_irc, raw, label='perform (manual)')
            irc.replySuccess()
        run = wrap(run, ['admin', 'somethingWithoutSpaces'])

        def clear(self, irc, msg, args, network):
            """<network>

            Clear the entire perform list for <network>.
            """
            plugin = irc.getCallback('Resilience')
            plugin.setRegistryValue('perform', '', network=network)
            irc.replySuccess()
        clear = wrap(clear, ['admin', 'somethingWithoutSpaces'])

    # --- Nick recovery commands management ---------------------------------

    class nickrecover(callbacks.Commands):
        """
        Manage per-network nick-recovery commands.

        These are sent when the bot is not using its configured nick.
        Subcommands: set, show, run, clear
        """

        def set(self, irc, msg, args, network, commands):
            """<network> <command1>, <command2>, ...

            Set the nick-recovery command list for <network>.
            Same comma-separated format as perform.

            Example for DALnet:
              nickrecover set dalnet PRIVMSG NickServ :RECOVER $nick $password, PRIVMSG NickServ :RELEASE $nick $password
            """
            plugin = irc.getCallback('Resilience')
            plugin.setRegistryValue('nickRecoverCommands', commands,
                                    network=network)
            count = len(_split_commands(commands))
            irc.reply(_('Nick-recovery list for %s set (%d command(s)).') % (
                network, count))
        set = wrap(set, ['admin', 'somethingWithoutSpaces', 'text'])

        def show(self, irc, msg, args, network):
            """<network>

            Show the current nick-recovery command list for <network>.
            """
            plugin = irc.getCallback('Resilience')
            raw = plugin.registryValue('nickRecoverCommands',
                                       network=network).strip()
            if not raw:
                irc.reply(_('No nick-recovery commands set for %s.') % network)
                return
            cmds = _split_commands(raw)
            lines = ['%d: %s' % (i + 1, c) for i, c in enumerate(cmds)]
            irc.reply('; '.join(lines))
        show = wrap(show, ['admin', 'somethingWithoutSpaces'])

        def run(self, irc, msg, args, network):
            """<network>

            Immediately run the nick-recovery commands for <network>.
            """
            plugin = irc.getCallback('Resilience')
            target_irc = None
            for obj in world.ircs:
                if obj.network == network:
                    target_irc = obj
                    break
            if target_irc is None:
                irc.error(_('Not connected to %s.') % network, Raise=True)
            raw = plugin.registryValue('nickRecoverCommands',
                                       network=network).strip()
            if not raw:
                irc.error(_('No nick-recovery commands set for %s.') % network,
                          Raise=True)
            plugin._send_command_list(target_irc, raw, label='nickRecover (manual)')
            irc.replySuccess()
        run = wrap(run, ['admin', 'somethingWithoutSpaces'])

        def clear(self, irc, msg, args, network):
            """<network>

            Clear the nick-recovery command list for <network>.
            """
            plugin = irc.getCallback('Resilience')
            plugin.setRegistryValue('nickRecoverCommands', '',
                                    network=network)
            irc.replySuccess()
        clear = wrap(clear, ['admin', 'somethingWithoutSpaces'])

    # --- Nick password -----------------------------------------------------

    def nickpassword(self, irc, msg, args, network, password):
        """<network> <password>

        Set the nick password for <network>.  This is stored privately and
        used as $password in perform and nickrecover commands.
        """
        self.setRegistryValue('nickPassword', password, network=network)
        irc.replySuccess()
    nickpassword = wrap(nickpassword,
                        ['admin', 'somethingWithoutSpaces', 'something'])

    # --- Join retry control ------------------------------------------------

    def retrylist(self, irc, msg, args):
        """takes no arguments

        Show all channels currently awaiting a join retry across all networks.
        """
        if not self._joinRetryEvents:
            irc.reply(_('No pending join retries.'))
            return
        entries = sorted('%s @ %s' % (ch, net)
                         for (net, ch) in self._joinRetryEvents)
        irc.reply(', '.join(entries))
    retrylist = wrap(retrylist, ['admin'])

    def retrycancel(self, irc, msg, args, channel):
        """[<channel>]

        Cancel the pending join retry for <channel> on the current network.
        """
        key = (irc.network, channel)
        name = self._joinRetryEvents.pop(key, None)
        if name is not None:
            try:
                schedule.removeEvent(name)
            except KeyError:
                pass
            irc.replySuccess()
        else:
            irc.reply(_('No pending retry for %s on %s.') % (
                channel, irc.network))
    retrycancel = wrap(retrycancel, ['admin', 'validChannel'])

    def retrynow(self, irc, msg, args, channel):
        """[<channel>]

        Cancel any scheduled retry and immediately attempt to join <channel>
        on the current network.
        """
        self._cancelJoinRetry(irc, channel)
        networkGroup = conf.supybot.networks.get(irc.network)
        irc.queueMsg(networkGroup.channels.join(channel))
        irc.noReply()
    retrynow = wrap(retrynow, ['admin', 'validChannel'])

    # --- Nick recovery control ---------------------------------------------

    def claimnick(self, irc, msg, args):
        """takes no arguments

        Immediately attempt to reclaim the configured nick on this network
        and start the periodic recovery loop.
        """
        desired = _desired_nick(irc)
        if ircutils.strEqual(irc.nick, desired):
            irc.reply(_('Already using the configured nick (%s).') % desired)
            return
        self._send_nick_recover_commands(irc)
        irc.queueMsg(ircmsgs.nick(desired))
        if self.registryValue('recoverNick', network=irc.network):
            self._startNickRecovery(irc)
        irc.noReply()
    claimnick = wrap(claimnick, ['admin'])

    # --- Manual reop -------------------------------------------------------

    def reop(self, irc, msg, args, channel):
        """[<channel>]

        Attempt to self-reop in <channel>.  Tries halfop first, then
        ChanServ UP if configured.
        """
        if channel not in irc.state.channels:
            irc.error(_('I am not in %s.') % channel, Raise=True)
        chanstate = irc.state.channels[channel]
        if irc.nick in chanstate.ops:
            irc.reply(_('I already have ops in %s.') % channel)
            return
        if irc.nick in chanstate.halfops:
            irc.queueMsg(ircmsgs.op(channel, irc.nick))
            irc.replySuccess()
            return
        if self.registryValue('useChanServUp', channel, irc.network):
            if self._cs_command(irc, 'UP', channel):
                irc.replySuccess()
                return
        irc.error(_('No halfop and ChanServ UP not available for %s.') % channel,
                  Raise=True)
    reop = wrap(reop, ['admin', 'inChannel'])

    # --- Manual self-unban -------------------------------------------------

    def selfunban(self, irc, msg, args, channel):
        """[<channel>]

        Attempt to remove own ban masks from <channel> via MODE -b (requires
        the bot to currently have ops there).
        """
        if self._trySelfUnban(irc, channel):
            irc.replySuccess()
        else:
            irc.error(
                _('Cannot unban self in %s (no ops or no matching bans).')
                % channel, Raise=True)
    selfunban = wrap(selfunban, ['admin', 'validChannel'])

    # --- Manual ChanServ UP ------------------------------------------------

    def csup(self, irc, msg, args, channel):
        """[<channel>]

        Ask ChanServ to give the bot its channel access flags (UP command).
        Useful on Anope-based networks.
        """
        if self._cs_command(irc, 'UP', channel):
            irc.replySuccess()
        else:
            irc.error(_('ChanServ is not configured for %s.') % irc.network,
                      Raise=True)
    csup = wrap(csup, ['admin', 'inChannel'])


Class = Resilience

# vim:set shiftwidth=4 softtabstop=4 expandtab textwidth=79:
