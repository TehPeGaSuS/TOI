###
# Resilience plugin - config.py
###

import supybot.conf as conf
import supybot.registry as registry
import supybot.ircutils as ircutils
from supybot.i18n import PluginInternationalization
_ = PluginInternationalization('Resilience')


def configure(advanced):
    from supybot.questions import yn
    conf.registerPlugin('Resilience', True)


Resilience = conf.registerPlugin('Resilience')

# ---------------------------------------------------------------------------
# ChanServ integration
# ---------------------------------------------------------------------------

conf.registerNetworkValue(Resilience, 'ChanServ',
    registry.String('ChanServ', _("""
    The nick of ChanServ on this network.  Used for CS UNBAN, CS UP, etc.
    Set to empty string to disable ChanServ integration entirely.
    """)))

conf.registerChannelValue(Resilience, 'useChanServUnban',
    registry.Boolean(True, _("""
    If True, when the bot is banned from a channel (474) it will ask ChanServ
    to unban it (CS UNBAN #channel) before retrying the join.
    Requires supybot.plugins.Resilience.ChanServ to be set.
    """)))

conf.registerChannelValue(Resilience, 'useChanServUp',
    registry.Boolean(False, _("""
    If True, when the bot joins a channel it will ask ChanServ to give it
    ops/halfop/voice via the UP command (CS UP #channel).
    Useful on Anope-based networks.  Requires ChanServ to be configured.
    """)))

conf.registerChannelValue(Resilience, 'useChanServInvite',
    registry.Boolean(True, _("""
    If True, when the bot is refused entry to an invite-only channel (473)
    it will ask ChanServ to invite it (CS INVITE #channel) before retrying.
    Requires supybot.plugins.Resilience.ChanServ to be set.
    """)))

# ---------------------------------------------------------------------------
# Join retry (471 full, 473 invite-only, 474 banned, 475 bad key)
# ---------------------------------------------------------------------------

conf.registerChannelValue(Resilience, 'retryJoin',
    registry.Boolean(True, _("""
    Master switch.  If True, the bot will keep retrying to join a channel
    indefinitely when it receives a join error.  Each error type has its own
    toggle below.
    """)))

conf.registerChannelValue(Resilience, 'retryJoinDelay',
    registry.PositiveInteger(60, _("""
    Seconds to wait between join retry attempts.
    """)))

conf.registerChannelValue(Resilience, 'retryOnBan',
    registry.Boolean(True, _("""
    Retry indefinitely when banned (474).  Requires retryJoin to be True.
    """)))

conf.registerChannelValue(Resilience, 'retryOnFull',
    registry.Boolean(True, _("""
    Retry indefinitely when channel is full (471).  Requires retryJoin.
    """)))

conf.registerChannelValue(Resilience, 'retryOnInviteOnly',
    registry.Boolean(True, _("""
    Retry indefinitely when channel is invite-only (473).  Requires retryJoin.
    """)))

conf.registerChannelValue(Resilience, 'retryOnBadKey',
    registry.Boolean(True, _("""
    Retry indefinitely when channel key is wrong (475).  Requires retryJoin.
    """)))

# ---------------------------------------------------------------------------
# Self-unban via MODE (fallback when no ChanServ, or useChanServUnban=False)
# ---------------------------------------------------------------------------

conf.registerChannelValue(Resilience, 'selfUnban',
    registry.Boolean(True, _("""
    If True and the bot has ops in the channel (e.g. was opped before the ban
    was set), it will issue MODE #chan -b <mask> to remove its own ban before
    rejoining.  Tried before ChanServ if both are enabled.
    """)))

# ---------------------------------------------------------------------------
# Auto-rejoin after kick
# ---------------------------------------------------------------------------

conf.registerChannelValue(Resilience, 'rejoinOnKick',
    registry.Boolean(True, _("""
    Whether the bot automatically rejoins a channel after being kicked.
    """)))

conf.registerChannelValue(Resilience, 'rejoinKickDelay',
    registry.NonNegativeInteger(5, _("""
    Seconds to wait before rejoining after a kick.  0 = immediate.
    """)))

# ---------------------------------------------------------------------------
# Auto-reop
# ---------------------------------------------------------------------------

conf.registerChannelValue(Resilience, 'autoReop',
    registry.Boolean(True, _("""
    If True and the bot is deopped in a channel, it will attempt to reop
    itself via halfop (if it has +h), or via ChanServ UP if useChanServUp
    is also enabled.
    """)))

conf.registerChannelValue(Resilience, 'autoReopDelay',
    registry.NonNegativeInteger(3, _("""
    Seconds to wait before attempting to reop after being deopped.
    """)))

# ---------------------------------------------------------------------------
# Nick password (used for nick-recovery commands and perform substitution)
# ---------------------------------------------------------------------------

conf.registerNetworkValue(Resilience, 'nickPassword',
    registry.String('', _("""
    Password for the bot's nick on this network.  Used in $password
    substitutions inside perform and nickRecoverCommands.
    Stored in the private config file.
    """), private=True))

# ---------------------------------------------------------------------------
# Perform on connect (like ZNC *perform)
# ---------------------------------------------------------------------------

conf.registerNetworkValue(Resilience, 'perform',
    registry.String('', _("""
    Comma-separated list of raw IRC commands to send after connecting and
    receiving the MOTD (376/422).

    Each command is a full IRC protocol string.  Commas separate commands.
    To include a literal comma in a command, escape it as \\, (backslash-comma).

    Substitutions available in every command:
      $nick       — the bot's current nick
      $botnick    — same as $nick
      $network    — the network name
      $password   — supybot.plugins.Resilience.networks.<net>.nickPassword

    Example (set in supybot.conf or via 'config' command):
      supybot.plugins.Resilience.networks.libera.perform =
          PRIVMSG NickServ :IDENTIFY $password, MODE $nick +ix

    Example for DALnet (multiple commands):
      PRIVMSG NickServ :IDENTIFY $nick $password,
      PRIVMSG NickServ :RECOVER $nick $password,
      PRIVMSG NickServ :RELEASE $nick $password
    """)))

conf.registerNetworkValue(Resilience, 'performDelay',
    registry.NonNegativeInteger(2, _("""
    Seconds to wait after MOTD before sending perform commands.
    Useful to let SASL or other auth settle first.  0 = send immediately.
    """)))

# ---------------------------------------------------------------------------
# Nick recovery commands (sent when bot's nick differs from configured nick)
# ---------------------------------------------------------------------------

conf.registerNetworkValue(Resilience, 'nickRecoverCommands',
    registry.String('', _("""
    Comma-separated list of raw IRC commands to send when the bot connects
    (or detects) that it is NOT using its configured nick.  Intended for
    networks that need specific sequences to recover a nick (e.g. DALnet).

    Same substitutions as 'perform': $nick (desired nick), $botnick
    (current nick), $network, $password.

    Example for DALnet:
      PRIVMSG NickServ :RECOVER $nick $password,
      PRIVMSG NickServ :RELEASE $nick $password

    After sending these commands the bot will also attempt a NICK change
    to claim the desired nick (with a short delay set by recoverNickDelay).
    """)))

conf.registerNetworkValue(Resilience, 'recoverNick',
    registry.Boolean(True, _("""
    If True and the bot is not using its configured nick, it will watch for
    the desired nick to become available (via QUIT/NICK) and reclaim it,
    and also run a periodic poll.
    """)))

conf.registerNetworkValue(Resilience, 'recoverNickDelay',
    registry.NonNegativeInteger(30, _("""
    Seconds between periodic nick reclaim attempts while on a fallback nick.
    Set to 0 to only react to QUIT/NICK events (no polling).
    """)))
