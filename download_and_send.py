#!/usr/bin/env python3
"""
تحميل نسخة PDF اليومية من صحيفة الشرق الأوسط وإرسالها عبر بوت Telegram.

آلية العمل:
1. حساب رقم عدد اليوم اعتمادًا على عدد مرجعي معروف وتاريخه.
2. محاولة تحميل ملف الـ PDF عبر رابط مباشر (تم التحقق منه فعليًا بالمتصفح
   وهو الرابط الذي يفتحه زر "Download" فعليًا في نهاية المطاف).
3. إن فشل الرابط المباشر (مثلاً بسبب تغيير في بنية الموقع)، يتم اللجوء
   تلقائيًا إلى Playwright: فتح الصفحة، الضغط على زر "Download" في الشريط
   السفلي، ثم الضغط على رابط "Full Publication" داخل النافذة المنبثقة،
   والتقاط ملف الـ PDF الناتج عن ذلك.
4. إرسال الملف الناتج عبر Telegram Bot API (sendDocument).
"""

import argparse
import datetime
import os
import sys
from zoneinfo import ZoneInfo

import requests

# عدد مرجعي معروف وتاريخه (بتوقيت السعودية) لحساب رقم أي عدد لاحق.
REFERENCE_ISSUE_NUMBER = 17457
REFERENCE_DATE = datetime.date(2026, 9, 14)
SAUDI_TZ = ZoneInfo("Asia/Riyadh")

BASE_URL = "https://aawsat.com/files/pdf/issue{issue}"
DIRECT_PDF_URL = BASE_URL + "/files/assets/common/downloads/issue{issue}.pdf"
VIEWER_URL = BASE_URL + "/index.html"

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendDocument"

# الموقع يحجب طلبات لا تحمل ترويسة User-Agent شبيهة بمتصفح حقيقي (تم التحقق من ذلك فعليًا).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def compute_issue_number(target_date: datetime.date) -> int:
    delta_days = (target_date - REFERENCE_DATE).days
    return REFERENCE_ISSUE_NUMBER + delta_days


def today_in_saudi_arabia() -> datetime.date:
    return datetime.datetime.now(SAUDI_TZ).date()


def looks_like_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def try_direct_download(issue_number: int, out_path: str) -> bool:
    """المحاولة الأسرع: تحميل الملف مباشرة عبر الرابط الثابت الذي تحقق منه فعليًا."""
    url = DIRECT_PDF_URL.format(issue=issue_number)
    print(f"[*] محاولة التحميل المباشر: {url}")
    try:
        response = requests.get(url, headers={"User-Agent": BROWSER_USER_AGENT}, timeout=120)
    except requests.RequestException as exc:
        print(f"[!] فشل الاتصال بالرابط المباشر: {exc}")
        return False

    content_type = response.headers.get("Content-Type", "")
    if response.status_code != 200 or "pdf" not in content_type.lower() or not looks_like_pdf(response.content):
        print(f"[!] الرابط المباشر لم يُرجع ملف PDF صالحًا (status={response.status_code}, content-type={content_type})")
        return False

    with open(out_path, "wb") as f:
        f.write(response.content)
    print(f"[+] تم التحميل المباشر بنجاح: {out_path} ({len(response.content):,} bytes)")
    return True


def download_via_browser(issue_number: int, out_path: str) -> bool:
    """خطة بديلة: محاكاة نقرات المستخدم الفعلية عبر Playwright في حال تغيّرت بنية الموقع."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[!] Playwright غير مثبت، تعذّر استخدام الخطة البديلة.")
        return False

    url = VIEWER_URL.format(issue=issue_number)
    print(f"[*] فتح الصفحة عبر Playwright: {url}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            page.goto(url, wait_until="load", timeout=60000)
            page.wait_for_timeout(6000)

            page.locator('button[title="Download"]').click()
            page.wait_for_selector("a.download-full-button", timeout=15000)

            with page.expect_download(timeout=60000) as download_info:
                page.locator("a.download-full-button").click()
            download = download_info.value
            download.save_as(out_path)

            browser.close()
    except Exception as exc:
        print(f"[!] فشلت خطة Playwright البديلة: {exc}")
        return False

    if not os.path.exists(out_path) or not looks_like_pdf(open(out_path, "rb").read(5)):
        print("[!] الملف الناتج عن Playwright ليس PDF صالحًا.")
        return False

    print(f"[+] تم التحميل عبر Playwright بنجاح: {out_path}")
    return True


def send_to_telegram(pdf_path: str, issue_number: int, issue_date: datetime.date) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("[!] يجب تعيين TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID كمتغيرات بيئة.")
        sys.exit(1)

    caption = f"صحيفة الشرق الأوسط - العدد {issue_number} - {issue_date.isoformat()}"
    url = TELEGRAM_API_URL.format(token=bot_token)

    with open(pdf_path, "rb") as f:
        files = {"document": (os.path.basename(pdf_path), f, "application/pdf")}
        data = {"chat_id": chat_id, "caption": caption}
        response = requests.post(url, data=data, files=files, timeout=300)

    if response.status_code != 200:
        print(f"[!] فشل إرسال الملف عبر Telegram: {response.status_code} - {response.text}")
        sys.exit(1)

    print("[+] تم إرسال الملف عبر Telegram بنجاح.")


def main():
    parser = argparse.ArgumentParser(description="تحميل عدد اليوم من الشرق الأوسط وإرساله عبر Telegram")
    parser.add_argument(
        "--issue",
        type=int,
        default=None,
        help="رقم عدد محدد للتجربة (اختياري، الافتراضي هو حساب عدد اليوم تلقائيًا)",
    )
    args = parser.parse_args()

    if args.issue is not None:
        issue_number = args.issue
        issue_date = REFERENCE_DATE + datetime.timedelta(days=issue_number - REFERENCE_ISSUE_NUMBER)
    else:
        issue_date = today_in_saudi_arabia()
        issue_number = compute_issue_number(issue_date)

    print(f"[*] التاريخ (بتوقيت السعودية): {issue_date.isoformat()}")
    print(f"[*] رقم العدد المحسوب: {issue_number}")

    out_path = f"issue{issue_number}.pdf"

    success = try_direct_download(issue_number, out_path)
    if not success:
        success = download_via_browser(issue_number, out_path)

    if not success:
        print("[!] فشلت جميع طرق التحميل.")
        sys.exit(1)

    send_to_telegram(out_path, issue_number, issue_date)


if __name__ == "__main__":
    main()
