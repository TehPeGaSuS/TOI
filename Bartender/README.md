# Bartender — Limnoria Plugin

A fun IRC bartender plugin. Serve drinks to users, buy the whole channel a round, and manage a global drink menu backed by SQLite.

---

## Installation

1. Copy the `Bartender/` folder into your bot's `plugins/` directory.
2. Load the plugin: `!load Bartender`
3. Enable it in a channel: `!config channel plugins.Bartender.enabled True`

---

## Commands

### User commands

| Command | Description |
|---|---|
| `!order <drink>` | Serve yourself a drink |
| `!order <drink> for <nick>` | Serve a drink to someone else in the channel |
| `!round <drink>` | Buy a round for the whole channel |
| `!bartender list` | List all available drinks |
| `!bartender show <drink>` | Show the serve message and aliases for a drink |

### Admin-only commands

These require the `admin` capability.

| Command | Description |
|---|---|
| `!bartender add <name> <serve message>` | Add a new drink |
| `!bartender remove <name>` | Remove a drink and all its aliases |
| `!bartender edit <name> <new serve message>` | Edit a drink's serve message |
| `!bartender alias <drink> <alias>` | Add an alias for a drink |

---

## Serve message tokens

When adding or editing a drink, you can use the following tokens in the serve message:

| Token | Replaced with |
|---|---|
| `$nick` | The nick of whoever ordered |
| `$target` | The intended recipient (same as `$nick` if no `for <nick>` given) |
| `$drink` | The canonical drink name |
| `$channel` | The channel name |

**Example:**
```
!bartender add beer slides a cold pint of $drink down the bar to $target
```
When `Alice` orders `!order beer for Bob`:
> \* YourBot slides a cold pint of beer down the bar to Bob

---

## Configuration

All config values are per-channel. Set them with:
```
!config channel plugins.Bartender.<key> <value>
```

| Key | Default | Description |
|---|---|---|
| `enabled` | `False` | Enable/disable the bar in this channel |
| `cooldown` | `30` | Seconds between `!order` uses (0 = no cooldown) |
| `roundCooldown` | `300` | Seconds between `!round` uses (0 = no cooldown) |
| `roundMessage` | *(see below)* | Template for the `!round` response |

Default `roundMessage`:
```
slides a round of $drink down the bar for everyone in $channel, courtesy of $nick!
```

---

## Behaviour notes

- **Bar closed**: If `enabled` is `False`, any `!order` or `!round` command will be met with: `The bar is closed in #channel.`
- **Cooldowns**: When still on cooldown, the bot stays **silent** (no error message, no spam).
- **Unknown target**: If the target nick is not in the channel, the bot replies: `Alice is not in #channel.`
- **Unknown drink**: Bot replies: `I don't know how to make that. Try !bartender list.`
- **Aliases**: Aliases are global and resolve to their canonical drink name. Removing a drink also removes all its aliases.
- **Database**: A single global `Bartender.db` SQLite file lives in the bot's data directory. The drink menu is shared across all channels and networks.

---

## Quick start example

```irc
!config channel plugins.Bartender.enabled True
!bartender add beer slides a cold pint of $drink down the bar to $target
!bartender add whiskey pours a fine glass of $drink for $target. Neat, just how they like it.
!bartender alias beer lager
!order beer for Alice
!round whiskey
```
