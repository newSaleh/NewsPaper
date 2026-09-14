#!/usr/bin/env python3
"""
متابعة رسائل بوت Telegram: أي رسالة يرسلها المستخدم (أي نص كان) تجعل
البوت يعرض قائمة بآخر 10 أيام (اليوم + 9 أيام سابقة) ليختار المستخدم
يومًا منها، ويرسل عدد ذلك اليوم عند اختياره.

يعمل بأسلوب "الاستطلاع" (polling): يُستدعى دوريًا عبر GitHub Actions
(كل بضع دقائق)، يفحص الرسائل الجديدة عبر getUpdates، يردّ عليها، ثم
يحفظ آخر update_id تمت معالجته في bot_state.json حتى لا تُعاد معالجة
نفس الرسائل في المرة القادمة.

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

LIST_PROMPT = "اختر اليوم الذي تريد إرسال عدده:"


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


def build_recent_days_keyboard() -> dict:
    today = datetime.datetime.now(SAUDI_TZ).date()
    rows = []
    for i in range(RECENT_DAYS_COUNT):
        d = today - datetime.timedelta(days=i)
        weekday = ARABIC_WEEKDAYS_BY_PY_WEEKDAY[d.weekday()]
        label = f"{'اليوم - ' if i == 0 else ''}{d.isoformat()} ({weekday})"
        rows.append([{"text": label, "callback_data": f"day:{d.isoformat()}"}])
    return {"inline_keyboard": rows}


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup)
    r = requests.post(api_url("sendMessage"), data=data, timeout=30)
    return r.json()


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
        # أي رسالة نصية على الإطلاق (بغض النظر عن محتواها) تعرض قائمة الأيام.
        if msg.get("text"):
            keyboard = build_recent_days_keyboard()
            send_message(chat_id, LIST_PROMPT, keyboard)
        return

    if "callback_query" in update:
        cq = update["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        data = cq.get("data", "")

        if str(chat_id) != str(allowed_chat_id):
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
