#!/usr/bin/env python3
"""Read-only slot monitor for three Heilbronn Ausländerbehörde services."""

import argparse
import datetime as dt
import html
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://www.etermin.net"
WEBID = "qtermin-stadtheilbronn-abh"
BOOKING_URL = f"{BASE}/{WEBID}"
HEILBRONN_TZ = ZoneInfo("Europe/Berlin")
SERVICES = {
    170652: ("Abholung Aufenthaltserlaubnis", 5),
    144512: ("Verlängerung Aufenthaltserlaubnis", 20),
    144511: ("Erteilung Aufenthaltserlaubnis", 20),
}
ALERT_SECTIONS = (
    (144512, "🔄 Verlängerung des Aufenthaltstitels"),
    (170652, "📦 Abholung des Aufenthaltstitels"),
    (144511, "🆕 Ersterteilung eines Aufenthaltstitels"),
)
GERMAN_WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
GERMAN_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)
INTERVAL = max(60, int(os.getenv("HBN_INTERVAL", "60")))
LOOKAHEAD = int(os.getenv("HBN_LOOKAHEAD", "180"))
TOKEN = os.getenv("HBN_TG_TOKEN", "").strip()
CHAT = os.getenv("HBN_TG_CHAT", "").strip()
STATE = Path(os.getenv("HBN_STATE_FILE", Path(__file__).with_name("state.json")))


def get_json(path, params):
    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; HeilbronnSlotMonitor/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "de,en;q=0.7",
        "Referer": BOOKING_URL,
        "webid": WEBID,
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def list_services():
    for service in get_json("/api/servicegroupservice", {"cache": 1, "w": WEBID, "vlang": "de"}):
        if service.get("s"):
            print(f'{service["sid"]:>8}  {service.get("s")} ({service.get("duration")} min)')


def calendars(service_id):
    data = get_json("/api/calendar", {
        "availablecal": 1, "lang": "de", "serviceid": service_id, "calendarid": "undefined"
    })
    return [(c["calendarid"], c["calendarname"]) for c in data]


def open_days(service_id, calendar_id):
    data = get_json("/api/timeslots", {
        "date": heilbronn_today().isoformat(), "serviceid": service_id,
        "rangesearch": 1, "caching": "false", "capacity": "",
        "duration": SERVICES[service_id][1], "cluster": "false", "slottype": 0,
        "fillcalendarstrategy": 0, "showavcap": "true", "appfuture": LOOKAHEAD,
        "appdeadline": 0, "appdeadlinewm": 0, "msdcm": 0, "calendarid": calendar_id,
    })
    today = heilbronn_today()
    days = set()
    for item in data:
        if item.get("available"):
            day = item.get("start", "")[:10]
            try:
                if dt.date.fromisoformat(day) >= today:
                    days.add(day)
            except ValueError:
                pass
    return sorted(days)


def slots_for_day(service_id, calendar_id, day):
    data = get_json("/api/timeslots", {
        "date": day, "serviceid": service_id, "capacity": 1, "caching": "false",
        "duration": SERVICES[service_id][1], "cluster": "false", "slottype": 0,
        "fillcalendarstrategy": 0, "showavcap": "true", "appfuture": LOOKAHEAD,
        "appdeadline": 0, "msdcm": 0, "appdeadlinewm": 0,
        "tz": "W. Europe Standard Time", "tzaccount": "W. Europe Standard Time",
        "calendarid": calendar_id,
    })
    return sorted({item["start"][11:16] for item in data if item.get("f") and item.get("start")})


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def format_german_date(day):
    date = dt.date.fromisoformat(day)
    weekday = GERMAN_WEEKDAYS[date.weekday()]
    month = GERMAN_MONTHS[date.month - 1]
    return f"{weekday}, {date.day}. {month} {date.year}"


def heilbronn_today():
    return dt.datetime.now(HEILBRONN_TZ).date()


def send_telegram(message):
    if not TOKEN or not CHAT:
        raise RuntimeError("Telegram is not configured; set HBN_TG_TOKEN and HBN_TG_CHAT.")
    reply_markup = {"inline_keyboard": [[{"text": "📅 Jetzt Termin buchen", "url": BOOKING_URL}]]}
    body = urllib.parse.urlencode({
        "chat_id": CHAT,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
    }).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=body)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode())
            if not result.get("ok"):
                raise RuntimeError("Telegram rejected the notification")
    except urllib.error.HTTPError as exc:
        try:
            details = json.loads(exc.read().decode("utf-8", "replace")).get("description", "")
        except (ValueError, AttributeError):
            details = ""
        reason = f": {details}" if details else ""
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}{reason}") from None


def alert_chunks(slots, test=False, current=False):
    grouped = {}
    for key, _label, calendar_name, day, clock in slots:
        service_id = int(key.split("|", 1)[0])
        group_key = (service_id, day, calendar_name)
        grouped.setdefault(group_key, set()).add(clock)

    subtitle = "Jetzt buchbare Termine" if current else "Neue freie Termine gefunden"
    header = f"🔔 <b>Heilbronn · Ausländerbehörde</b>\n<i>{subtitle}</i>\n"
    if test:
        header = "🧪 <b>TEST · Beispieltermine, nicht echt</b>\n" + header
    footer = f"\n\n<i>Stand: {dt.datetime.now(HEILBRONN_TZ).strftime('%H:%M')} Uhr</i>"
    # Arrange calendars under a single date heading. This avoids repeating
    # the same date for every room/calendar while keeping each calendar distinct.
    chunks = []
    chunk = header
    for service_id, title in ALERT_SECTIONS:
        days = sorted({day for (sid, day, _calendar) in grouped if sid == service_id})
        if not days:
            continue
        service_heading = f"\n━━━━━━━━━━━━━━━━━━\n<b>{html.escape(title)}</b>\n"
        if len(chunk) + len(service_heading) + len(footer) > 3500 and chunk != header:
            chunks.append(chunk.rstrip() + footer)
            chunk = header
        chunk += service_heading
        for day_index, day in enumerate(days):
            date = dt.date.fromisoformat(day)
            full_date = format_german_date(day)
            separator = "\n──────────────\n" if day_index else "\n"
            date_heading = f"{separator}📅 <b>{full_date}</b>\n"
            if len(chunk) + len(date_heading) + len(footer) > 3500:
                chunks.append(chunk.rstrip() + footer)
                chunk = header + service_heading
            chunk += date_heading
            calendar_entries = sorted(
                (calendar_name, sorted(clocks))
                for (sid, entry_day, calendar_name), clocks in grouped.items()
                if sid == service_id and entry_day == day
            )
            for calendar_name, clocks in calendar_entries:
                label = html.escape(calendar_name)
                time_lines = [" · ".join(clocks[index:index + 6]) for index in range(0, len(clocks), 6)]
                times = "\n".join(f"<code>{line}</code>" for line in time_lines)
                line = f"• <b>{label}</b> · {len(clocks)} Zeitfenster\n{times}\n\n"
                if len(chunk) + len(line) + len(footer) > 3500:
                    chunks.append(chunk.rstrip() + footer)
                    chunk = header + service_heading + date_heading
                chunk += line
    if chunk != header:
        chunks.append(chunk.rstrip() + footer)
    return chunks


def send_alert(slots, test=False, current=False):
    for message in alert_chunks(slots, test=test, current=current):
        print(message)
        send_telegram(message)


def send_test_alert():
    day = (heilbronn_today() + dt.timedelta(days=7)).isoformat()
    sample_slots = [
        ("144512|sample|day|09:15", SERVICES[144512][0], "Beispiel", day, "09:15"),
        ("144512|sample|day|10:30", SERVICES[144512][0], "Beispiel", day, "10:30"),
        ("170652|sample|day|08:45", SERVICES[170652][0], "Beispiel", day, "08:45"),
        ("170652|sample|day|09:00", SERVICES[170652][0], "Beispiel", day, "09:00"),
        ("144511|sample|day|11:00", SERVICES[144511][0], "Beispiel", day, "11:00"),
    ]
    send_alert(sample_slots, test=True)


def check_once(force_earliest=False):
    previous = read_state()
    now = time.time()
    current = {}
    new_slots = []
    available_slots = []
    for service_id, (label, _duration) in SERVICES.items():
        try:
            service_calendars = calendars(service_id)
        except Exception as exc:
            raise RuntimeError(f"{label}: calendar lookup failed: {exc}") from exc
        for calendar_id, calendar_name in service_calendars:
            try:
                days = open_days(service_id, calendar_id)
                for day in days:
                    times = slots_for_day(service_id, calendar_id, day)
                    for clock in times:
                        key = f"{service_id}|{calendar_id}|{day}|{clock}"
                        slot = (key, label, calendar_name, day, clock)
                        available_slots.append(slot)
                        if key not in previous:
                            new_slots.append(slot)
                            current[key] = now
                        else:
                            current[key] = previous[key]
                    if times:
                        print(f"{label}: {day} {', '.join(times)} ({calendar_name})")
                time.sleep(random.uniform(0.3, 0.8))
            except Exception as exc:
                raise RuntimeError(f"{label} / {calendar_name}: availability lookup failed: {exc}") from exc

    if force_earliest:
        new_slots = [min(available_slots, key=lambda slot: (slot[3], slot[4], slot[0]))] if available_slots else []

    if not new_slots:
        message = "no currently available slots" if force_earliest else "no newly available slots"
        print(f"{dt.datetime.now(HEILBRONN_TZ).isoformat(timespec='seconds')} — {message}")
    else:
        send_alert(new_slots, current=force_earliest)
        for key, *_rest in new_slots:
            if key not in previous:
                current[key] = now

    # Only keep slots visible in this successful scan. If a slot disappears and
    # later returns, its key is new and it will trigger another alert.
    STATE.write_text(json.dumps(current, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="check once")
    group.add_argument("--daemon", action="store_true", help="check continuously, every 60 seconds by default")
    group.add_argument("--list-services", action="store_true", help="print services visible on the public booking page")
    group.add_argument("--test-alert", action="store_true", help="send a clearly labeled sample Telegram alert")
    group.add_argument("--send-earliest-current-slot", action="store_true", help="send one currently bookable slot, regardless of alert cooldown")
    args = parser.parse_args()
    if args.list_services:
        list_services()
    elif args.once:
        check_once()
    elif args.test_alert:
        send_test_alert()
    elif args.send_earliest_current_slot:
        check_once(force_earliest=True)
    else:
        print(f"Monitoring {len(SERVICES)} services every {INTERVAL}s. Stop with Ctrl+C.")
        while True:
            try:
                check_once()
            except Exception as exc:
                print(f"Check failed: {exc}")
            time.sleep(INTERVAL + random.uniform(0, 5))


if __name__ == "__main__":
    main()
