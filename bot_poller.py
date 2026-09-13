#!/usr/bin/env python3
"""
متابعة أوامر بوت Telegram: يعرض تقويمًا لاختيار تاريخ من الأشهر السابقة
ويرسل عدد ذلك اليوم عند اختياره.

يعمل بأسلوب "الاستطلاع" (polling): يُستدعى دوريًا عبر GitHub Actions
(كل بضع دقائق)، يفحص الرسائل الجديدة عبر getUpdates، يردّ عليها، ثم
يحفظ آخر update_id تمت معالجته في bot_state.json حتى لا تُعاد معالجة
نفس الرسائل في المرة القادمة.

لا يعتمد على الخطة البديلة عبر Playwright عمدًا (لإبقاء هذا الفحص
الدوري سريعًا وخفيفًا)؛ يعتمد فقط على رابط التحميل المباشر.
"""

import calendar
import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

import requests

import download_and_send as dl

STATE_FILE = "bot_state.json"
SAUDI_TZ = ZoneInfo("Asia/Riyadh")

ARABIC_MONTHS = [
    "", "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
]
ARABIC_WEEKDAYS = ["أحد", "اثنين", "ثلاثاء", "أربعاء", "خميس", "جمعة", "سبت"]

CALENDAR_PROMPT = "اختر تاريخ العدد الذي تريد إرساله:"


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_update_id": 0}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def api_url(method: str) -> str:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    return f"https://api.telegram.org/bot{token}/{method}"


def build_calendar_keyboard(year: int, month: int) -> dict:
    today = datetime.datetime.now(SAUDI_TZ).date()
    cal = calendar.Calendar(firstweekday=6)  # الأسبوع يبدأ بالأحد
    weeks = cal.monthdayscalendar(year, month)

    rows = [
        [{"text": f"{ARABIC_MONTHS[month]} {year}", "callback_data": "noop"}],
        [{"text": d, "callback_data": "noop"} for d in ARABIC_WEEKDAYS],
    ]

    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append({"text": " ", "callback_data": "noop"})
                continue
            d = datetime.date(year, month, day)
            if d > today:
                row.append({"text": "·", "callback_data": "noop"})
            else:
                row.append({"text": str(day), "callback_data": f"day:{d.isoformat()}"})
        rows.append(row)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    nav_row = [{"text": "« الشهر السابق", "callback_data": f"nav:{prev_year}:{prev_month}"}]
    if (next_year, next_month) <= (today.year, today.month):
        nav_row.append({"text": "الشهر التالي »", "callback_data": f"nav:{next_year}:{next_month}"})
    rows.append(nav_row)

    return {"inline_keyboard": rows}


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(api_url("sendMessage"), data=data, timeout=30)
    return r.json()


def edit_message(chat_id, message_id, text: str, reply_markup: dict | None = None) -> None:
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    requests.post(api_url("editMessageText"), data=data, timeout=30)


def answer_callback(callback_id: str, text: str | None = None) -> None:
    data = {"callback_query_id": callback_id}
    if text:
        data["text"] = text
    requests.post(api_url("answerCallbackQuery"), data=data, timeout=30)


def send_document(chat_id, file_path: str, caption: str) -> None:
    with open(file_path, "rb") as f:
        files = {"document": (os.path.basename(file_path), f, "application/pdf")}
        data = {"chat_id": chat_id, "caption": caption}
        requests.post(api_url("sendDocument"), data=data, files=files, timeout=300)


def handle_day_selection(chat_id, target_date: datetime.date) -> None:
    issue_number = dl.compute_issue_number(target_date)
    out_path = f"issue{issue_number}.pdf"

    ok = dl.try_direct_download(issue_number, out_path)
    if not ok:
        send_message(
            chat_id,
            f"تعذّر العثور على عدد بتاريخ {target_date.isoformat()} (رقم العدد المتوقع: {issue_number}).",
        )
        return

    caption = f"صحيفة الشرق الأوسط - العدد {issue_number} - {target_date.isoformat()}"
    send_document(chat_id, out_path, caption)
    os.remove(out_path)


def handle_update(update: dict, allowed_chat_id: str) -> None:
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        if str(chat_id) != str(allowed_chat_id):
            return
        text = msg.get("text", "").strip()
        if text in ("/date", "/تاريخ", "/start"):
            today = datetime.datetime.now(SAUDI_TZ).date()
            keyboard = build_calendar_keyboard(today.year, today.month)
            send_message(chat_id, CALENDAR_PROMPT, keyboard)
        return

    if "callback_query" in update:
        cq = update["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        message_id = cq["message"]["message_id"]
        data = cq.get("data", "")

        if str(chat_id) != str(allowed_chat_id):
            answer_callback(cq["id"])
            return

        if data == "noop":
            answer_callback(cq["id"])
            return

        if data.startswith("nav:"):
            _, year, month = data.split(":")
            keyboard = build_calendar_keyboard(int(year), int(month))
            edit_message(chat_id, message_id, CALENDAR_PROMPT, keyboard)
            answer_callback(cq["id"])
            return

        if data.startswith("day:"):
            date_str = data.split(":", 1)[1]
            target_date = datetime.date.fromisoformat(date_str)
            answer_callback(cq["id"], text="جارٍ التحميل، الرجاء الانتظار...")
            handle_day_selection(chat_id, target_date)
            return


def main() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    allowed_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not allowed_chat_id:
        print("[!] يجب تعيين TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID كمتغيرات بيئة.")
        sys.exit(1)

    state = load_state()
    offset = state.get("last_update_id", 0) + 1

    response = requests.get(api_url("getUpdates"), params={"offset": offset, "timeout": 0}, timeout=30)
    payload = response.json()
    if not payload.get("ok"):
        print(f"[!] فشل جلب التحديثات من Telegram: {payload}")
        sys.exit(1)

    updates = payload["result"]
    if not updates:
        print("[*] لا توجد رسائل جديدة.")
        return

    for update in updates:
        try:
            handle_update(update, allowed_chat_id)
        except Exception as exc:
            print(f"[!] خطأ أثناء معالجة التحديث {update.get('update_id')}: {exc}")
        state["last_update_id"] = update["update_id"]

    save_state(state)
    print(f"[+] تمت معالجة {len(updates)} تحديث/تحديثات جديدة.")


if __name__ == "__main__":
    main()
