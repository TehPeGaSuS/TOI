# autoban.py — WeeChat Autoban Manager

An eggdrop-style autoban manager for WeeChat with per-channel tracking, timed bans, and automatic enforcement on join.

---

## Features

- **Eggdrop-style ban management** — organize bans by nick entry, each with one or more host masks
- **9 configurable ban mask types** — from simple `*!*@host` to nick-specific patterns with wildcard domains
- **Timed bans** — set an expiry in minutes; masks are automatically removed when they expire
- **Per-channel tracking** — bans are lifted only on the channels they were applied to
- **Auto-enforce on join** — any user matching an active mask is immediately banned and kicked when they join
- **Persistent storage** — ban list is saved to JSON and survives restarts

---

## Installation

**Option A — Autoload (recommended):**
```
cp autoban.py ~/.local/share/weechat/python/autoload/
```

**Option B — Load manually in WeeChat:**
```
/python load autoban.py
```

---

## Commands

### `/addban <nick> [mask] [minutes]`
Creates a new nick entry with an initial ban mask.

- If the nick is currently in the channel, the mask is auto-built from their host using the current ban type.
- If the nick is not in the channel, falls back to `nick!*@*` — refine later with `/addhost`.
- Providing a `mask` overrides auto-detection.
- `minutes` sets an expiry (0 or omitted = permanent).
- Immediately bans and kicks any matching users already in the channel.
- **Errors** if the nick entry already exists — use `/addhost` to add more masks.

```
/addban spammer
/addban spammer *!*baduser@*.isp.net
/addban spammer *!*baduser@*.isp.net 60
```

---

### `/addhost <nick> <mask> [minutes]`
Adds an additional host mask to an existing nick entry.

- The nick entry must already exist (created with `/addban`).
- Immediately applies the ban and kicks any matching users in the current channel.

```
/addhost spammer *!*@other.isp.net
/addhost spammer *!*@other.isp.net 30
```

---

### `/delban <nick>`
Removes the entire nick entry and lifts **all** of its masks from every tracked channel.

```
/delban spammer
```

### `/delban <nick> <mask>`
Removes a single mask from a nick entry and lifts it from all tracked channels. If it was the last mask, the nick entry is also removed.

```
/delban spammer *!*baduser@*.isp.net
```

---

### `/listban [nick]`
Without an argument, shows a summary of all nick entries including active mask count and any expired masks.

With a nick argument, shows the full detail for that entry: each mask, its expiry, when it was added, and which channels it applies to.

```
/listban
/listban spammer
```

---

### `/checkban`
Re-applies all active ban masks in the **current channel**. Useful after joining a channel where bans may have been lost (e.g. after a netsplit or bot restart). Also kicks any currently present users who match active masks.

```
/checkban
```

---

### `/bantype [0-9]`
Shows or sets the default ban mask type used when auto-generating masks. With no argument, lists all types with the current one highlighted.

```
/bantype
/bantype 4
```

#### Available Ban Types

| Type | Pattern             | Description                          |
|------|---------------------|--------------------------------------|
| 0    | `*!user@host`       | Any nick, exact user and host        |
| 1    | `*!*user@host`      | Any nick, partial user, exact host   |
| 2    | `*!*@host`          | Any nick and user, exact host        |
| 3    | `*!*user@*.host`    | Any nick, partial user, wildcard domain *(default)* |
| 4    | `*!*@*.host`        | Any nick and user, wildcard domain   |
| 5    | `nick!user@host`    | Exact nick, user, and host           |
| 6    | `nick!*user@host`   | Exact nick, partial user, exact host |
| 7    | `nick!*@host`       | Exact nick, any user, exact host     |
| 8    | `nick!*user@*.host` | Exact nick, partial user, wildcard domain |
| 9    | `nick!*@*.host`     | Exact nick, any user, wildcard domain |

> **Tip:** Type 3 (the default) is usually a good balance — it blocks the user's ident across the whole ISP subnet without being so broad it catches innocent users.

---

## Storage

Bans are stored in:
```
~/.local/share/weechat/autoban.json
```

Example format:
```json
{
  "spammer": {
    "added": "2025-01-01 12:00:00 UTC",
    "masks": {
      "*!*baduser@*.isp.net": {
        "added": "2025-01-01 12:00:00 UTC",
        "expires": 1234567890,
        "channels": ["libera/#channel1", "libera/#channel2"]
      }
    }
  }
}
```

---

## Expiry & Auto-cleanup

The script checks for expired masks every **60 seconds**. When a mask expires:

1. `MODE -b` is sent on every channel the mask was tracked to.
2. The mask is removed from storage.
3. If no masks remain for a nick entry, the entry itself is removed.

---

## Auto-ban on Join

When any user joins a channel the script monitors, their full `nick!user@host` is checked against all active masks. If a match is found, the bot immediately sets `MODE +b` and kicks the user, then records the new channel in the mask's tracking list.

---

## Requirements

- [WeeChat](https://weechat.org/) with Python plugin support
- Python 3

---

## License

MIT — see script header for full details.

**Author:** PeGaSuS  
**Version:** 1.0.0
