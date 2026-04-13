#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
  BOT TELEGRAM THEO DÕI GIÁ VÀNG VIỆT NAM
=============================================================================
  Phiên bản: 2.0
  Ngôn ngữ : Python 3.10+

  Nguồn dữ liệu: 24h.com.vn
  URL: https://www.24h.com.vn/gia-vang-hom-nay-c425.html

  Chức năng chính:
  ─────────────────
  1. Cào dữ liệu giá vàng từ trang 24h.com.vn
     (tổng hợp: SJC, DOJI HN, DOJI SG, BTMH, BTMC, Phú Quý, PNJ)
  2. Kiểm tra tự động mỗi 15 phút qua JobQueue (APScheduler tích hợp)
  3. Cảnh báo Tăng (Alert 1): Giá bán >= ngưỡng cảnh báo
  4. Cảnh báo Giảm (Alert 2): Giá quay về < ngưỡng trong vòng 1 giờ
     kể từ khi cảnh báo tăng được phát ra
  5. Quản lý trạng thái (State) bằng Dictionary cho từng loại vàng độc lập

  Hướng dẫn cài đặt:
  ─────────────────
  pip install "python-telegram-bot[job-queue]" requests beautifulsoup4 lxml

  Hướng dẫn sử dụng:
  ─────────────────
  1. Thay YOUR_BOT_TOKEN bằng token BotFather cấp
  2. Thay YOUR_CHAT_ID bằng chat ID của bạn (dùng @userinfobot)
  3. Chạy: python gold_price_bot.py
=============================================================================
"""

import logging
import os
import re
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =============================================================================
# CẤU HÌNH (CONFIGURATION)
# =============================================================================

# ┌─────────────────────────────────────────────────────────────┐
# │  HƯỚNG DẪN LẤY BOT_TOKEN:                                  │
# │  1. Mở Telegram, tìm @BotFather                             │
# │  2. Gửi lệnh /newbot, đặt tên và username cho bot           │
# │  3. BotFather sẽ trả về một token dạng:                     │
# │     123456789:ABCdefGHIjklMNOpqrsTUVwxyz                    │
# │  4. Copy token đó và dán vào biến BOT_TOKEN bên dưới        │
# │                                                              │
# │  HƯỚNG DẪN LẤY CHAT_ID:                                     │
# │  1. Mở Telegram, tìm @userinfobot hoặc @getmyid_bot        │
# │  2. Gửi /start, bot sẽ trả về Chat ID của bạn              │
# │  3. Hoặc: gửi tin nhắn bất kỳ cho bot của bạn, rồi truy    │
# │     cập: https://api.telegram.org/bot<TOKEN>/getUpdates     │
# │     để xem chat_id trong kết quả JSON                       │
# │  4. Copy Chat ID (dạng số) và dán vào biến CHAT_ID          │
# └─────────────────────────────────────────────────────────────┘

BOT_TOKEN = "8637938416:AAHS3zRoTG5ZeQdycW9EAJWFI8XsWWuYPZo"
CHAT_ID = "6239098089"

# Ngưỡng giá cảnh báo (VNĐ/lượng)
PRICE_THRESHOLD = 175_000_000  # 175 triệu VNĐ

# Tần suất kiểm tra giá (giây) - mặc định 15 phút = 900 giây
CHECK_INTERVAL_SECONDS = 15 * 60  # 900 giây

# Thời gian theo dõi giảm giá sau cảnh báo tăng (giây) - mặc định 1 giờ
DROP_MONITOR_WINDOW_SECONDS = 60 * 60  # 3600 giây

# Múi giờ Việt Nam (UTC+7)
VN_TIMEZONE = timezone(timedelta(hours=7))

# URL nguồn dữ liệu 24h.com.vn
DATA_SOURCE_URL = "https://www.24h.com.vn/gia-vang-hom-nay-c425.html"

# HTTP Headers giả lập trình duyệt để tránh bị chặn
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.24h.com.vn/",
}

# Timeout cho HTTP requests (giây)
HTTP_TIMEOUT = 15

# Port cho web server (Render.com truyền qua biến môi trường PORT)
PORT = int(os.environ.get("PORT", 10000))

# URL của service trên Render (dùng để self-ping giữ bot sống)
# Render tự cung cấp biến RENDER_EXTERNAL_HOSTNAME
# Hoặc bạn có thể set thủ công, VD: "https://ten-service.onrender.com"
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

# Mapping tên hiển thị đẹp hơn cho data-seach values
GOLD_NAME_MAP = {
    "sjc": "SJC",
    "doji_hn": "DOJI Hà Nội",
    "doji_sg": "DOJI Sài Gòn",
    "btmh": "Bảo Tín Mạnh Hải",
    "bao_tin_minh_chau": "Bảo Tín Minh Châu",
    "phu_quy_sjc": "Phú Quý SJC",
    "pnj_tp_hcml": "PNJ TP.HCM",
    "pnj_hn": "PNJ Hà Nội",
}

# =============================================================================
# THIẾT LẬP LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =============================================================================
# QUẢN LÝ TRẠNG THÁI (STATE MANAGEMENT)
# =============================================================================
#
# Dictionary lưu trữ trạng thái cảnh báo cho từng loại vàng:
#
#   alert_state = {
#       "SJC": {
#           "alert_time": datetime(2025, 1, 15, 10, 0, 0),   # Thời điểm phát cảnh báo tăng
#           "alert_price": 176_500_000,                       # Giá tại thời điểm cảnh báo
#           "already_notified_drop": False,                   # Đã gửi cảnh báo giảm chưa?
#       },
#       "DOJI Hà Nội": {
#           "alert_time": datetime(2025, 1, 15, 10, 15, 0),  # Có thể khác thời điểm
#           "alert_price": 175_200_000,
#           "already_notified_drop": False,
#       },
#   }
#
# Giải thích logic thời gian 1 giờ:
# ──────────────────────────────────
# - Khi giá vàng loại X vượt ngưỡng → ghi lại alert_time = now()
# - Mỗi lần kiểm tra (15 phút/lần), tính: elapsed = now() - alert_time
# - Nếu elapsed <= 60 phút VÀ giá < ngưỡng → GỬI cảnh báo giảm
# - Nếu elapsed > 60 phút → XÓA bản ghi khỏi alert_state (hết hạn theo dõi)
# - Nếu đã gửi cảnh báo giảm (already_notified_drop = True) → KHÔNG gửi lại
#
alert_state: dict[str, dict] = {}


# =============================================================================
# MODULE CÀO DỮ LIỆU GIÁ VÀNG TỪ 24H.COM.VN
# =============================================================================
#
# Cấu trúc HTML bảng giá vàng trên 24h.com.vn:
#
#   <table class="gia-vang-search-data-table">
#     <tbody>
#       <tr data-seach="sjc">
#         <td class="colorGrey pdL10"><h2>SJC</h2></td>
#         <td class="colorGrey">
#           <span class="fixW">168,500</span>          ← Giá mua (nghìn đồng)
#           <img src="btn_down2019.png" />              ← Icon tăng/giảm
#           <span class="colorRed">900</span>           ← Mức thay đổi
#         </td>
#         <td class="colorGrey">
#           <span class="fixW">171,500</span>          ← Giá bán (nghìn đồng)
#           <img src="btn_down2019.png" />
#           <span class="colorRed">900</span>
#         </td>
#         <td class="colorBlur">169,400</td>           ← Giá mua hôm qua
#         <td class="colorBlur">172,400</td>           ← Giá bán hôm qua
#       </tr>
#       ... (8 dòng cho 8 thương hiệu vàng)
#     </tbody>
#   </table>
#
# Danh sách data-seach:
#   sjc, doji_hn, doji_sg, btmh, bao_tin_minh_chau,
#   phu_quy_sjc, pnj_tp_hcml, pnj_hn
#


def _parse_price_24h(price_str: str) -> Optional[int]:
    """
    Chuyển đổi giá từ 24h.com.vn thành số nguyên (VNĐ).

    24h.com.vn hiển thị giá theo đơn vị nghìn đồng/lượng:
      - "168,500" → 168,500 × 1,000 = 168,500,000 VNĐ
      - "171,500" → 171,500 × 1,000 = 171,500,000 VNĐ

    Returns:
        int: Giá tính bằng VNĐ, hoặc None nếu không parse được
    """
    if not price_str or not price_str.strip():
        return None

    # Xóa khoảng trắng và ký tự thừa
    cleaned = price_str.strip()

    # Loại bỏ dấu phẩy phân cách hàng nghìn: "168,500" → "168500"
    cleaned = cleaned.replace(",", "")

    # Loại bỏ ký tự không phải số hoặc dấu chấm
    cleaned = re.sub(r"[^\d.]", "", cleaned)

    if not cleaned:
        return None

    try:
        value = int(cleaned)
    except ValueError:
        try:
            value = int(float(cleaned))
        except ValueError:
            return None

    # 24h.com.vn hiển thị đơn vị nghìn đồng → nhân 1,000 để ra VNĐ
    # VD: 168500 × 1000 = 168,500,000 VNĐ
    if value < 1_000_000:
        value *= 1_000

    return value


def crawl_24h() -> tuple[list[dict], str]:
    """
    Cào dữ liệu giá vàng từ 24h.com.vn.

    Đọc bảng giá tại:
        https://www.24h.com.vn/gia-vang-hom-nay-c425.html

    Tìm bảng có class "gia-vang-search-data-table", duyệt qua từng hàng (<tr>)
    với thuộc tính data-seach để xác định loại vàng.

    Returns:
        tuple[list[dict], str]:
            - list[dict]: Danh sách giá vàng, mỗi phần tử chứa:
                {
                    "name": "SJC",             # Tên loại vàng (đã map đẹp)
                    "code": "sjc",             # Mã data-seach gốc
                    "buy":  168_500_000,        # Giá mua vào (VNĐ)
                    "sell": 171_500_000,        # Giá bán ra (VNĐ)
                    "buy_change":  -900_000,    # Thay đổi giá mua (VNĐ)
                    "sell_change": -900_000,    # Thay đổi giá bán (VNĐ)
                    "buy_yesterday":  169_400_000,  # Giá mua hôm qua
                    "sell_yesterday": 172_400_000,  # Giá bán hôm qua
                    "trend": "down",           # Xu hướng: "up" / "down" / "stable"
                }
            - str: Thời gian cập nhật từ trang web (VD: "23:24 (13/04/2026)")
    """
    results = []
    update_time = ""

    try:
        resp = requests.get(DATA_SOURCE_URL, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = "utf-8"

        soup = BeautifulSoup(resp.text, "html.parser")

        # ─── Lấy thời gian cập nhật ───
        # Format trên trang: "Nguồn: giavang.net, pnj.com.vn và baotinmanhhai.vn - Cập nhật lúc 23:24 (13/04/2026)"
        time_match = re.search(
            r"Cập nhật lúc\s*(\d{1,2}:\d{2})\s*\((\d{1,2}/\d{1,2}/\d{4})\)",
            resp.text
        )
        if time_match:
            update_time = f"{time_match.group(1)} {time_match.group(2)}"

        # ─── Tìm bảng giá vàng chính ───
        gold_table = soup.find("table", class_="gia-vang-search-data-table")
        if not gold_table:
            logger.warning("[24h] Không tìm thấy bảng giá vàng (class='gia-vang-search-data-table')")
            return results, update_time

        # ─── Duyệt từng hàng trong bảng ───
        rows = gold_table.find_all("tr", attrs={"data-seach": True})
        logger.info(f"[24h] Tìm thấy {len(rows)} hàng dữ liệu giá vàng")

        for row in rows:
            try:
                # Lấy mã loại vàng từ thuộc tính data-seach
                code = row.get("data-seach", "").strip()
                if not code:
                    continue

                cols = row.find_all("td")
                if len(cols) < 3:
                    continue

                # ── Cột 1: Tên loại vàng ──
                name_tag = cols[0].find("h2")
                raw_name = name_tag.get_text(strip=True) if name_tag else cols[0].get_text(strip=True)

                # Dùng tên đẹp từ GOLD_NAME_MAP nếu có, nếu không dùng tên gốc
                display_name = GOLD_NAME_MAP.get(code, raw_name)

                # ── Cột 2: Giá mua hôm nay ──
                buy_span = cols[1].find("span", class_="fixW")
                buy_str = buy_span.get_text(strip=True) if buy_span else ""
                buy_price = _parse_price_24h(buy_str)

                # Lấy mức thay đổi giá mua (nếu có)
                buy_change_span = cols[1].find("span", class_=re.compile(r"color(Red|Green)"))
                buy_change_str = buy_change_span.get_text(strip=True) if buy_change_span else "0"

                # ── Cột 3: Giá bán hôm nay ──
                sell_span = cols[2].find("span", class_="fixW")
                sell_str = sell_span.get_text(strip=True) if sell_span else ""
                sell_price = _parse_price_24h(sell_str)

                # Lấy mức thay đổi giá bán (nếu có)
                sell_change_span = cols[2].find("span", class_=re.compile(r"color(Red|Green)"))
                sell_change_str = sell_change_span.get_text(strip=True) if sell_change_span else "0"

                # ── Xác định xu hướng (icon tăng/giảm) ──
                trend = "stable"
                img_tag = cols[1].find("img")
                if img_tag:
                    img_src = img_tag.get("src", "")
                    if "down" in img_src.lower():
                        trend = "down"
                    elif "up" in img_src.lower():
                        trend = "up"

                # ── Parse mức thay đổi ──
                buy_change = _parse_price_24h(buy_change_str) or 0
                sell_change = _parse_price_24h(sell_change_str) or 0
                # Nếu xu hướng giảm → giá thay đổi là số âm
                if trend == "down":
                    buy_change = -abs(buy_change)
                    sell_change = -abs(sell_change)

                # ── Cột 4-5: Giá hôm qua (nếu có) ──
                buy_yesterday = None
                sell_yesterday = None
                if len(cols) >= 5:
                    buy_yesterday = _parse_price_24h(cols[3].get_text(strip=True))
                    sell_yesterday = _parse_price_24h(cols[4].get_text(strip=True))

                # ── Bỏ qua nếu không có giá bán ──
                if sell_price is None:
                    logger.debug(f"[24h] Bỏ qua {display_name}: không có giá bán")
                    continue

                results.append({
                    "name": display_name,
                    "code": code,
                    "buy": buy_price,
                    "sell": sell_price,
                    "buy_change": buy_change,
                    "sell_change": sell_change,
                    "buy_yesterday": buy_yesterday,
                    "sell_yesterday": sell_yesterday,
                    "trend": trend,
                })

            except Exception as e:
                logger.warning(f"[24h] Lỗi parse hàng data-seach='{code}': {e}")
                continue

        logger.info(
            f"[24h] ✅ Thu thập thành công {len(results)} loại vàng | "
            f"Cập nhật: {update_time}"
        )

    except requests.RequestException as e:
        logger.error(f"[24h] ❌ Lỗi kết nối: {e}")
    except Exception as e:
        logger.error(f"[24h] ❌ Lỗi không xác định: {e}")
        logger.debug(traceback.format_exc())

    return results, update_time


# =============================================================================
# FORMAT TIN NHẮN TELEGRAM
# =============================================================================

def format_price(price: Optional[int]) -> str:
    """Format giá thành chuỗi dễ đọc: 176,500,000 VNĐ"""
    if price is None:
        return "N/A"
    return f"{price:,.0f} VNĐ"


def format_change(change: int) -> str:
    """Format mức thay đổi giá với dấu +/- và emoji."""
    if change > 0:
        return f"📈 +{change:,.0f}"
    elif change < 0:
        return f"📉 {change:,.0f}"
    else:
        return "➡️ 0"


def build_rise_alert_message(item: dict, now: datetime, update_time: str) -> str:
    """
    Xây dựng tin nhắn cảnh báo TĂNG GIÁ (Alert 1).

    Nội dung bao gồm: emoji, loại vàng, giá mua/bán, ngưỡng, thời gian.
    """
    over = item["sell"] - PRICE_THRESHOLD
    return (
        f"🔴🔴🔴 *CẢNH BÁO GIÁ VÀNG TĂNG* 🔴🔴🔴\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *Loại vàng:* {item['name']}\n"
        f"💰 *Giá mua vào:* `{format_price(item['buy'])}`\n"
        f"💸 *Giá bán ra:*  `{format_price(item['sell'])}`\n"
        f"⚠️ *Ngưỡng cảnh báo:* `{format_price(PRICE_THRESHOLD)}`\n"
        f"📊 *Vượt ngưỡng:* `+{format_price(over)}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 *Thời gian cập nhật (24h):* {update_time}\n"
        f"🕐 *Thời gian kiểm tra:* {now.strftime('%H:%M:%S %d/%m/%Y')}\n"
        f"📡 Hệ thống đang theo dõi giá trong 60 phút tới...\n"
        f"📌 Nguồn: 24h.com.vn"
    )


def build_drop_alert_message(item: dict, alert_time: datetime, now: datetime, update_time: str) -> str:
    """
    Xây dựng tin nhắn cảnh báo GIẢM GIÁ (Alert 2).

    Nội dung bao gồm: thời điểm báo tăng ban đầu, thời gian đã qua,
    giá hiện tại, xác nhận đã giảm xuống dưới ngưỡng.
    """
    elapsed = now - alert_time
    elapsed_minutes = int(elapsed.total_seconds() / 60)

    return (
        f"🟢🟢🟢 *CẢNH BÁO GIÁ VÀNG GIẢM* 🟢🟢🟢\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *Loại vàng:* {item['name']}\n"
        f"💰 *Giá bán hiện tại:* `{format_price(item['sell'])}`\n"
        f"⬇️ *Đã giảm xuống dưới:* `{format_price(PRICE_THRESHOLD)}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ *Thời điểm cảnh báo tăng:* {alert_time.strftime('%H:%M:%S %d/%m/%Y')}\n"
        f"⏱ *Thời gian từ lúc tăng:* {elapsed_minutes} phút\n"
        f"🕐 *Thời gian phát hiện giảm:* {now.strftime('%H:%M:%S %d/%m/%Y')}\n"
        f"🕐 *Cập nhật (24h):* {update_time}\n"
        f"✅ Giá đã quay về dưới ngưỡng trong phạm vi 60 phút.\n"
        f"📌 Nguồn: 24h.com.vn"
    )


# =============================================================================
# LOGIC XỬ LÝ CHÍNH (CORE PROCESSING LOGIC)
# =============================================================================

async def process_gold_prices(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Hàm xử lý chính - được gọi mỗi 15 phút bởi JobQueue.

    Luồng xử lý:
    ─────────────
    1. Thu thập giá vàng từ 24h.com.vn
    2. Quét từng loại vàng:
       a. Nếu giá bán >= ngưỡng VÀ chưa có trong alert_state:
          → Gửi Cảnh báo Tăng + Lưu vào alert_state
       b. Nếu đang trong alert_state:
          → Kiểm tra đã quá 60 phút chưa
          → Nếu chưa quá 60 phút VÀ giá < ngưỡng:
             → Gửi Cảnh báo Giảm
          → Nếu đã quá 60 phút:
             → Xóa khỏi alert_state (hết hạn)
    """
    global alert_state

    now = datetime.now(VN_TIMEZONE)
    logger.info(f"{'='*60}")
    logger.info(f"[{now.strftime('%H:%M:%S %d/%m/%Y')}] Bắt đầu kiểm tra giá vàng...")
    logger.info(f"{'='*60}")

    # ─── Bước 1: Thu thập dữ liệu từ 24h.com.vn ───
    all_prices, update_time = crawl_24h()

    if not all_prices:
        logger.warning("⚠️ Không thu thập được dữ liệu giá vàng từ 24h.com.vn!")
        return

    # Tạo dict tra cứu nhanh theo tên loại vàng (key = name)
    price_lookup = {item["name"]: item for item in all_prices}

    # ─── Bước 2: Xử lý cảnh báo ───
    keys_to_remove = []  # Danh sách key cần xóa sau vòng lặp

    # 2a. Kiểm tra các loại vàng đang được theo dõi trong alert_state
    for key, state in alert_state.items():
        alert_time = state["alert_time"]
        elapsed = now - alert_time
        elapsed_seconds = elapsed.total_seconds()

        # ── Trường hợp 1: Đã quá 60 phút → Hết hạn theo dõi ──
        if elapsed_seconds > DROP_MONITOR_WINDOW_SECONDS:
            logger.info(
                f"[EXPIRED] {key} - Đã theo dõi {int(elapsed_seconds/60)} phút, "
                f"hết hạn. Xóa khỏi danh sách theo dõi."
            )
            keys_to_remove.append(key)
            continue

        # ── Trường hợp 2: Trong phạm vi 60 phút → Kiểm tra giá ──
        if key in price_lookup:
            current_item = price_lookup[key]
            current_sell = current_item["sell"]

            if current_sell is not None and current_sell < PRICE_THRESHOLD:
                # Giá đã giảm xuống dưới ngưỡng!
                if not state["already_notified_drop"]:
                    # Chưa gửi cảnh báo giảm → GỬI NGAY
                    drop_msg = build_drop_alert_message(
                        current_item, alert_time, now, update_time
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=CHAT_ID,
                            text=drop_msg,
                            parse_mode="Markdown",
                        )
                        logger.info(f"[ALERT-DROP] ✅ Đã gửi cảnh báo GIẢM cho: {key}")
                        state["already_notified_drop"] = True
                    except Exception as e:
                        logger.error(f"[ALERT-DROP] ❌ Lỗi gửi tin nhắn: {e}")

                    # Sau khi gửi cảnh báo giảm, xóa khỏi state
                    keys_to_remove.append(key)
                else:
                    logger.debug(f"[ALERT-DROP] Đã gửi cảnh báo giảm trước đó cho: {key}")
        else:
            logger.debug(f"[MONITOR] Key '{key}' không có trong dữ liệu lần này, bỏ qua.")

    # Xóa các key hết hạn hoặc đã xử lý
    for key in keys_to_remove:
        del alert_state[key]
        logger.info(f"[STATE] Đã xóa '{key}' khỏi alert_state")

    # 2b. Kiểm tra tất cả giá vàng để phát cảnh báo TĂNG mới
    for item in all_prices:
        key = item["name"]
        sell_price = item["sell"]

        if sell_price is None:
            continue

        # Nếu giá bán >= ngưỡng VÀ chưa có trong alert_state
        if sell_price >= PRICE_THRESHOLD and key not in alert_state:
            # ── Phát Cảnh báo Tăng (Alert 1) ──
            rise_msg = build_rise_alert_message(item, now, update_time)
            try:
                await context.bot.send_message(
                    chat_id=CHAT_ID,
                    text=rise_msg,
                    parse_mode="Markdown",
                )
                logger.info(
                    f"[ALERT-RISE] ✅ Đã gửi cảnh báo TĂNG cho: {key} | "
                    f"Giá: {format_price(sell_price)}"
                )
            except Exception as e:
                logger.error(f"[ALERT-RISE] ❌ Lỗi gửi tin nhắn: {e}")

            # Lưu vào alert_state với mốc thời gian hiện tại
            alert_state[key] = {
                "alert_time": now,
                "alert_price": sell_price,
                "already_notified_drop": False,
            }
            logger.info(
                f"[STATE] Đã thêm '{key}' vào alert_state, "
                f"bắt đầu theo dõi đến "
                f"{(now + timedelta(seconds=DROP_MONITOR_WINDOW_SECONDS)).strftime('%H:%M:%S')}"
            )

    # ─── Bước 3: Log trạng thái hiện tại ───
    if alert_state:
        logger.info(f"[STATE] Đang theo dõi {len(alert_state)} loại vàng:")
        for key, state in alert_state.items():
            remaining = DROP_MONITOR_WINDOW_SECONDS - (now - state["alert_time"]).total_seconds()
            logger.info(
                f"  ├─ {key} | Alert lúc: {state['alert_time'].strftime('%H:%M:%S')} | "
                f"Còn: {int(remaining/60)} phút"
            )
    else:
        logger.info("[STATE] Không có loại vàng nào đang được theo dõi.")

    logger.info(
        f"[{now.strftime('%H:%M:%S')}] Hoàn tất kiểm tra. "
        f"Lần tiếp theo sau {CHECK_INTERVAL_SECONDS // 60} phút."
    )
    logger.info(f"{'='*60}\n")


# =============================================================================
# LỆNH TELEGRAM (TELEGRAM COMMANDS)
# =============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /start - Khởi động bot và gửi hướng dẫn sử dụng.
    """
    welcome_msg = (
        "🥇 *Bot Theo Dõi Giá Vàng Việt Nam* 🥇\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Xin chào! Bot sẽ tự động kiểm tra giá vàng\n"
        f"mỗi *{CHECK_INTERVAL_SECONDS // 60} phút* và gửi cảnh báo.\n\n"
        "📋 *Danh sách lệnh:*\n"
        "  /start - Khởi động bot\n"
        "  /price - Xem giá vàng hiện tại\n"
        "  /status - Xem trạng thái theo dõi\n"
        "  /check - Kiểm tra giá ngay lập tức\n"
        "  /threshold - Xem/đổi ngưỡng cảnh báo\n"
        "  /help - Hướng dẫn chi tiết\n\n"
        f"⚠️ *Ngưỡng cảnh báo:* `{format_price(PRICE_THRESHOLD)}`\n"
        "📌 *Nguồn dữ liệu:* 24h.com.vn\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Bot đã sẵn sàng hoạt động!"
    )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /price - Hiển thị bảng giá vàng hiện tại từ 24h.com.vn.
    """
    await update.message.reply_text("⏳ Đang thu thập giá vàng từ 24h.com.vn...")

    all_prices, update_time = crawl_24h()
    now = datetime.now(VN_TIMEZONE)

    if not all_prices:
        await update.message.reply_text(
            "❌ Không thể thu thập dữ liệu giá vàng lúc này.\n"
            "Vui lòng thử lại sau."
        )
        return

    # Xây dựng tin nhắn bảng giá
    msg_parts = [
        f"🥇 *BẢNG GIÁ VÀNG VIỆT NAM*\n"
        f"📌 Nguồn: 24h.com.vn\n"
        f"🕐 Cập nhật: {update_time}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for item in all_prices:
        # Emoji cảnh báo nếu vượt ngưỡng
        alert_icon = ""
        if item["sell"] is not None and item["sell"] >= PRICE_THRESHOLD:
            alert_icon = " 🔴"

        # Emoji xu hướng
        trend_icon = "➡️"
        if item["trend"] == "up":
            trend_icon = "🔺"
        elif item["trend"] == "down":
            trend_icon = "🔻"

        msg_parts.append(
            f"\n{trend_icon} *{item['name']}*{alert_icon}\n"
            f"  ├ Mua: `{format_price(item['buy'])}` {format_change(item['buy_change'])}\n"
            f"  └ Bán: `{format_price(item['sell'])}` {format_change(item['sell_change'])}"
        )

    msg_parts.append(
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Ngưỡng cảnh báo: `{format_price(PRICE_THRESHOLD)}`\n"
        f"🔴 = Vượt ngưỡng | 🔺 Tăng | 🔻 Giảm"
    )

    full_msg = "\n".join(msg_parts)

    # Telegram giới hạn 4096 ký tự/tin nhắn → chia nhỏ nếu cần
    if len(full_msg) <= 4096:
        await update.message.reply_text(full_msg, parse_mode="Markdown")
    else:
        chunks = [full_msg[i:i + 4000] for i in range(0, len(full_msg), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /status - Hiển thị trạng thái theo dõi hiện tại.
    """
    now = datetime.now(VN_TIMEZONE)

    if not alert_state:
        await update.message.reply_text(
            "📊 *Trạng thái theo dõi*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Không có loại vàng nào đang được theo dõi.\n"
            f"⚠️ Ngưỡng cảnh báo: `{format_price(PRICE_THRESHOLD)}`\n"
            f"🕐 Hiện tại: {now.strftime('%H:%M:%S %d/%m/%Y')}",
            parse_mode="Markdown",
        )
        return

    msg_parts = [
        "📊 *Trạng thái theo dõi*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for key, state in alert_state.items():
        alert_time = state["alert_time"]
        elapsed = now - alert_time
        elapsed_min = int(elapsed.total_seconds() / 60)
        remaining = max(0, int((DROP_MONITOR_WINDOW_SECONDS - elapsed.total_seconds()) / 60))

        status_emoji = "🟡" if remaining > 0 else "🔴"

        msg_parts.append(
            f"\n{status_emoji} *{key}*\n"
            f"  ├ Cảnh báo tăng lúc: {alert_time.strftime('%H:%M:%S')}\n"
            f"  ├ Giá lúc cảnh báo: `{format_price(state['alert_price'])}`\n"
            f"  ├ Đã theo dõi: {elapsed_min} phút\n"
            f"  └ Còn lại: {remaining} phút"
        )

    msg_parts.append(
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Hiện tại: {now.strftime('%H:%M:%S %d/%m/%Y')}"
    )

    await update.message.reply_text("\n".join(msg_parts), parse_mode="Markdown")


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /check - Kiểm tra giá vàng ngay lập tức (không chờ scheduler).
    """
    await update.message.reply_text("🔄 Đang kiểm tra giá vàng ngay lập tức...")
    await process_gold_prices(context)
    await update.message.reply_text("✅ Kiểm tra hoàn tất!")


async def cmd_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /threshold [giá] - Xem hoặc thay đổi ngưỡng cảnh báo.

    Ví dụ:
        /threshold          → Xem ngưỡng hiện tại
        /threshold 180000000 → Đổi ngưỡng thành 180 triệu
        /threshold 175       → Đổi ngưỡng thành 175 triệu (tự nhân triệu)
    """
    global PRICE_THRESHOLD

    if context.args and len(context.args) > 0:
        try:
            raw = context.args[0].replace(",", "").replace(".", "")
            new_threshold = int(raw)

            # Tự động nhân lên nếu giá trị quá nhỏ
            if new_threshold < 1_000:
                # Nhập dạng 175 → 175 triệu
                new_threshold *= 1_000_000
            elif new_threshold < 1_000_000:
                # Nhập dạng 175000 → nhân 1000
                new_threshold *= 1_000

            old_threshold = PRICE_THRESHOLD
            PRICE_THRESHOLD = new_threshold

            await update.message.reply_text(
                f"✅ *Đã cập nhật ngưỡng cảnh báo*\n"
                f"  Cũ: `{format_price(old_threshold)}`\n"
                f"  Mới: `{format_price(PRICE_THRESHOLD)}`",
                parse_mode="Markdown",
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Giá trị không hợp lệ. Vui lòng nhập số.\n"
                "Ví dụ: `/threshold 180000000` hoặc `/threshold 180`",
                parse_mode="Markdown",
            )
    else:
        await update.message.reply_text(
            f"⚠️ *Ngưỡng cảnh báo hiện tại:* `{format_price(PRICE_THRESHOLD)}`\n\n"
            f"Để thay đổi, dùng: `/threshold <giá>`\n"
            f"Ví dụ:\n"
            f"  `/threshold 180000000` → 180 triệu\n"
            f"  `/threshold 175` → 175 triệu",
            parse_mode="Markdown",
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Xử lý lệnh /help - Hiển thị hướng dẫn chi tiết.
    """
    help_msg = (
        "📖 *HƯỚNG DẪN SỬ DỤNG CHI TIẾT*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔎 *Cách hoạt động:*\n"
        f"Bot kiểm tra giá vàng từ 24h.com.vn mỗi {CHECK_INTERVAL_SECONDS // 60} phút.\n\n"
        "🔴 *Cảnh báo Tăng (Alert 1):*\n"
        f"Khi giá BÁN RA của bất kỳ loại vàng nào đạt mức "
        f"`{format_price(PRICE_THRESHOLD)}` trở lên, bot sẽ gửi cảnh báo ngay.\n\n"
        "🟢 *Cảnh báo Giảm (Alert 2):*\n"
        "Sau khi phát cảnh báo tăng, bot theo dõi loại vàng đó trong 60 phút.\n"
        f"Nếu giá giảm xuống dưới `{format_price(PRICE_THRESHOLD)}` trong 60 phút → Gửi cảnh báo giảm.\n"
        "Nếu quá 60 phút mà giá vẫn trên ngưỡng → Dừng theo dõi (tránh spam).\n\n"
        "📋 *Danh sách lệnh:*\n"
        "  /start - Khởi động, xem tổng quan\n"
        "  /price - Xem bảng giá vàng đầy đủ\n"
        "  /status - Xem danh sách đang theo dõi\n"
        "  /check - Kiểm tra giá tức thì\n"
        "  /threshold [giá] - Xem/thay đổi ngưỡng\n"
        "  /help - Bảng hướng dẫn này\n\n"
        "📡 *Nguồn dữ liệu: 24h.com.vn*\n"
        "Tổng hợp giá từ:\n"
        "  • SJC\n"
        "  • DOJI (Hà Nội & Sài Gòn)\n"
        "  • Bảo Tín Mạnh Hải\n"
        "  • Bảo Tín Minh Châu\n"
        "  • Phú Quý SJC\n"
        "  • PNJ (TP.HCM & Hà Nội)\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(help_msg, parse_mode="Markdown")


# =============================================================================
# KHỞI ĐỘNG BOT (APPLICATION STARTUP)
# =============================================================================

async def post_init(application: Application) -> None:
    """
    Callback sau khi Application được khởi tạo.
    Dùng để thiết lập JobQueue chạy định kỳ.
    """
    job_queue = application.job_queue

    if job_queue is None:
        logger.error(
            "❌ JobQueue không khả dụng! "
            "Hãy cài đặt: pip install 'python-telegram-bot[job-queue]'"
        )
        return

    # Thiết lập job kiểm tra giá vàng chạy mỗi 15 phút
    # first=10: Chạy lần đầu sau 10 giây kể từ khi bot khởi động
    job_queue.run_repeating(
        callback=process_gold_prices,
        interval=CHECK_INTERVAL_SECONDS,
        first=10,  # Delay 10 giây trước lần chạy đầu tiên
        name="gold_price_checker",
    )

    logger.info(
        f"✅ Đã thiết lập JobQueue: "
        f"Kiểm tra giá mỗi {CHECK_INTERVAL_SECONDS // 60} phút, "
        f"lần đầu sau 10 giây"
    )

    # Gửi thông báo khởi động
    if CHAT_ID and CHAT_ID != "YOUR_CHAT_ID":
        try:
            now = datetime.now(VN_TIMEZONE)
            startup_msg = (
                "🤖 *Bot Theo Dõi Giá Vàng đã khởi động!*\n"
                f"🕐 {now.strftime('%H:%M:%S %d/%m/%Y')}\n"
                f"⚠️ Ngưỡng cảnh báo: `{format_price(PRICE_THRESHOLD)}`\n"
                f"🔄 Kiểm tra mỗi: {CHECK_INTERVAL_SECONDS // 60} phút\n"
                f"📌 Nguồn: 24h.com.vn\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "Lần kiểm tra đầu tiên sẽ chạy trong 10 giây..."
            )
            await application.bot.send_message(
                chat_id=CHAT_ID,
                text=startup_msg,
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Lỗi gửi thông báo khởi động: {e}")


# =============================================================================
# KEEP-ALIVE WEB SERVER CHO RENDER.COM
# =============================================================================
#
# Render.com free tier yêu cầu:
#   1. Service phải bind vào PORT (biến môi trường từ Render)
#   2. Service bị tắt sau 15 phút không có HTTP request
#
# Giải pháp:
#   - Chạy một HTTP server nhỏ trên PORT để Render nhận diện service đang sống
#   - Tạo background thread tự ping chính mình mỗi 5 phút để tránh bị tắt
#


class KeepAliveHandler(BaseHTTPRequestHandler):
    """
    HTTP handler đơn giản trả về status 200.
    Render.com sẽ gửi health check đến đây.
    """

    def do_GET(self):
        """Xử lý GET request - trả về trạng thái bot."""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        now = datetime.now(VN_TIMEZONE)
        tracking = len(alert_state)

        html = (
            "<html><head><title>Gold Price Bot</title></head>"
            "<body style='font-family:monospace; padding:20px; background:#1a1a2e; color:#eee;'>"
            "<h1>🥇 Bot Theo Dõi Giá Vàng</h1>"
            f"<p>⏰ Thời gian: {now.strftime('%H:%M:%S %d/%m/%Y')}</p>"
            f"<p>📊 Ngưỡng cảnh báo: {format_price(PRICE_THRESHOLD)}</p>"
            f"<p>🔄 Kiểm tra mỗi: {CHECK_INTERVAL_SECONDS // 60} phút</p>"
            f"<p>👁 Đang theo dõi: {tracking} loại vàng</p>"
            f"<p>📌 Nguồn: 24h.com.vn</p>"
            "<p>✅ Bot đang hoạt động!</p>"
            "</body></html>"
        )
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        """Tắt log mặc định của HTTPServer để tránh spam console."""
        pass


def start_keep_alive_server():
    """
    Khởi động HTTP server trên PORT để Render.com nhận diện service.
    Chạy trong background thread, không block main thread.
    """
    server = HTTPServer(("", PORT), KeepAliveHandler)
    logger.info(f"🌐 Keep-alive server đang chạy trên port {PORT}")
    server.serve_forever()


def self_ping():
    """
    Tự ping chính mình mỗi 5 phút để Render.com không tắt service.

    Render free tier tắt service sau 15 phút không có request.
    Bằng cách gửi request đến chính mình, service luôn có traffic
    và sẽ không bị tắt.

    Cách hoạt động:
    - Đọc RENDER_EXTERNAL_URL (tự cấu hình) hoặc RENDER_EXTERNAL_HOSTNAME (Render cung cấp)
    - Gửi GET request mỗi 5 phút
    - Nếu không có URL, bỏ qua (chạy local không cần self-ping)
    """
    # Xác định URL để ping
    ping_url = RENDER_EXTERNAL_URL
    if not ping_url:
        hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")
        if hostname:
            ping_url = f"https://{hostname}"

    if not ping_url:
        logger.info("[KEEP-ALIVE] Không tìm thấy URL Render → bỏ qua self-ping (đang chạy local)")
        return

    logger.info(f"[KEEP-ALIVE] Bắt đầu self-ping tới: {ping_url}")

    while True:
        time.sleep(300)  # 5 phút
        try:
            resp = requests.get(ping_url, timeout=10)
            logger.debug(f"[KEEP-ALIVE] Ping OK → status {resp.status_code}")
        except Exception as e:
            logger.warning(f"[KEEP-ALIVE] Ping thất bại: {e}")


def main() -> None:
    """
    Hàm main - Điểm vào chương trình.

    Quy trình:
    1. Kiểm tra cấu hình BOT_TOKEN và CHAT_ID
    2. Khởi động keep-alive web server (cho Render.com)
    3. Khởi động self-ping thread
    4. Khởi tạo Application với python-telegram-bot 20.x
    5. Đăng ký các command handlers
    6. Thiết lập post_init callback để cấu hình JobQueue
    7. Bắt đầu polling
    """

    # ── Kiểm tra cấu hình ──
    if BOT_TOKEN == "YOUR_BOT_TOKEN" or not BOT_TOKEN:
        print("=" * 60)
        print("❌ LỖI: Chưa cấu hình BOT_TOKEN!")
        print("=" * 60)
        print("Hướng dẫn:")
        print("1. Mở Telegram → tìm @BotFather")
        print("2. Gửi /newbot → làm theo hướng dẫn")
        print("3. Copy token được cấp")
        print("4. Dán vào biến BOT_TOKEN trong file này")
        print("=" * 60)
        return

    if CHAT_ID == "YOUR_CHAT_ID" or not CHAT_ID:
        print("=" * 60)
        print("⚠️  CẢNH BÁO: Chưa cấu hình CHAT_ID!")
        print("=" * 60)
        print("Bot vẫn chạy nhưng sẽ không gửi được cảnh báo tự động.")
        print()

    # ── Khởi động Keep-Alive Server (background thread) ──
    # Thread daemon=True → tự tắt khi main thread kết thúc
    server_thread = threading.Thread(target=start_keep_alive_server, daemon=True)
    server_thread.start()
    logger.info(f"✅ Keep-alive server đã khởi động trên port {PORT}")

    # ── Khởi động Self-Ping (background thread) ──
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()

    # ── Khởi tạo Application ──
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── Đăng ký Command Handlers ──
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("price", cmd_price))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("check", cmd_check))
    application.add_handler(CommandHandler("threshold", cmd_threshold))
    application.add_handler(CommandHandler("help", cmd_help))

    # ── Bắt đầu Polling ──
    logger.info("🚀 Khởi động Bot Theo Dõi Giá Vàng...")
    logger.info(f"📌 Nguồn dữ liệu: 24h.com.vn")
    logger.info(f"📊 Ngưỡng cảnh báo: {format_price(PRICE_THRESHOLD)}")
    logger.info(f"🔄 Tần suất kiểm tra: {CHECK_INTERVAL_SECONDS // 60} phút")
    logger.info(f"⏱ Thời gian theo dõi giảm: {DROP_MONITOR_WINDOW_SECONDS // 60} phút")
    logger.info(f"🌐 Web server: port {PORT}")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    main()
