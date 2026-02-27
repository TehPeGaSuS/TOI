# join_tries.py

A [WeeChat](https://weechat.org/) script that replicates the join attempt
limiting behaviour of [ZNC](https://znc.in/)'s built-in `JoinTries` setting.

When you're banned from a channel, WeeChat will attempt to rejoin once
(if `irc.server_default.autorejoin` is on) and then give up silently.
ZNC handles this more gracefully — it tracks how many times it has tried,
keeps retrying on a schedule, and after a configurable number of failures
it gives up, disables the channel, and tells you about it. This script
brings that same behaviour to WeeChat.

## How it works

- Watches for failed JOIN numerics on all servers:
  - `474` — you are banned (`+b`)
  - `473` — channel is invite-only (`+i`)
  - `471` — channel is full (`+l`)
  - `475` — wrong or missing key (`+k`)
- On each failure, schedules a retry after `retry_delay` seconds and
  increments a per-channel counter
- Once the counter reaches `max_tries`, the channel is **disabled**:
  - Retries stop
  - The channel buffer is closed
  - A message is printed to the dedicated `join_tries` buffer
- On a successful JOIN, the counter resets to zero automatically
- Rejoins never steal focus from your current buffer (`/join -noswitch`)
- Also handles kick-then-banned scenarios when WeeChat's own autorejoin
  is disabled

All settings are independent from WeeChat's own `autorejoin` configuration.

## Requirements

- WeeChat 3.0 or later
- Python 3 plugin enabled (it is by default)

## Installation

Copy `join_tries.py` to your WeeChat Python scripts directory:

```sh
cp join_tries.py ~/.local/share/weechat/python/autoload/
```

To load it immediately without restarting WeeChat:

```
/script load join_tries.py
```

To load it from a different path:

```
/script load /path/to/join_tries.py
```

## The `join_tries` buffer

All output from the script goes to a dedicated buffer named `join_tries`.
It opens automatically when the script loads. You can switch to it at any
time with:

```
/buffer join_tries
```

If you accidentally close it, it will reopen itself the next time the
script has something to print.

## Commands

### `/join_tries`
Show the full in-client help text.

### `/join_tries list`
Show all channels that have had at least one failed join attempt, along
with their current try count and status. Channels you are happily sitting
in are never listed here.

```
Tracked channels:
  libera/#weechat  tries=3/10  [rejoin pending]
  libera/#python   tries=10/10 [DISABLED]
```

### `/join_tries reset #channel server`
Re-enable a channel that was disabled after hitting the limit. Resets the
try counter to zero and immediately attempts to join.

```
/join_tries reset #weechat libera
```

### `/join_tries set_max <n>`
Set the maximum number of join attempts before a channel is disabled.
Use `0` for unlimited (the script will keep retrying forever, like
WeeChat's default behaviour).

```
/join_tries set_max 5
/join_tries set_max 0
```

Default: `10` — the same as ZNC's `JoinTries` default.

### `/join_tries set_delay <n>`
Set the number of seconds to wait between each join attempt. This is
fully independent from WeeChat's `irc.server_default.autorejoin_delay`
— changing one does not affect the other.

```
/join_tries set_delay 60
```

Default: `30`

## Configuration

Both settings are persisted to WeeChat's plugin config immediately when
changed — no need to manually save anything. They survive restarts and
script reloads.

| Key | Default | Description |
|-----|---------|-------------|
| `plugins.var.python.join_tries.max_tries` | `10` | Max attempts before disabling a channel (0 = unlimited) |
| `plugins.var.python.join_tries.retry_delay` | `30` | Seconds between each attempt |

You can also inspect or change them directly via `/set`:

```
/set plugins.var.python.join_tries.max_tries 5
/set plugins.var.python.join_tries.retry_delay 60
```

Note: changing them via `/set` directly won't take effect until the next
script reload. Use `/join_tries set_max` and `/join_tries set_delay`
for changes to apply immediately.

## Background

ZNC's `JoinTries` logic lives in `IRCNetwork::JoinChan()` in
`src/IRCNetwork.cpp`. Every time the join timer fires for a channel that
hasn't been joined yet, it calls `CChan::IncJoinTries()`. Once
`GetJoinTries() >= JoinTries()`, it calls `CChan::Disable()` and prints a
status message. On a successful join, `CChan::Reset()` calls
`ResetJoinTries()` to clear the counter. This script replicates that flow
using WeeChat's signal hooks and timer API.

## License

MIT
