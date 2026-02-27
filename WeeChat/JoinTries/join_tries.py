# join_tries.py — WeeChat script replicating ZNC's JoinTries behavior
#
# Replicates the logic found in ZNC's IRCNetwork.cpp / Chan.cpp:
#   - Tracks how many times WeeChat has attempted to join each channel
#   - On a failed join (banned 474, invite-only 473, full 471, key-needed 475,
#     or a kick that triggers autorejoin), the try-count is incremented
#   - Once the try-count reaches the configured max (default: 10, same as ZNC),
#     the channel is flagged as disabled, a message is printed to the dedicated
#     "join_tries" buffer, and WeeChat stops retrying until you reset it
#   - The try-count resets to 0 on a successful JOIN
#
# Both max_tries and retry_delay are persisted to WeeChat's plugin config
# (plugins.var.python.join_tries.*) and survive restarts and script reloads
# — set them once and forget about them.
#
# Install:
#   Copy to ~/.local/share/weechat/python/autoload/
#   Or load manually with: /script load join_tries.py
#
# Usage:
#   /join_tries                        — show this help
#   /join_tries list                   — show all tracked channels and their state
#   /join_tries reset #chan server     — re-enable a disabled channel and rejoin
#   /join_tries set_max <n>            — set max attempts before giving up (0 = never)
#   /join_tries set_delay <n>          — set seconds between each attempt (default: 30)
#
# SPDX-License-Identifier: MIT

import weechat

SCRIPT_NAME    = "join_tries"
SCRIPT_AUTHOR  = "you"
SCRIPT_VERSION = "1.1"
SCRIPT_LICENSE = "MIT"
SCRIPT_DESC    = (
    "Replicates ZNC's JoinTries behaviour: stop retrying a channel "
    "after N failed join attempts and report it in a dedicated buffer"
)

# { "server/##channel": { "tries": int, "disabled": bool, "hook_rejoin": str|"" } }
channel_state = {}

# Runtime values — loaded from plugin config on startup.
# Mirrors ZNC's m_uMaxJoinTries which defaults to 10.
max_tries   = 10
retry_delay = 30  # seconds between each join attempt

# Buffer pointer for the "join_tries" status buffer. "" means not open yet.
join_tries_buffer = ""

# Config keys stored as plugins.var.python.join_tries.*
CONFIG_MAX_TRIES   = "max_tries"
CONFIG_RETRY_DELAY = "retry_delay"


# ── persistent config ─────────────────────────────────────────────────────────

def _load_int(key, default):
    """Read an integer plugin config value, seeding the default on first run."""
    value = weechat.config_get_plugin(key)
    if value == "":
        weechat.config_set_plugin(key, str(default))
        return default
    try:
        return int(value)
    except ValueError:
        weechat.config_set_plugin(key, str(default))
        return default


def config_load():
    """
    Read all settings from WeeChat's plugin config on startup.
    Missing keys are seeded with their defaults so they appear in
    /set plugins.var.python.join_tries.* straight away.

      plugins.var.python.join_tries.max_tries   — default 10
      plugins.var.python.join_tries.retry_delay — default 30
    """
    global max_tries, retry_delay
    max_tries   = _load_int(CONFIG_MAX_TRIES,   10)
    retry_delay = _load_int(CONFIG_RETRY_DELAY, 30)


def config_save_max_tries():
    """Persist max_tries immediately after it changes."""
    weechat.config_set_plugin(CONFIG_MAX_TRIES, str(max_tries))


def config_save_retry_delay():
    """Persist retry_delay immediately after it changes."""
    weechat.config_set_plugin(CONFIG_RETRY_DELAY, str(retry_delay))


# ── join_tries buffer ─────────────────────────────────────────────────────────

def buffer_open():
    """
    Open (or find) the join_tries status buffer.
    If a buffer from a previous load is still open, reuse it rather than
    creating a duplicate — weechat.buffer_search() handles this.
    """
    global join_tries_buffer

    join_tries_buffer = weechat.buffer_search("python", SCRIPT_NAME)

    if not join_tries_buffer:
        join_tries_buffer = weechat.buffer_new(
            SCRIPT_NAME,
            "buffer_input_cb", "",
            "buffer_close_cb", ""
        )

    if join_tries_buffer:
        weechat.buffer_set(join_tries_buffer, "title",
                           "Join Tries Monitor — /join_tries help")
        # Never appear as unread in the hotlist or trigger highlights
        weechat.buffer_set(join_tries_buffer, "notify", "0")
        weechat.buffer_set(join_tries_buffer, "highlight_words", "-")
        # Don't log this buffer
        weechat.buffer_set(join_tries_buffer, "localvar_set_no_log", "1")


def buffer_input_cb(data, buffer, input_data):
    """The input bar on the join_tries buffer is intentionally a no-op."""
    return weechat.WEECHAT_RC_OK


def buffer_close_cb(data, buffer):
    """
    Called when the user closes the buffer with /buffer close or Ctrl-W.
    We just clear our pointer; buf_print() will reopen it if needed.
    """
    global join_tries_buffer
    join_tries_buffer = ""
    return weechat.WEECHAT_RC_OK


def buf_print(msg):
    """
    Print a line to the join_tries buffer.
    Transparently reopens the buffer if the user previously closed it,
    and falls back to the WeeChat core buffer if creation fails.
    """
    global join_tries_buffer

    if not join_tries_buffer:
        buffer_open()

    if join_tries_buffer:
        weechat.prnt(join_tries_buffer, msg)
    else:
        weechat.prnt(
            "",
            f"{weechat.color('yellow')}[join_tries]{weechat.color('reset')} {msg}"
        )


# ── state helpers ─────────────────────────────────────────────────────────────

def state_key(server, channel):
    return f"{server}/{channel.lower()}"


def get_state(server, channel):
    key = state_key(server, channel)
    if key not in channel_state:
        channel_state[key] = {"tries": 0, "disabled": False, "hook_rejoin": ""}
    return channel_state[key]


def cancel_rejoin_hook(state):
    """Cancel any pending rejoin timer for this channel."""
    if state["hook_rejoin"]:
        weechat.unhook(state["hook_rejoin"])
        state["hook_rejoin"] = ""


def schedule_rejoin(server, channel):
    """Schedule a single rejoin attempt after retry_delay seconds."""
    state = get_state(server, channel)
    cancel_rejoin_hook(state)
    # hook_timer expects (interval_ms, align_second, max_calls, callback, data)
    # all as plain ints — and callback_data must not contain null bytes.
    # We use \t as a separator since server names and channel names never contain it.
    hook = weechat.hook_timer(
        int(retry_delay) * 1000, 0, 1,
        "rejoin_timer_cb",
        f"{server}\t{channel}"
    )
    state["hook_rejoin"] = hook


def tries_display(state):
    """Format the current try count as 'n/max' or 'n/unlimited'."""
    limit = str(max_tries) if max_tries else "unlimited"
    return f"{state['tries']}/{limit}"


def do_rejoin(server, channel):
    """
    Increment the try counter and attempt a JOIN, or disable the channel
    if we've hit the limit. Mirrors ZNC's IRCNetwork::JoinChan() logic:

        if (GetJoinTries() >= JoinTries()) {
            pChan->Disable();
        } else {
            pChan->IncJoinTries();
            ... send JOIN ...
        }
    """
    state = get_state(server, channel)

    if state["disabled"]:
        return  # already disabled, nothing to do

    state["tries"] += 1

    if max_tries != 0 and state["tries"] > max_tries:
        state["disabled"] = True
        state["tries"] = max_tries  # cap for display
        buf_print(f"Couldn't join {channel} on {server}. Disabling it.")
        buf_print(
            f"  Use /join_tries reset {channel} {server} to re-enable."
        )
        # Close the channel buffer — it's useless while banned/disabled
        # and would just sit there as dead weight in the buffer list.
        chan_buf = weechat.buffer_search("irc", f"{server}.{channel}")
        if chan_buf:
            weechat.buffer_close(chan_buf)
        return

    server_buf = weechat.info_get("irc_buffer", server)
    if server_buf:
        # Include the channel key if one is set
        channel_ptr = weechat.info_get("irc_channel", f"{server},{channel}")
        key = ""
        if channel_ptr:
            key = weechat.buffer_get_string(channel_ptr, "localvar_channel_key") or ""
        join_cmd = f"/join -noswitch {channel}" + (f" {key}" if key else "")
        weechat.command(server_buf, join_cmd)
    else:
        buf_print(f"Could not find server buffer for {server} — cannot rejoin {channel}.")


# ── timer callback ────────────────────────────────────────────────────────────

def rejoin_timer_cb(data, remaining_calls):
    server, channel = data.split("\t", 1)
    state = get_state(server, channel)
    state["hook_rejoin"] = ""
    do_rejoin(server, channel)
    return weechat.WEECHAT_RC_OK


# ── IRC numeric callbacks (failed JOINs) ──────────────────────────────────────
#
# Raw IRC line format: ":server NNN mynick #channel :reason"
#
# Numerics handled:
#   471  ERR_CHANNELISFULL     — channel has hit its user limit (+l)
#   473  ERR_INVITEONLYCHAN    — channel is invite-only (+i)
#   474  ERR_BANNEDFROMCHAN    — we are banned (+b)
#   475  ERR_BADCHANNELKEY     — wrong or missing key (+k)
#
# Each failure schedules a retry (using our own retry_delay setting) and
# increments the counter. Once the counter exceeds max_tries the channel
# is disabled and retries stop.

def numeric_failed_join_cb(data, signal, signal_data):
    parts = signal_data.split(" ", 4)
    if len(parts) < 4:
        return weechat.WEECHAT_RC_OK

    numeric      = parts[1]
    channel      = parts[3].lstrip(":")
    server       = signal.split(",")[0]

    if not channel.startswith(("#", "&", "!", "+")):
        return weechat.WEECHAT_RC_OK

    state = get_state(server, channel)
    if state["disabled"]:
        return weechat.WEECHAT_RC_OK

    buf_print(
        f"Cannot join {channel} on {server} "
        f"(numeric {numeric}, attempt {tries_display(state)}) "
        f"— retrying in {retry_delay}s"
    )
    schedule_rejoin(server, channel)
    return weechat.WEECHAT_RC_OK


# ── KICK → autorejoin with try tracking ──────────────────────────────────────
#
# Raw IRC line format: ":kicker!u@h KICK #chan kicked_nick :reason"
#
# We only act here if WeeChat's built-in autorejoin is OFF for this server.
# If it's ON, WeeChat handles the immediate rejoin; if that rejoin then hits
# a 474, our numeric handler above will take over and start counting.

def kick_cb(data, signal, signal_data):
    server = signal.split(",")[0]

    parts = signal_data.split(" ", 4)
    if len(parts) < 4:
        return weechat.WEECHAT_RC_OK

    channel     = parts[2]
    kicked_nick = parts[3].lstrip(":")
    own_nick    = weechat.info_get("irc_nick", server)

    if kicked_nick.lower() != own_nick.lower():
        return weechat.WEECHAT_RC_OK  # not us

    # Defer to WeeChat's built-in autorejoin if enabled
    if weechat.config_boolean(weechat.config_get("irc.server_default.autorejoin")):
        return weechat.WEECHAT_RC_OK

    state = get_state(server, channel)

    buf_print(
        f"Kicked from {channel} on {server} "
        f"— rejoining in {retry_delay}s (attempt {tries_display(state)})"
    )
    schedule_rejoin(server, channel)
    return weechat.WEECHAT_RC_OK


# ── successful JOIN → reset try counter ──────────────────────────────────────
#
# Raw IRC line format: ":nick!u@h JOIN #channel"
# Mirrors ZNC's CChan::Reset() → ResetJoinTries().

def join_cb(data, signal, signal_data):
    server = signal.split(",")[0]

    parts = signal_data.split(" ", 3)
    if len(parts) < 3:
        return weechat.WEECHAT_RC_OK

    channel      = parts[2].lstrip(":")
    joining_nick = parts[0].lstrip(":").split("!")[0]
    own_nick     = weechat.info_get("irc_nick", server)

    if joining_nick.lower() != own_nick.lower():
        return weechat.WEECHAT_RC_OK  # someone else joined

    key = state_key(server, channel)
    if key in channel_state and channel_state[key]["tries"] > 0:
        cancel_rejoin_hook(channel_state[key])
        channel_state[key] = {"tries": 0, "disabled": False, "hook_rejoin": ""}
        buf_print(f"Successfully joined {channel} on {server} — try counter reset.")

    return weechat.WEECHAT_RC_OK


# ── /join_tries command ───────────────────────────────────────────────────────

HELP_TEXT = """\
%(bold)s/join_tries%(bold_off)s — ZNC-style join attempt limiter

Tracks failed JOIN attempts per channel. Once the limit is reached the
channel is disabled and won't be retried until you explicitly reset it.
All output goes to the dedicated "join_tries" buffer.

Both settings are saved to WeeChat's plugin config and survive restarts
and script reloads — set them once and forget about them.

%(bold)sCommands:%(bold_off)s

%(bold)s/join_tries%(bold_off)s
  Show this help text.

%(bold)s/join_tries list%(bold_off)s
  Show every channel currently being tracked, along with its try count,
  whether it has been disabled, and whether a rejoin is pending.

%(bold)s/join_tries reset #channel server%(bold_off)s
  Re-enable a channel that was disabled after hitting the limit, reset
  its try counter to zero, and immediately attempt to join it again.
  Example: /join_tries reset #weechat libera

%(bold)s/join_tries set_max <n>%(bold_off)s
  Set the maximum number of join attempts before a channel is disabled.
  Use 0 for unlimited (never disable).
  Saved to: plugins.var.python.join_tries.max_tries
  Default: 10 (same as ZNC's JoinTries default).
  Example: /join_tries set_max 5

%(bold)s/join_tries set_delay <n>%(bold_off)s
  Set the number of seconds to wait between each join attempt.
  This setting is fully independent — it does not read or affect
  WeeChat's own irc.server_default.autorejoin_delay.
  Saved to: plugins.var.python.join_tries.retry_delay
  Default: 30
  Example: /join_tries set_delay 60
"""


def cmd_cb(data, buffer, args):
    global max_tries, retry_delay
    args = args.strip()
    tokens = args.split()

    # ── /join_tries (no args) or /join_tries help ─────────────────────────
    if not args or tokens[0] in ("help",):
        bold     = weechat.color("bold")
        bold_off = weechat.color("-bold")
        for line in HELP_TEXT.format(bold=bold, bold_off=bold_off).splitlines():
            buf_print(line)
        if join_tries_buffer:
            weechat.buffer_set(join_tries_buffer, "display", "1")
        return weechat.WEECHAT_RC_OK

    # ── /join_tries list ──────────────────────────────────────────────────
    if tokens[0] == "list":
        if not channel_state:
            buf_print("No failed join attempts recorded — all good.")
        else:
            col_red    = weechat.color("red")
            col_yellow = weechat.color("yellow")
            col_reset  = weechat.color("reset")
            buf_print("Tracked channels:")
            for key, st in sorted(channel_state.items()):
                disabled_tag = f" {col_red}[DISABLED]{col_reset}" if st["disabled"] else ""
                pending_tag  = f" {col_yellow}[rejoin pending]{col_reset}" if st["hook_rejoin"] else ""
                limit = str(max_tries) if max_tries else "unlimited"
                buf_print(f"  {key}  tries={st['tries']}/{limit}{disabled_tag}{pending_tag}")
        if join_tries_buffer:
            weechat.buffer_set(join_tries_buffer, "display", "1")
        return weechat.WEECHAT_RC_OK

    # ── /join_tries set_max <n> ───────────────────────────────────────────
    if tokens[0] == "set_max":
        if len(tokens) < 2 or not tokens[1].isdigit():
            buf_print("Usage: /join_tries set_max <n>  (0 = unlimited, never disable)")
            return weechat.WEECHAT_RC_OK
        max_tries = int(tokens[1])
        config_save_max_tries()
        if max_tries:
            buf_print(
                f"Max join tries set to {max_tries}. "
                f"Saved to plugins.var.python.join_tries.{CONFIG_MAX_TRIES}."
            )
        else:
            buf_print(
                "Max join tries set to unlimited (0) — channels will never be disabled. "
                f"Saved to plugins.var.python.join_tries.{CONFIG_MAX_TRIES}."
            )
        return weechat.WEECHAT_RC_OK

    # ── /join_tries set_delay <n> ─────────────────────────────────────────
    if tokens[0] == "set_delay":
        if len(tokens) < 2 or not tokens[1].isdigit() or int(tokens[1]) < 1:
            buf_print("Usage: /join_tries set_delay <n>  (seconds between attempts, minimum 1)")
            return weechat.WEECHAT_RC_OK
        retry_delay = int(tokens[1])
        config_save_retry_delay()
        buf_print(
            f"Retry delay set to {retry_delay}s between each join attempt. "
            f"Saved to plugins.var.python.join_tries.{CONFIG_RETRY_DELAY}."
        )
        return weechat.WEECHAT_RC_OK

    # ── /join_tries reset #channel server ────────────────────────────────
    if tokens[0] == "reset":
        if len(tokens) < 3:
            buf_print("Usage: /join_tries reset #channel server")
            buf_print("Example: /join_tries reset #weechat libera")
            return weechat.WEECHAT_RC_OK
        channel = tokens[1]
        server  = tokens[2]
        key = state_key(server, channel)
        if key in channel_state:
            cancel_rejoin_hook(channel_state[key])
            channel_state[key] = {"tries": 0, "disabled": False, "hook_rejoin": ""}
        buf_print(f"Reset {channel} on {server} — try counter cleared, attempting to join now.")
        server_buf = weechat.info_get("irc_buffer", server)
        if server_buf:
            weechat.command(server_buf, f"/join -noswitch {channel}")
        else:
            buf_print(f"  (Could not find server buffer for {server} — join it manually)")
        return weechat.WEECHAT_RC_OK

    buf_print(f"Unknown subcommand: '{tokens[0]}'. Run /join_tries for help.")
    return weechat.WEECHAT_RC_OK


# ── registration ──────────────────────────────────────────────────────────────

if weechat.register(SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION,
                    SCRIPT_LICENSE, SCRIPT_DESC, "", ""):

    # Load persisted max_tries from plugin config (or seed the default)
    config_load()

    # Open the dedicated status buffer
    buffer_open()

    # Hook failed-join numerics on all servers
    for numeric in ("471", "473", "474", "475"):
        weechat.hook_signal(f"*,irc_in2_{numeric}", "numeric_failed_join_cb", "")

    # Hook KICK for kick-then-banned scenarios
    weechat.hook_signal("*,irc_in2_KICK", "kick_cb", "")

    # Hook successful JOIN to reset counters
    weechat.hook_signal("*,irc_in2_JOIN", "join_cb", "")

    # Register the management command with completion hints
    weechat.hook_command(
        "join_tries",
        "ZNC-style join attempt limiter — stop retrying a channel after N failures",
        "[list] | [reset #channel server] | [set_max <n>] | [set_delay <n>]",
        (
            "list                  : list all tracked channels with try counts and status\n"
            "reset #channel server : re-enable a disabled channel and rejoin immediately\n"
            "set_max <n>           : max attempts before giving up (0 = unlimited, persisted)\n"
            "set_delay <n>         : seconds between each attempt (persisted, default: 30)\n"
            "\n"
            "Run /join_tries with no arguments for full help."
        ),
        "list || reset %(irc_channels) %(irc_servers) || set_max || set_delay",
        "cmd_cb",
        ""
    )

    limit_str = str(max_tries) if max_tries else "unlimited"
    buf_print(
        f"{weechat.color('green')}join_tries v{SCRIPT_VERSION} loaded{weechat.color('reset')} "
        f"— max tries: {limit_str}, retry delay: {retry_delay}s "
        f"(see /join_tries for help)"
    )
