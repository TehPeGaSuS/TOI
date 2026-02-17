"""
autoban.py — WeeChat autoban manager (eggdrop-style)

Install:  cp autoban.py ~/.local/share/weechat/python/autoload/
Or load:  /python load autoban.py

Commands:
  /addban  <nick> [mask] [minutes]  Add a new nick entry with an initial mask
  /addhost <nick> <mask> [minutes]  Add a host mask to an existing nick entry;
                                    immediately bans any matching users in channel
  /delban  <nick>                   Remove entire entry + lift all masks from channel
  /delban  <nick> <mask>            Remove one mask + lift it; remove entry if last
  /listban [nick]                   List all entries, or detail one nick
  /checkban                         Re-apply all active bans in current channel
  /bantype [0-9]                    Show or set the default ban mask type

Storage format (autoban.json):
  {
    "spammer": {
      "added": "2025-01-01 12:00:00 UTC",
      "masks": {
        "*!*baduser@*.isp.net": {
          "added": "...",
          "expires": 1234567890,
          "channels": ["server/#channel1", "server/#channel2"]
        }
      }
    }
  }
"""

import weechat
import json
import os
import re
import time

SCRIPT_NAME    = "autoban"
SCRIPT_AUTHOR  = "PeGaSuS"
SCRIPT_VERSION = "1.0.0"
SCRIPT_LICENSE = "MIT"
SCRIPT_DESC    = "Eggdrop-style autoban manager with channel tracking"

BAN_FILE = os.path.join(
    os.environ.get("HOME", "~"),
    ".local", "share", "weechat", "autoban.json"
)

# ---------------------------------------------------------------------------
# Ban mask type builder
# ---------------------------------------------------------------------------

BAN_TYPE_DESCRIPTIONS = {
    0: "*!user@host",
    1: "*!*user@host",
    2: "*!*@host",
    3: "*!*user@*.host",
    4: "*!*@*.host",
    5: "nick!user@host",
    6: "nick!*user@host",
    7: "nick!*@host",
    8: "nick!*user@*.host",
    9: "nick!*@*.host",
}


def build_mask(nick, user, host, ban_type):
    def strip_host(h):
        if re.match(r'^[\d.:a-fA-F]+$', h):
            return h
        parts = h.split(".")
        return "*." + ".".join(parts[1:]) if len(parts) > 2 else h

    u  = user.lstrip("~")
    sh = strip_host(host)
    fh = host

    types = {
        0: "*!{}@{}".format(u, fh),
        1: "*!*{}@{}".format(u, fh),
        2: "*!*@{}".format(fh),
        3: "*!*{}@{}".format(u, sh),
        4: "*!*@{}".format(sh),
        5: "{}!{}@{}".format(nick, u, fh),
        6: "{}!*{}@{}".format(nick, u, fh),
        7: "{}!*@{}".format(nick, fh),
        8: "{}!*{}@{}".format(nick, u, sh),
        9: "{}!*@{}".format(nick, sh),
    }
    return types.get(ban_type, types[3])


def get_ban_type():
    val = weechat.config_get_plugin("ban_type")
    try:
        t = int(val)
        if 0 <= t <= 9:
            return t
    except (ValueError, TypeError):
        pass
    return 3


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_bans():
    if os.path.isfile(BAN_FILE):
        try:
            with open(BAN_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            weechat.prnt("", "[autoban] Failed to load ban file: {}".format(e))
    return {}


def save_bans(bans):
    os.makedirs(os.path.dirname(BAN_FILE), exist_ok=True)
    try:
        with open(BAN_FILE, "w") as f:
            json.dump(bans, f, indent=2)
    except Exception as e:
        weechat.prnt("", "[autoban] Failed to save ban file: {}".format(e))


bans = load_bans()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def color(name):
    return weechat.color(name)

def prnt(buffer, msg):
    weechat.prnt(buffer, "{}[autoban]{} {}".format(color("yellow"), color("reset"), msg))

def prnt_global(msg):
    weechat.prnt("", "{}[autoban]{} {}".format(color("yellow"), color("reset"), msg))

def now_unix():
    return int(time.time())

def timestamp():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def get_current_channel(buffer):
    server  = weechat.buffer_get_string(buffer, "localvar_server")
    channel = weechat.buffer_get_string(buffer, "localvar_channel")
    return server, channel


def nick_lookup(server, channel, nick, ban_type):
    """
    Look up nick in channel nicklist.
    Returns (mask, "nick!user@host") or (None, None).
    """
    infolist = weechat.infolist_get("irc_nick", "", "{},{},{}".format(server, channel, nick))
    if not infolist:
        return None, None
    mask = None
    full = None
    if weechat.infolist_next(infolist):
        host_field = weechat.infolist_string(infolist, "host")
        if host_field and "@" in host_field:
            user, host = host_field.split("@", 1)
            mask = build_mask(nick, user, host, ban_type)
            full = "{}!{}".format(nick, host_field)
    weechat.infolist_free(infolist)
    return mask, full


def scan_channel_for_mask(server, channel, mask):
    """
    Iterate the nicklist of server/channel and return a list of
    (nick, full_hostmask) pairs that match the given ban mask.
    Pure in-memory — no network traffic.
    """
    rx      = irc_mask_to_regex(mask)
    hits    = []
    infolist = weechat.infolist_get("irc_nick", "", "{},{}".format(server, channel))
    if not infolist:
        return hits
    while weechat.infolist_next(infolist):
        n          = weechat.infolist_string(infolist, "name")
        host_field = weechat.infolist_string(infolist, "host")  # user@host
        if n and host_field and "@" in host_field:
            full = "{}!{}".format(n, host_field)
            if rx.match(full):
                hits.append((n, full))
    weechat.infolist_free(infolist)
    return hits


def apply_ban(server, channel, mask):
    buf = weechat.buffer_search("irc", "{}.{}".format(server, channel))
    if buf:
        weechat.command(buf, "/mode {} +b {}".format(channel, mask))

def apply_unban(server, channel, mask):
    buf = weechat.buffer_search("irc", "{}.{}".format(server, channel))
    if buf:
        weechat.command(buf, "/mode {} -b {}".format(channel, mask))

def apply_kick(server, channel, nick, reason="You are banned."):
    buf = weechat.buffer_search("irc", "{}.{}".format(server, channel))
    if buf:
        weechat.command(buf, "/kick {} {} {}".format(channel, nick, reason))


def irc_mask_to_regex(mask):
    escaped = re.escape(mask)
    escaped = escaped.replace(r"\*", ".*").replace(r"\?", ".")
    return re.compile("^{}$".format(escaped), re.IGNORECASE)


def get_all_joined_channels():
    """
    Get all channels the user is currently in.
    Returns list of (server, channel) tuples.
    """
    result = []
    
    # Get all buffers
    infolist = weechat.infolist_get("buffer", "", "")
    if not infolist:
        return result
    
    while weechat.infolist_next(infolist):
        full_name = weechat.infolist_string(infolist, "full_name")
        name = weechat.infolist_string(infolist, "name")
        pointer = weechat.infolist_pointer(infolist, "pointer")
        
        # Look for buffers that are IRC channels based on full_name
        if full_name and full_name.startswith("irc."):
            parts = full_name.split(".", 2)
            if len(parts) == 3:
                _, server, channel = parts
                if channel and (channel.startswith("#") or channel.startswith("&")):
                    nicklist = weechat.infolist_get("nicklist", pointer, "")
                    if nicklist:
                        weechat.infolist_free(nicklist)
                        result.append((server, channel))
        
        # Alternative: check name format directly
        elif name and "." in name:
            parts = name.split(".", 1)
            if len(parts) == 2:
                server, channel = parts
                if channel and (channel.startswith("#") or channel.startswith("&")):
                    nicklist = weechat.infolist_get("nicklist", pointer, "")
                    if nicklist:
                        weechat.infolist_free(nicklist)
                        result.append((server, channel))
    
    weechat.infolist_free(infolist)
    return result


def parse_minutes(token):
    try:
        m = int(token)
        return m if m >= 0 else None
    except (ValueError, TypeError):
        return None


def format_expiry(expires):
    if not expires:
        return "{}permanent{}".format(color("green"), color("reset"))
    remaining = expires - now_unix()
    if remaining <= 0:
        return "{}expired{}".format(color("red"), color("reset"))
    mins, secs = divmod(remaining, 60)
    if mins >= 60:
        h, m = divmod(mins, 60)
        return "{}{}h{}m{}".format(color("cyan"), h, m, color("reset"))
    elif mins > 0:
        return "{}{}m{}s{}".format(color("cyan"), mins, secs, color("reset"))
    else:
        return "{}{}s{}".format(color("red"), secs, color("reset"))


def active_masks_for_nick(nick_entry):
    """Yield (mask, mask_info) for masks that are not yet expired."""
    now = now_unix()
    for mask, minfo in nick_entry.get("masks", {}).items():
        expires = minfo.get("expires", 0)
        if expires and expires <= now:
            continue
        yield mask, minfo


def all_masks_matching(full_hostmask):
    """
    Check full_hostmask against every active mask in every nick entry.
    Returns (nick, mask) for the first match, or (None, None).
    """
    now = now_unix()
    for nick, entry in bans.items():
        for mask, minfo in entry.get("masks", {}).items():
            expires = minfo.get("expires", 0)
            if expires and expires <= now:
                continue
            if irc_mask_to_regex(mask).match(full_hostmask):
                return nick, mask
    return None, None


def parse_mask_and_minutes(tokens):
    """
    Given tokens after <nick>, return (mask_or_None, minutes).
      []              -> (None, 0)
      ["30"]          -> (None, 30)       plain integer = minutes
      ["*!*@x"]       -> ("*!*@x", 0)    mask-like token
      ["*!*@x", "60"] -> ("*!*@x", 60)
    """
    if not tokens:
        return None, 0
    if len(tokens) == 1:
        m = parse_minutes(tokens[0])
        if m is not None:
            return None, m
        return tokens[0], 0
    m = parse_minutes(tokens[-1])
    if m is not None:
        return " ".join(tokens[:-1]), m
    return " ".join(tokens), 0


def enforce_mask_on_channel(buffer, server, channel, mask):
    """
    Scan the channel for users matching mask, ban+kick each one.
    Reports results to buffer.  Returns hit count and updates channel list.
    """
    hits = scan_channel_for_mask(server, channel, mask)
    if not hits:
        prnt(buffer, "  No matching users currently in {}.".format(channel))
        return 0
    
    # Apply the ban
    apply_ban(server, channel, mask)
    
    # Kick matching users
    for hit_nick, hit_full in hits:
        prnt(buffer,
             "  Kicking {}{}{} ({}) — matches {}{}{}".format(
                 color("bold"), hit_nick, color("reset"),
                 hit_full,
                 color("cyan"), mask, color("reset")))
        apply_kick(server, channel, hit_nick)
    
    return len(hits)


# ---------------------------------------------------------------------------
# Expiry timer
# ---------------------------------------------------------------------------

def expire_bans(data, remaining_calls):
    """
    Fires every 60 s. Finds expired masks, sends MODE -b only on channels
    where the ban was actually applied, removes them, saves.
    """
    now = now_unix()
    dirty = False
    expired_count = 0

    for nick in list(bans.keys()):
        entry = bans[nick]
        masks = entry.get("masks", {})
        
        # Find expired masks
        expired_masks = []
        for mask, minfo in masks.items():
            expires = minfo.get("expires", 0)
            if expires and expires <= now:
                expired_masks.append((mask, minfo))
        
        if not expired_masks:
            continue

        expired_count += len(expired_masks)

        # Remove expired masks and send unban only on their tracked channels
        for mask, minfo in expired_masks:
            channels = minfo.get("channels", [])
            
            for channel_info in channels:
                if "/" in channel_info:
                    server, channel = channel_info.split("/", 1)
                    apply_unban(server, channel, mask)
                    
                    # Print in the channel buffer
                    buf = weechat.buffer_search("irc", "{}.{}".format(server, channel))
                    if buf:
                        prnt(buf, "Removed expired ban: {}".format(mask))
            
            # Remove the mask from internal storage
            del masks[mask]
            dirty = True

        # Remove nick entry if no masks left
        if not masks:
            del bans[nick]
            prnt_global("Removed empty nick entry: {}".format(nick))

    if dirty:
        save_bans(bans)
        if expired_count > 0:
            prnt_global("Removed {} expired mask(s) and saved ban list".format(expired_count))

    return weechat.WEECHAT_RC_OK


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_addban(data, buffer, args):
    """
    /addban <nick> [mask] [minutes]
    Creates a NEW nick entry. Errors if nick already exists.
    """
    tokens = args.strip().split()
    if not tokens:
        prnt(buffer, "Usage: /addban <nick> [mask] [minutes]")
        return weechat.WEECHAT_RC_OK

    nick = tokens[0]

    if nick in bans:
        prnt(buffer,
             "Nick {}{}{} is already in the ban list. "
             "Use /addhost {} <mask> to add another host.".format(
                 color("bold"), nick, color("reset"), nick))
        return weechat.WEECHAT_RC_OK

    override, minutes = parse_mask_and_minutes(tokens[1:])

    server, channel = get_current_channel(buffer)
    if not server or not channel:
        prnt(buffer, "Must be run inside an IRC channel buffer.")
        return weechat.WEECHAT_RC_OK

    ban_type = get_ban_type()

    if override:
        mask = override
    else:
        mask, full_id = nick_lookup(server, channel, nick, ban_type)
        if mask is None:
            mask = "{}!*@*".format(nick)
            prnt(buffer,
                 "{}Note:{} '{}' not in channel; "
                 "using fallback mask {}{}{}.  "
                 "Use /addhost to refine later.".format(
                     color("cyan"), color("reset"), nick,
                     color("bold"), mask, color("reset")))

    expires = (now_unix() + minutes * 60) if minutes > 0 else 0

    # Create mask entry with channel tracking
    mask_entry = {
        "added": timestamp(),
        "expires": expires,
        "channels": ["{}/{}".format(server, channel)]  # Track where this ban was applied
    }

    bans[nick] = {
        "added": timestamp(),
        "masks": {mask: mask_entry}
    }
    save_bans(bans)

    expiry_label = "expires in {} min".format(minutes) if expires else "permanent"
    prnt(buffer,
         "Added ban: {}{}{} — mask {}{}{} [{}] [{}{}{}]".format(
             color("bold"), nick, color("reset"),
             color("bold"), mask, color("reset"),
             "manual mask" if override else "auto",
             color("green") if not expires else color("cyan"),
             expiry_label, color("reset")))

    enforce_mask_on_channel(buffer, server, channel, mask)
    return weechat.WEECHAT_RC_OK


def cmd_addhost(data, buffer, args):
    """
    /addhost <nick> <mask> [minutes]
    Adds a host mask to an existing nick entry.
    """
    tokens = args.strip().split()
    if len(tokens) < 2:
        prnt(buffer, "Usage: /addhost <nick> <mask> [minutes]")
        return weechat.WEECHAT_RC_OK

    nick = tokens[0]

    if nick not in bans:
        prnt(buffer,
             "Nick {}{}{} is not in the ban list. "
             "Use /addban first.".format(color("bold"), nick, color("reset")))
        return weechat.WEECHAT_RC_OK

    mask, minutes = parse_mask_and_minutes(tokens[1:])

    if not mask:
        prnt(buffer, "Usage: /addhost <nick> <mask> [minutes]  (mask is required)")
        return weechat.WEECHAT_RC_OK

    server, channel = get_current_channel(buffer)
    if not server or not channel:
        prnt(buffer, "Must be run inside an IRC channel buffer.")
        return weechat.WEECHAT_RC_OK

    expires = (now_unix() + minutes * 60) if minutes > 0 else 0

    # Check if mask already exists
    if mask in bans[nick]["masks"]:
        # Add current channel to existing mask's channel list
        channel_info = "{}/{}".format(server, channel)
        if channel_info not in bans[nick]["masks"][mask].get("channels", []):
            bans[nick]["masks"][mask]["channels"].append(channel_info)
            prnt(buffer, "Added channel {}/{} to existing mask".format(server, channel))
    else:
        # Create new mask entry with channel tracking
        bans[nick]["masks"][mask] = {
            "added": timestamp(),
            "expires": expires,
            "channels": ["{}/{}".format(server, channel)]
        }

    save_bans(bans)

    expiry_label = "expires in {} min".format(minutes) if expires else "permanent"
    prnt(buffer,
         "Added host to {}{}{}: {}{}{} [{}{}{}]".format(
             color("bold"), nick, color("reset"),
             color("bold"), mask, color("reset"),
             color("green") if not expires else color("cyan"),
             expiry_label, color("reset")))

    # Apply ban and kick in current channel
    apply_ban(server, channel, mask)
    enforce_mask_on_channel(buffer, server, channel, mask)

    return weechat.WEECHAT_RC_OK


def cmd_delban(data, buffer, args):
    """
    /delban <nick>          Remove entire entry.
    /delban <nick> <mask>   Remove one mask.
    """
    tokens = args.strip().split(None, 1)
    if not tokens:
        prnt(buffer, "Usage: /delban <nick> [mask]")
        return weechat.WEECHAT_RC_OK

    nick = tokens[0]
    mask = tokens[1] if len(tokens) > 1 else None

    if nick not in bans:
        prnt(buffer, "Nick '{}' not found in ban list.".format(nick))
        return weechat.WEECHAT_RC_OK

    if mask:
        # Single mask removal
        if mask not in bans[nick].get("masks", {}):
            prnt(buffer,
                 "Mask {}{}{} not found under nick {}{}{}.".format(
                     color("bold"), mask, color("reset"),
                     color("bold"), nick, color("reset")))
            return weechat.WEECHAT_RC_OK

        # Get channels where this mask was applied
        channels = bans[nick]["masks"][mask].get("channels", [])
        
        # Remove ban from all tracked channels
        for channel_info in channels:
            if "/" in channel_info:
                server, channel = channel_info.split("/", 1)
                apply_unban(server, channel, mask)

        # Remove the mask
        del bans[nick]["masks"][mask]

        remaining = len(bans[nick]["masks"])
        if remaining == 0:
            del bans[nick]
            save_bans(bans)
            prnt(buffer,
                 "Removed mask {}{}{} from {}{}{} — "
                 "entry deleted, no masks remaining.".format(
                     color("bold"), mask, color("reset"),
                     color("bold"), nick, color("reset")))
        else:
            save_bans(bans)
            prnt(buffer,
                 "Removed mask {}{}{} from {}{}{}. "
                 "{} mask(s) remaining.".format(
                     color("bold"), mask, color("reset"),
                     color("bold"), nick, color("reset"),
                     remaining))
    else:
        # Full nick entry removal
        masks = list(bans[nick].get("masks", {}).items())
        del bans[nick]
        save_bans(bans)

        # Remove all bans from their tracked channels
        for mask_name, mask_info in masks:
            channels = mask_info.get("channels", [])
            for channel_info in channels:
                if "/" in channel_info:
                    server, channel = channel_info.split("/", 1)
                    apply_unban(server, channel, mask_name)

        prnt(buffer,
             "Removed ban for {}{}{} — {} mask(s) lifted.".format(
                 color("bold"), nick, color("reset"),
                 len(masks)))

    return weechat.WEECHAT_RC_OK


def cmd_listban(data, buffer, args):
    """
    /listban         Summary of all entries.
    /listban <nick>  Detailed mask list for that nick.
    """
    arg = args.strip()

    if not bans:
        prnt(buffer, "Autoban list is empty.")
        return weechat.WEECHAT_RC_OK

    if arg:
        if arg not in bans:
            prnt(buffer, "Nick '{}' not found in ban list.".format(arg))
            return weechat.WEECHAT_RC_OK
        entry = bans[arg]
        masks = entry.get("masks", {})
        prnt(buffer,
             "Ban entry: {}{}{}  added: {}  {} mask(s)".format(
                 color("bold"), arg, color("reset"),
                 entry.get("added", "?"), len(masks)))
        for m, minfo in sorted(masks.items()):
            channels = minfo.get("channels", [])
            channel_str = ", ".join(channels) if channels else "unknown"
            prnt(buffer,
                 "  {}{:<44}{}  {}  added: {}  [{}]".format(
                     color("bold"), m, color("reset"),
                     format_expiry(minfo.get("expires", 0)),
                     minfo.get("added", "?"),
                     channel_str))
        return weechat.WEECHAT_RC_OK

    # Summary - with the added hint about using /listban <nick>
    current_type = get_ban_type()
    total_masks  = sum(len(e.get("masks", {})) for e in bans.values())
    prnt(buffer,
         "{} nick(s), {} mask(s) total  "
         "[default type: {}{}{} = {}]  "
         "(use /listban <nick> for details)".format(
             len(bans), total_masks,
             color("cyan"), current_type, color("reset"),
             BAN_TYPE_DESCRIPTIONS[current_type]))

    for nick, entry in sorted(bans.items()):
        masks   = entry.get("masks", {})
        now     = now_unix()
        active  = sum(1 for mi in masks.values()
                      if not mi.get("expires", 0) or mi["expires"] > now)
        expired = len(masks) - active
        expired_str = ("  {}({} expired){}".format(color("red"), expired, color("reset"))
                       if expired else "")
        prnt(buffer,
             "  {}{:<20}{}  {} mask(s){}".format(
                 color("bold"), nick, color("reset"),
                 active, expired_str))

    return weechat.WEECHAT_RC_OK


def cmd_checkban(data, buffer, args):
    server, channel = get_current_channel(buffer)
    if not server or not channel:
        prnt(buffer, "Must be run inside an IRC channel buffer.")
        return weechat.WEECHAT_RC_OK

    if not bans:
        prnt(buffer, "Autoban list is empty; nothing to apply.")
        return weechat.WEECHAT_RC_OK

    count = 0
    for nick, entry in bans.items():
        for mask, minfo in active_masks_for_nick(entry):
            apply_ban(server, channel, mask)
            
            # Add current channel to mask's channel list if not already there
            channel_info = "{}/{}".format(server, channel)
            if "channels" not in minfo:
                minfo["channels"] = []
            if channel_info not in minfo["channels"]:
                minfo["channels"].append(channel_info)
            
            hits = scan_channel_for_mask(server, channel, mask)
            for hit_nick, hit_full in hits:
                prnt(buffer,
                     "  Kicking {}{}{} ({}) — matches {}{}{} (entry: {}{}{})".format(
                         color("bold"), hit_nick, color("reset"),
                         hit_full,
                         color("cyan"), mask, color("reset"),
                         color("bold"), nick, color("reset")))
                apply_kick(server, channel, hit_nick)
            count += 1
    
    if count > 0:
        save_bans(bans)
    prnt(buffer, "Re-applied {} active mask(s) in {}.".format(count, channel))
    return weechat.WEECHAT_RC_OK


def cmd_bantype(data, buffer, args):
    arg = args.strip()
    if not arg:
        current = get_ban_type()
        prnt(buffer,
             "Current ban type: {}{}{} = {}{}{}".format(
                 color("bold"), current, color("reset"),
                 color("cyan"), BAN_TYPE_DESCRIPTIONS[current], color("reset")))
        prnt(buffer, "Available types:")
        for t, desc in BAN_TYPE_DESCRIPTIONS.items():
            marker = ("  {}<- current{}".format(color("green"), color("reset"))
                      if t == current else "")
            prnt(buffer, "  {}{}{}  {}{}".format(
                color("bold"), t, color("reset"), desc, marker))
        return weechat.WEECHAT_RC_OK

    try:
        new_type = int(arg)
        if not 0 <= new_type <= 9:
            raise ValueError
    except ValueError:
        prnt(buffer, "Invalid type '{}'. Must be an integer 0-9.".format(arg))
        return weechat.WEECHAT_RC_OK

    weechat.config_set_plugin("ban_type", str(new_type))
    prnt(buffer,
         "Ban type set to {}{}{} = {}{}{}".format(
             color("bold"), new_type, color("reset"),
             color("cyan"), BAN_TYPE_DESCRIPTIONS[new_type], color("reset")))
    return weechat.WEECHAT_RC_OK

def cmd_help_autoban(data, buffer, args):
    """Master help command for autoban"""
    cmd = args.strip()
    
    if not cmd:
        prnt(buffer, "Autoban commands:")
        prnt(buffer, "  /addban    - Add a new nick entry with initial mask")
        prnt(buffer, "  /addhost   - Add a host mask to an existing nick entry")
        prnt(buffer, "  /delban    - Remove a nick entry or single mask")
        prnt(buffer, "  /listban   - List all entries or details for one nick")
        prnt(buffer, "  /checkban  - Re-apply all active bans in current channel")
        prnt(buffer, "  /bantype   - Show or set default ban mask type (0-9)")
        prnt(buffer, "")
        prnt(buffer, "For detailed help on a specific command, use /help <command>")
        return weechat.WEECHAT_RC_OK
    
# ---------------------------------------------------------------------------
# Signal hooks
# ---------------------------------------------------------------------------

def on_join(data, signal, signal_data):
    match = re.match(r":([^!]+)!([^@]+)@(\S+)\s+JOIN\s+:?(\S+)", signal_data)
    if not match:
        return weechat.WEECHAT_RC_OK

    joining_nick, user, host, channel = match.groups()
    full_mask = "{}!{}@{}".format(joining_nick, user, host)
    server    = signal.split(",")[0]

    matched_nick, matched_mask = all_masks_matching(full_mask)
    if matched_nick:
        buf = weechat.buffer_search("irc", "{}.{}".format(server, channel))
        if buf:
            prnt(buf,
                 "Autobanning {}{}{} ({}) — matches {}{}{} (entry: {}{}{})".format(
                     color("bold"), joining_nick, color("reset"),
                     full_mask,
                     color("cyan"), matched_mask, color("reset"),
                     color("bold"), matched_nick, color("reset")))
            apply_ban(server, channel, matched_mask)
            
            # Add this channel to the mask's channel list
            if matched_nick in bans and matched_mask in bans[matched_nick]["masks"]:
                channel_info = "{}/{}".format(server, channel)
                if "channels" not in bans[matched_nick]["masks"][matched_mask]:
                    bans[matched_nick]["masks"][matched_mask]["channels"] = []
                if channel_info not in bans[matched_nick]["masks"][matched_mask]["channels"]:
                    bans[matched_nick]["masks"][matched_mask]["channels"].append(channel_info)
                    save_bans(bans)
            
            apply_kick(server, channel, joining_nick)

    return weechat.WEECHAT_RC_OK


# ---------------------------------------------------------------------------
# Script init
# ---------------------------------------------------------------------------

if __name__ == "__main__" and weechat.register(SCRIPT_NAME, SCRIPT_AUTHOR, SCRIPT_VERSION,
                    SCRIPT_LICENSE, SCRIPT_DESC, "", ""):
    # Set default ban_type if not set
    if not weechat.config_is_set_plugin("ban_type"):
        weechat.config_set_plugin("ban_type", "3")

    # Register commands
    # Register master help command
    weechat.hook_command(
        "autoban",
        "Autoban script - Eggdrop-style ban manager",
        "[addban|addhost|delban|listban|checkban|bantype]",
        "  addban   : Add a new nick entry with initial mask\n"
        "  addhost  : Add a host mask to an existing nick entry\n"
        "  delban   : Remove a nick entry or single mask\n"
        "  listban  : List all entries or details for one nick\n"
        "  checkban : Re-apply all active bans in current channel\n"
        "  bantype  : Show or set default ban mask type (0-9)\n\n"
        "For detailed help on a specific command, use /help <command>\n"
        "Example: /help addban",
        "",
        "",
        ""
    )

    weechat.hook_command(
        "addban",
        "Add a new nick entry to the autoban list with an initial ban mask",
        "<nick> [mask] [minutes]",
        "  nick    : the IRC nick to ban (becomes the entry key)\n"
        "  mask    : ban mask; auto-built from nick's host if in channel,\n"
        "            or nick!*@* if not. Format set by /bantype.\n"
        "  minutes : expiry; 0 or omitted = permanent\n\n"
        "Errors if nick already exists — use /addhost to add more masks.\n"
        "Immediately bans any matching users already in the channel.",
        "",
        "cmd_addban",
        ""
    )

    weechat.hook_command(
        "addhost",
        "Add a host mask to an existing nick entry",
        "<nick> <mask> [minutes]",
        "  nick    : existing nick entry (must exist, created with /addban)\n"
        "  mask    : ban mask to add, e.g. *!*@other.isp.net\n"
        "  minutes : expiry; 0 or omitted = permanent\n\n"
        "Scans the current channel and immediately bans+kicks any matching users.",
        "",
        "cmd_addhost",
        ""
    )

    weechat.hook_command(
        "delban",
        "Remove a nick entry or a single mask from the autoban list",
        "<nick> [mask]",
        "  nick only : remove the entire entry and lift ALL its masks\n"
        "  nick mask : remove just that mask; remove entry if it was the last\n\n"
        "Sends MODE -b on all channels where the mask was applied.",
        "",
        "cmd_delban",
        ""
    )

    weechat.hook_command(
        "listban",
        "List all autoban entries, or show details for one nick",
        "[nick]",
        "  (no argument) : summary table of all nick entries\n"
        "  nick          : detailed mask list with per-mask expiry and channels",
        "",
        "cmd_listban",
        ""
    )

    weechat.hook_command(
        "checkban",
        "Re-apply all active ban masks in the current channel",
        "",
        "Sets MODE +b for every active mask and kicks any matching users.\n"
        "Also updates channel tracking for each mask.",
        "",
        "cmd_checkban",
        ""
    )

    weechat.hook_command(
        "bantype",
        "Show or set the default ban mask type (eggdrop-style, 0-9)",
        "[0-9]",
        "  0  *!user@host           5  nick!user@host\n"
        "  1  *!*user@host          6  nick!*user@host\n"
        "  2  *!*@host              7  nick!*@host\n"
        "  3  *!*user@*.host (def)  8  nick!*user@*.host\n"
        "  4  *!*@*.host            9  nick!*@*.host",
        "",
        "cmd_bantype",
        ""
    )

    # Register signal and timer hooks
    weechat.hook_signal("*,irc_in2_join", "on_join", "")
    weechat.hook_timer(60 * 1000, 0, 0, "expire_bans", "")

    # Display startup message
    total_masks = sum(len(e.get("masks", {})) for e in bans.values())
    current_type = get_ban_type()
    weechat.prnt(
        "",
        "{}[autoban]{} loaded — {} nick(s), {} mask(s), "
        "ban type: {}{}{} ({}), storage: {}".format(
            weechat.color("green"), weechat.color("reset"),
            len(bans), total_masks,
            weechat.color("cyan"), current_type, weechat.color("reset"),
            BAN_TYPE_DESCRIPTIONS[current_type],
            BAN_FILE)
    )
