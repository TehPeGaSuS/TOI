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
| `!bartender add <name> [<serve message>]` | Add a new drink, optionally with a custom serve message |
| `!bartender remove <name>` | Remove a drink and all its aliases |
| `!bartender edit <name> <new serve message>` | Edit a drink's serve message |
| `!bartender alias <drink> <alias>` | Add an alias for a drink |

Multi-word drink names must be quoted:
```
!bartender add "pint of beer"
!bartender add "shot of tequila" pours $target a shot of tequila$courtesy.
```

---

## Serve message tokens

When adding or editing a drink's custom serve message, the following tokens are available:

| Token | Expands to |
|---|---|
| `$nick` | The nick of whoever ordered |
| `$target` | The recipient (same as `$nick` if no `for <nick>` given) |
| `$drink` | The canonical drink name |
| `$channel` | The channel name |
| `$courtesy` | `, courtesy of $nick` when ordering for someone else; empty string when self-ordering |

The `$courtesy` token is the key to making a single custom message handle both cases cleanly:

```
!bartender add "pint of beer" serves $target a pint of beer$courtesy.

!order "pint of beer"
* YourBot serves Nick a pint of beer.

!order "pint of beer" for Alice
* YourBot serves Alice a pint of beer, courtesy of Nick.
```

---

## Default serve messages

When a drink is added **without** a custom serve message, the bot picks one of two global default templates depending on who is being served:

| Situation | Config key | Default value |
|---|---|---|
| Ordering for yourself | `plugins.Bartender.defaultServeMessage` | `serves $target a $drink.` |
| Ordering for someone else | `plugins.Bartender.defaultServeMessageFor` | `serves $target a $drink, courtesy of $nick.` |

Both defaults can be changed globally with:
```
!config plugins.Bartender.defaultServeMessage <new template>
!config plugins.Bartender.defaultServeMessageFor <new template>
```

If a drink **does** have a custom serve message, that message is always used as-is — the `$courtesy` token inside it handles the self vs for-someone-else distinction.

---

## Configuration

### Global (applies across all channels and networks)

| Key | Default | Description |
|---|---|---|
| `plugins.Bartender.defaultServeMessage` | `serves $target a $drink.` | Default template for self-orders |
| `plugins.Bartender.defaultServeMessageFor` | `serves $target a $drink, courtesy of $nick.` | Default template when ordering for someone else |

### Per-channel

Set with `!config channel plugins.Bartender.<key> <value>`:

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

- **Bar closed**: If `enabled` is `False`, any `!order` or `!round` command replies with: `The bar is closed in #channel.`
- **Cooldowns**: When still on cooldown the bot stays **silent** — no error, no spam.
- **Unknown target**: If the target nick is not in the channel: `Alice is not in #channel.`
- **Unknown drink**: `I don't know how to make that. Try !bartender list.`
- **All responses** to `!order` and `!round` are sent as IRC `/me` actions.
- **Aliases**: Global and resolved to their canonical drink name. Removing a drink also removes all its aliases.
- **Database**: A single global `Bartender.db` SQLite file in the bot's data directory. The drink menu is shared across all channels and networks.

---

## Quick start example

```irc
!config channel plugins.Bartender.enabled True

!bartender add beer
!bartender add "pint of beer" serves $target a pint of beer$courtesy.
!bartender add whiskey pours $target a glass of whiskey$courtesy.
!bartender alias whiskey bourbon
!bartender alias whiskey scotch

!order beer
* YourBot serves Nick a beer.

!order "pint of beer" for Alice
* YourBot serves Alice a pint of beer, courtesy of Nick.

!order whiskey for Bob
* YourBot pours Bob a glass of whiskey, courtesy of Nick.

!round beer
* YourBot slides a round of beer down the bar for everyone in #channel, courtesy of Nick!
```
