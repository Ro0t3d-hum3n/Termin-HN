# Heilbronn appointment alerts

This read-only monitor checks the public eTermin availability endpoint for:

| Service | eTermin service ID |
|---|---:|
| Abholung Aufenthaltserlaubnis | 170652 |
| Verlängerung Aufenthaltserlaubnis | 144512 |
| Erteilung Aufenthaltserlaubnis | 144511 |

When a bookable slot appears, it sends a Telegram message with the service,
date, time, and booking link. It checks every 60 seconds by default and does
not book, reserve, or cancel appointments. It uses Python's standard library;
no packages need installing.

## Set up Telegram

1. In Telegram, message **@BotFather**, send `/newbot`, and follow its prompts.
   Keep the token BotFather gives you private. Do not send it in this chat or
   commit it to a repository.
2. Open your new bot and press **Start** (or send it a message).
3. On your computer, set the token and your Telegram chat ID in the terminal.
   To find your chat ID, open `https://api.telegram.org/bot<TOKEN>/getUpdates`
   after messaging the bot; read the `message.chat.id` value in the response.

On macOS/Linux, in the terminal where you will run the monitor:

```sh
export HBN_TG_TOKEN='paste-token-here'
export HBN_TG_CHAT='paste-chat-id-here'
```

On Windows PowerShell:

```powershell
$env:HBN_TG_TOKEN = 'paste-token-here'
$env:HBN_TG_CHAT = 'paste-chat-id-here'
```

Never put the token in source code or a public GitHub repository. If it is
exposed, revoke it with BotFather and create a replacement.

## Run

From this folder:

```sh
python3 heilbronn_monitor.py --list-services
python3 heilbronn_monitor.py --once
python3 heilbronn_monitor.py --daemon
```

The first command displays the services the public booking page currently
offers. `--once` checks immediately and exits. `--daemon` checks every minute;
leave that terminal running. Press Ctrl+C to stop it. The monitor writes a
`state.json` file in this folder to avoid repeating alerts for slots that
remain available. A slot that disappears and later reappears can alert again.

Optional settings:

```sh
HBN_INTERVAL=60       # seconds between checks; minimum 60
HBN_LOOKAHEAD=180     # days ahead to query
```

## Run on GitHub Actions (no personal server)

The included workflow runs the monitor every five minutes. GitHub may delay
scheduled runs, so it is less immediate than the local 60-second daemon.

1. In the repository, open **Settings → Secrets and variables → Actions**.
2. Add repository secrets named `HBN_TG_TOKEN` and `HBN_TG_CHAT` with your bot
   token and chat ID. Do not add either value to this README or source code.
3. Open **Actions → Heilbronn appointment monitor** and enable the workflow if
   prompted. Use **Run workflow** for a manual check.

GitHub Actions stores alert state between runs so an available slot does not
trigger a message every five minutes. Scheduled workflows can be paused by
GitHub after long periods without repository activity, so check the Actions
page occasionally.

The eTermin API used here is the public booking page's availability interface,
not the authenticated account management API. The page or interface may
change; if the monitor logs errors, check its service list and booking page.
Polling once per minute is best-effort and does not guarantee a slot will
remain available after the alert arrives.
