#!/usr/bin/env python3
"""
متابعة رسائل بوت Telegram: أي رسالة نصية عادية من المستخدم تجعل البوت
يردّ بقائمة نصية مرقّمة من 1 إلى 10 (اليوم + 9 أيام سابقة)، وعندما يرد
المستخدم برقم من هذه القائمة يرسل له البوت عدد ذلك اليوم.

يعمل بأسلوب "الاستطلاع" (polling): يُستدعى دوريًا عبر GitHub Actions
(كل بضع دقائق)، يفحص الرسائل الجديدة عبر getUpdates، يردّ عليها، ثم
يحفظ آخر update_id تمت معالجته في bot_state.json حتى لا تُعاد معالجة
نفس الرسائل في المرة القادمة.

لا حاجة لتذكّر أي "حالة محادثة" بين رسالة القائمة ورد الرقم: الرقم N
يقابل دائمًا (تاريخ اليوم - (N-1) يوم) وقت وصول الرد، وهذا وحده كافٍ
لحساب التاريخ الصحيح دون تخزين إضافي.

لا يعتمد على الخطة البديلة عبر Playwright عمدًا (لإبقاء هذا الفحص
الدوري سريعًا وخفيفًا)؛ يعتمد فقط على رابط التحميل المباشر. القائمة
محصورة بآخر 10 أيام لأن الأعداد الأقدم من ذلك تُحذف من موقع الشرق
الأوسط (تم التحقق من ذلك فعليًا).
"""

import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

import requests

import download_and_send as dl

STATE_FILE = "bot_state.json"
SAUDI_TZ = ZoneInfo("Asia/Riyadh")
RECENT_DAYS_COUNT = 10

ARABIC_WEEKDAYS_BY_PY_WEEKDAY = [
    "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد",
]

ARABIC_INDIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


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


def date_for_position(position: int, today: datetime.date) -> datetime.date:
    return today - datetime.timedelta(days=position - 1)


def format_day_label(position: int, d: datetime.date) -> str:
    date_str = d.strftime("%d-%m-%Y")
    if position == 1:
        return f"اليوم {date_str}"
    weekday = ARABIC_WEEKDAYS_BY_PY_WEEKDAY[d.weekday()]
    return f"{weekday} {date_str}"


def build_recent_days_list_text() -> str:
    today = datetime.datetime.now(SAUDI_TZ).date()
    lines = ["اختر رقم اليوم الذي تريد إرسال عدده، وأرسل الرقم فقط:"]
    for position in range(1, RECENT_DAYS_COUNT + 1):
        d = date_for_position(position, today)
        lines.append(f"{position}. {format_day_label(position, d)}")
    return "\n".join(lines)


def send_message(chat_id, text: str) -> dict:
    r = requests.post(api_url("sendMessage"), data={"chat_id": chat_id, "text": text}, timeout=30)
    return r.json()


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
    if "message" not in update:
        return

    msg = update["message"]
    chat_id = msg["chat"]["id"]
    if str(chat_id) != str(allowed_chat_id):
        return

    text = (msg.get("text") or "").strip().translate(ARABIC_INDIC_DIGITS)
    if not text:
        return

    if text.isdigit() and 1 <= int(text) <= RECENT_DAYS_COUNT:
        today = datetime.datetime.now(SAUDI_TZ).date()
        target_date = date_for_position(int(text), today)
        handle_day_selection(chat_id, target_date)
    else:
        send_message(chat_id, build_recent_days_list_text())


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
