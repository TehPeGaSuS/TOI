# Bartender — Limnoria Plugin

A fun IRC bartender plugin. Serve drinks to users, buy the whole channel a round, and manage a per-channel drink menu backed by SQLite.

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
| `!order <drink> <nick>` | Serve a drink to someone else in the channel |
| `!round <drink>` | Buy a round for the whole channel |
| `!bartender list` | List all drinks in this channel's menu |
| `!bartender show <drink>` | Show the serve message and aliases for a drink |

### Admin-only commands

These require the `admin` capability. All commands operate on the **current channel's** menu.

| Command | Description |
|---|---|
| `!bartender add <n> [<serve message>]` | Add a new drink, optionally with a custom serve message |
| `!bartender remove <n>` | Remove a drink and all its aliases |
| `!bartender edit <n> <new serve message>` | Edit a drink's serve message |
| `!bartender alias <drink> <alias>` | Add an alias for a drink |

---

## Ordering syntax

The last word of `!order` is always the target nick. Single word = self-order:

```
!order beer                      → serves yourself
!order beer Alice                → serves Alice
!order shot of tequila Alice     → serves Alice a shot of tequila
```

No quotes needed when ordering. Quotes are only required in admin commands
when the drink **name** itself contains spaces:

```
!bartender add "shot of tequila"
!bartender remove "shot of tequila"
```

As an alternative, use a single-word key with the full drink name in the
serve message — no quotes ever needed:

```
!bartender add tequila pours $target a shot of tequila$courtesy.
```

---

## Serve message tokens

| Token | Expands to |
|---|---|
| `$nick` | The nick of whoever ordered |
| `$target` | The recipient (same as `$nick` on a self-order) |
| `$drink` | The canonical drink name |
| `$channel` | The channel name |
| `$courtesy` | `, courtesy of $nick` when ordering for someone else; empty string on self-order |

`$courtesy` goes right before the closing punctuation:

```
!bartender add beer serves $target a beer$courtesy.

!order beer
* YourBot serves Nick a beer.

!order beer Alice
* YourBot serves Alice a beer, courtesy of Nick.
```

---

## Default serve messages

When a drink is added without a custom serve message, the bot picks one of
two default templates depending on who is being served:

| Situation | Config key | Default value |
|---|---|---|
| Self-order | `defaultServeMessage` | `serves $target a $drink.` |
| Ordering for someone else | `defaultServeMessageFor` | `serves $target a $drink, courtesy of $nick.` |

If a drink has a custom serve message, that message is always used as-is —
use `$courtesy` in it to handle both cases.

---

## Configuration

All config values are **per-channel** with a global fallback. Set the global
default with:
```
!config plugins.Bartender.<key> <value>
```
Override for a specific channel with:
```
!config channel plugins.Bartender.<key> <value>
```

| Key | Default | Description |
|---|---|---|
| `enabled` | `False` | Enable/disable the bar in this channel |
| `cooldown` | `30` | Seconds between `!order` uses (0 = no cooldown) |
| `roundCooldown` | `300` | Seconds between `!round` uses (0 = no cooldown) |
| `defaultServeMessage` | `serves $target a $drink.` | Default template for self-orders |
| `defaultServeMessageFor` | `serves $target a $drink, courtesy of $nick.` | Default template when ordering for someone else |
| `roundMessage` | *(see below)* | Template for the `!round` response |

Default `roundMessage`:
```
slides a round of $drink down the bar for everyone in $channel, courtesy of $nick!
```

---

## Per-channel menus and multilingual support

Each channel has its own independent drink menu stored in a separate SQLite
file. This means `#english-chan` and `#canal-br` can have completely different
drinks and serve messages:

```
# in #canal-br
!config channel plugins.Bartender.defaultServeMessage serve $target uma $drink$courtesy.
!config channel plugins.Bartender.defaultServeMessageFor serve $target uma $drink, cortesia de $nick.
!bartender add cerveja
!bartender add cachaça serve $target uma dose de cachaça$courtesy.

# in #english-chan  
!bartender add beer
!bartender add whiskey pours $target a glass of whiskey$courtesy.
```

---

## Behaviour notes

- **Bar closed**: If `enabled` is `False`, any `!order` or `!round` replies with: `The bar is closed in #channel.`
- **Cooldowns**: Silent when on cooldown — no error, no spam.
- **Unknown target**: `Sorry Nick, I don't see a customer with the name Alice in #channel.`
- **Unknown drink**: `I don't know how to make that. Try !bartender list.`
- **All responses** to `!order` and `!round` are sent as IRC `/me` actions.
- **Aliases**: Per-channel. Removing a drink also removes all its aliases.
- **Database**: One `Bartender_#channel.db` SQLite file per channel, stored in the bot's data directory.

---

## Quick start

```irc
!config channel plugins.Bartender.enabled True

!bartender add beer
!bartender add tequila pours $target a shot of tequila$courtesy.
!bartender add whiskey pours $target a glass of whiskey$courtesy.
!bartender alias whiskey bourbon
!bartender alias whiskey scotch

!order beer
* YourBot serves Nick a beer.

!order tequila Alice
* YourBot pours Alice a shot of tequila, courtesy of Nick.

!order whiskey Bob
* YourBot pours Bob a glass of whiskey, courtesy of Nick.

!round beer
* YourBot slides a round of beer down the bar for everyone in #channel, courtesy of Nick!
```
