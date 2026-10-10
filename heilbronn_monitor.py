#!/usr/bin/env python3
"""Read-only slot monitor for three Heilbronn Ausländerbehörde services."""

import argparse
import datetime as dt
import json
import os
import random
import time
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
REALERT_HOURS = float(os.getenv("HBN_REALERT_HOURS", "6"))
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
    body = urllib.parse.urlencode({"chat_id": CHAT, "text": message, "disable_web_page_preview": "true"}).encode()
    request = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=body)
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode())
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected the notification")


def alert_chunks(slots, test=False):
    grouped = {}
    for key, _label, calendar_name, day, clock in slots:
        service_id = int(key.split("|", 1)[0])
        group_key = (service_id, day, calendar_name)
        grouped.setdefault(group_key, set()).add(clock)

    header = "🔔 Heilbronn Ausländerbehörde\nNeue freie Termine verfügbar\n"
    if test:
        header = "🧪 TEST – Beispiel, keine echten Termine\n" + header
    footer = f"\n📲 Termin buchen: {BOOKING_URL}"
    # Keep each Telegram message below its 4096-character limit. If a
    # service section continues in another message, repeat its heading.
    chunks = []
    chunk = header
    for service_id, title in ALERT_SECTIONS:
        entries = [
            (day, calendar_name, clocks)
            for (group_service_id, day, calendar_name), clocks in grouped.items()
            if group_service_id == service_id
        ]
        if not entries:
            continue
        section_heading = f"\n━━━━━━━━━━━━━━━━━━\n{title}\n"
        if len(chunk) + len(section_heading) + len(footer) > 3500 and chunk != header:
            chunks.append(chunk.rstrip() + footer)
            chunk = header
        chunk += section_heading
        for day, calendar_name, clocks in sorted(entries):
            line = (
                f"📅 {format_german_date(day)}\n"
                f"🕘 {', '.join(sorted(clocks))} Uhr · {calendar_name}\n"
            )
            if len(chunk) + len(line) + len(footer) > 3500:
                chunks.append(chunk.rstrip() + footer)
                chunk = header + f"\n━━━━━━━━━━━━━━━━━━\n{title} (Fortsetzung)\n"
            chunk += line
    if chunk != header:
        chunks.append(chunk.rstrip() + footer)
    return chunks


def send_alert(slots, test=False):
    for message in alert_chunks(slots, test=test):
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


def check_once():
    previous = read_state()
    now = time.time()
    current = {}
    new_slots = []
    for service_id, (label, _duration) in SERVICES.items():
        try:
            service_calendars = calendars(service_id)
        except Exception as exc:
            print(f"{label}: calendar lookup failed: {exc}")
            continue
        for calendar_id, calendar_name in service_calendars:
            try:
                days = open_days(service_id, calendar_id)
                for day in days:
                    times = slots_for_day(service_id, calendar_id, day)
                    for clock in times:
                        key = f"{service_id}|{calendar_id}|{day}|{clock}"
                        last_alert = previous.get(key, 0)
                        if now - last_alert >= REALERT_HOURS * 3600:
                            new_slots.append((key, label, calendar_name, day, clock))
                            current[key] = now
                        else:
                            current[key] = last_alert
                    if times:
                        print(f"{label}: {day} {', '.join(times)} ({calendar_name})")
                time.sleep(random.uniform(0.3, 0.8))
            except Exception as exc:
                print(f"{label} / {calendar_name}: availability lookup failed: {exc}")

    if not new_slots:
        print(f"{dt.datetime.now(HEILBRONN_TZ).isoformat(timespec='seconds')} — no new slots")
    else:
        send_alert(new_slots)
        for key, *_rest in new_slots:
            current[key] = now

    # Keep only currently available slots so a slot that disappears and later
    # returns can alert again. Preserve original alert times for cooldowns.
    STATE.write_text(json.dumps(current, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="check once")
    group.add_argument("--daemon", action="store_true", help="check continuously, every 60 seconds by default")
    group.add_argument("--list-services", action="store_true", help="print services visible on the public booking page")
    group.add_argument("--test-alert", action="store_true", help="send a clearly labeled sample Telegram alert")
    args = parser.parse_args()
    if args.list_services:
        list_services()
    elif args.once:
        check_once()
    elif args.test_alert:
        send_test_alert()
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
