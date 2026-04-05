# ==========================================
# 章魚燒攤 LINE 自動接單系統
# ==========================================

from flask import Flask, request, jsonify, render_template_string, make_response, redirect
import requests
import os
import json
import sqlite3
from datetime import datetime, timedelta
import sys

app = Flask(__name__)
app.logger.setLevel("INFO")
handler = __import__('logging').StreamHandler(sys.stdout)
handler.setLevel("INFO")
app.logger.addHandler(handler)

# ==========================================
# 環境變數
# ==========================================
LINE_TOKEN = os.environ.get("LINE_TOKEN")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# ==========================================
# >>> 每間店需要修改的部分 <<<
# ==========================================

BRAND_NAME = "章魚燒攤"
BOSS_USER_ID = ""  # 等填入老闆的 LINE User ID

# 預設菜單（後台可修改）
DEFAULT_MENU = [
    {"id": 1, "name": "經典原味", "price": 60, "desc": "柴魚片+美乃滋+章魚燒醬"},
    {"id": 2, "name": "明太子", "price": 70, "desc": "明太子醬+海苔粉"},
    {"id": 3, "name": "起司爆漿", "price": 70, "desc": "雙倍起司+章魚燒醬"},
    {"id": 4, "name": "蔥鹽", "price": 65, "desc": "蔥花+鹽味醬+檸檬汁"},
    {"id": 5, "name": "地獄辣味", "price": 65, "desc": "特製辣醬+七味粉"},
]

# 外送設定
DELIVERY_FEE = 30          # 外送費
FREE_DELIVERY_MIN = 200    # 滿額免運
DELIVERY_RADIUS_KM = 2     # 外送範圍（公里）
BATCH_INTERVAL_MIN = 30    # 批次出餐間隔（分鐘）

# 營業時間
BUSINESS_HOURS = {"start": 11, "end": 20}  # 11:00 ~ 20:00

# ==========================================
# 資料庫
# ==========================================
DB_PATH = "/data/orders.db"

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            user_name TEXT DEFAULT '',
            items TEXT NOT NULL,
            total INTEGER NOT NULL,
            delivery_fee INTEGER DEFAULT 0,
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            note TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            batch_time TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    """)
    conn.commit()
    conn.close()

def get_setting(key, default=None):
    try:
        conn = get_db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
        return json.loads(row["value"]) if row else default
    except:
        return default

def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (key, json.dumps(value, ensure_ascii=False)))
    conn.commit()
    conn.close()

def get_menu():
    return get_setting("menu", DEFAULT_MENU)

# ==========================================
# 用戶點餐狀態管理
# ==========================================
# 狀態流程: idle → selecting → confirm_items → address → phone → note → confirm_order
user_states = {}

def get_state(user_id):
    return user_states.get(user_id, {"step": "idle", "cart": [], "address": "", "phone": "", "note": ""})

def set_state(user_id, state):
    user_states[user_id] = state

def clear_state(user_id):
    user_states.pop(user_id, None)

# ==========================================
# LINE API
# ==========================================

def reply_message(reply_token, messages):
    """回覆訊息，messages 可以是單一 dict 或 list"""
    if isinstance(messages, dict):
        messages = [messages]
    requests.post(
        "https://api.line.me/v2/bot/message/reply",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"replyToken": reply_token, "messages": messages[:5]},
        timeout=10
    )

def push_message(user_id, messages):
    if isinstance(messages, dict):
        messages = [messages]
    requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LINE_TOKEN}"},
        json={"to": user_id, "messages": messages[:5]},
        timeout=10
    )

def text_msg(text):
    return {"type": "text", "text": text}

def get_profile(user_id):
    try:
        r = requests.get(
            f"https://api.line.me/v2/bot/profile/{user_id}",
            headers={"Authorization": f"Bearer {LINE_TOKEN}"},
            timeout=5
        )
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return {"displayName": "客人"}

# ==========================================
# Flex Message 模板
# ==========================================

def build_menu_flex():
    """建構菜單 Flex Message"""
    menu = get_menu()
    items = []
    for item in menu:
        items.append({
            "type": "box", "layout": "horizontal", "spacing": "md",
            "contents": [
                {"type": "box", "layout": "vertical", "flex": 4, "contents": [
                    {"type": "text", "text": item["name"], "weight": "bold", "size": "md", "color": "#1a1a1a"},
                    {"type": "text", "text": item["desc"], "size": "xs", "color": "#999999", "wrap": True},
                ]},
                {"type": "box", "layout": "vertical", "flex": 2, "alignItems": "flex-end", "justifyContent": "center", "contents": [
                    {"type": "text", "text": f"${item['price']}", "weight": "bold", "size": "md", "color": "#e85d04"},
                ]},
            ],
            "paddingAll": "12px",
            "backgroundColor": "#ffffff",
            "cornerRadius": "8px",
        })
        items.append({"type": "separator", "margin": "sm", "color": "#f0f0f0"})

    if items:
        items.pop()  # 移除最後一個 separator

    delivery_note = f"滿 ${FREE_DELIVERY_MIN} 免運，未滿收 ${DELIVERY_FEE} 外送費"

    flex = {
        "type": "flex", "altText": "📋 章魚燒菜單",
        "contents": {
            "type": "bubble", "size": "mega",
            "header": {
                "type": "box", "layout": "vertical", "paddingAll": "16px",
                "backgroundColor": "#e85d04",
                "contents": [
                    {"type": "text", "text": "🐙 章魚燒菜單", "weight": "bold", "size": "xl", "color": "#ffffff"},
                    {"type": "text", "text": "每份 6 顆｜現烤現做", "size": "sm", "color": "#ffffffcc", "margin": "sm"},
                ]
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "sm",
                "paddingAll": "16px", "backgroundColor": "#fafafa",
                "contents": items + [
                    {"type": "separator", "margin": "lg", "color": "#e0e0e0"},
                    {"type": "text", "text": delivery_note, "size": "xs", "color": "#888888",
                     "margin": "md", "wrap": True, "align": "center"},
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical", "paddingAll": "12px",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#e85d04",
                     "action": {"type": "message", "label": "🛒 開始點餐", "text": "我要點餐"},
                     "height": "md"},
                ]
            }
        }
    }
    return flex


def build_cart_flex(state):
    """建構購物車確認 Flex Message"""
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}
    cart = state["cart"]

    rows = []
    subtotal = 0
    for c in cart:
        item = menu_map.get(c["id"], {"name": "?", "price": 0})
        line_total = item["price"] * c["qty"]
        subtotal += line_total
        rows.append({
            "type": "box", "layout": "horizontal",
            "contents": [
                {"type": "text", "text": f"{item['name']} x{c['qty']}", "size": "sm", "flex": 3, "color": "#333333"},
                {"type": "text", "text": f"${line_total}", "size": "sm", "flex": 1, "align": "end", "color": "#e85d04"},
            ]
        })

    delivery_fee = 0 if subtotal >= FREE_DELIVERY_MIN else DELIVERY_FEE
    total = subtotal + delivery_fee

    summary = [
        {"type": "separator", "margin": "lg"},
        {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
            {"type": "text", "text": "小計", "size": "sm", "color": "#888888", "flex": 3},
            {"type": "text", "text": f"${subtotal}", "size": "sm", "align": "end", "color": "#333333", "flex": 1},
        ]},
        {"type": "box", "layout": "horizontal", "margin": "sm", "contents": [
            {"type": "text", "text": "外送費", "size": "sm", "color": "#888888", "flex": 3},
            {"type": "text", "text": f"${delivery_fee}" if delivery_fee > 0 else "免運 🎉", "size": "sm", "align": "end",
             "color": "#e85d04" if delivery_fee == 0 else "#333333", "flex": 1},
        ]},
        {"type": "separator", "margin": "md"},
        {"type": "box", "layout": "horizontal", "margin": "md", "contents": [
            {"type": "text", "text": "合計", "size": "lg", "weight": "bold", "color": "#1a1a1a", "flex": 3},
            {"type": "text", "text": f"${total}", "size": "lg", "weight": "bold", "align": "end", "color": "#e85d04", "flex": 1},
        ]},
    ]

    flex = {
        "type": "flex", "altText": f"🛒 購物車 ${total}",
        "contents": {
            "type": "bubble", "size": "kilo",
            "header": {
                "type": "box", "layout": "vertical", "paddingAll": "14px",
                "backgroundColor": "#e85d04",
                "contents": [
                    {"type": "text", "text": "🛒 您的訂單", "weight": "bold", "size": "lg", "color": "#ffffff"},
                ]
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
                "contents": rows + summary
            },
            "footer": {
                "type": "box", "layout": "horizontal", "spacing": "sm", "paddingAll": "12px",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#e85d04",
                     "action": {"type": "message", "label": "✅ 確認送出", "text": "確認送出"},
                     "height": "sm", "flex": 2},
                    {"type": "button", "style": "secondary",
                     "action": {"type": "message", "label": "繼續加點", "text": "繼續加點"},
                     "height": "sm", "flex": 2},
                    {"type": "button", "style": "secondary",
                     "action": {"type": "message", "label": "清空", "text": "清空購物車"},
                     "height": "sm", "flex": 1},
                ]
            }
        }
    }
    return flex


def build_order_confirm_flex(order_id, state, profile_name):
    """建構最終訂單確認 Flex"""
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}
    cart = state["cart"]

    items_text = "\n".join([f"・{menu_map.get(c['id'], {}).get('name', '?')} x{c['qty']}" for c in cart])
    subtotal = sum(menu_map.get(c["id"], {}).get("price", 0) * c["qty"] for c in cart)
    delivery_fee = 0 if subtotal >= FREE_DELIVERY_MIN else DELIVERY_FEE
    total = subtotal + delivery_fee

    # 計算預估送達時間（下一個批次）
    now = datetime.now()
    minutes_to_next = BATCH_INTERVAL_MIN - (now.minute % BATCH_INTERVAL_MIN)
    batch_time = now + timedelta(minutes=minutes_to_next + BATCH_INTERVAL_MIN)  # 加一個批次的製作時間
    eta = batch_time.strftime("%H:%M")

    flex = {
        "type": "flex", "altText": f"✅ 訂單 #{order_id} 已成立！",
        "contents": {
            "type": "bubble", "size": "kilo",
            "header": {
                "type": "box", "layout": "vertical", "paddingAll": "16px",
                "backgroundColor": "#27ae60",
                "contents": [
                    {"type": "text", "text": "✅ 訂單成立！", "weight": "bold", "size": "xl", "color": "#ffffff"},
                    {"type": "text", "text": f"訂單編號 #{order_id}", "size": "sm", "color": "#ffffffcc", "margin": "sm"},
                ]
            },
            "body": {
                "type": "box", "layout": "vertical", "spacing": "lg", "paddingAll": "16px",
                "contents": [
                    {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                        {"type": "text", "text": "📦 訂購內容", "weight": "bold", "size": "sm", "color": "#888888"},
                        {"type": "text", "text": items_text, "size": "sm", "color": "#333333", "wrap": True},
                    ]},
                    {"type": "separator"},
                    {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                        {"type": "text", "text": f"📍 {state['address']}", "size": "sm", "color": "#333333", "wrap": True},
                        {"type": "text", "text": f"📱 {state['phone']}", "size": "sm", "color": "#333333"},
                        {"type": "text", "text": f"💰 合計 ${total}（貨到付款）", "size": "sm", "weight": "bold", "color": "#e85d04"},
                    ]},
                    {"type": "separator"},
                    {"type": "box", "layout": "vertical", "contents": [
                        {"type": "text", "text": f"🕐 預估送達：{eta}", "size": "md", "weight": "bold", "color": "#27ae60", "align": "center"},
                    ]},
                ]
            },
            "footer": {
                "type": "box", "layout": "vertical", "paddingAll": "12px",
                "contents": [
                    {"type": "button", "style": "secondary",
                     "action": {"type": "message", "label": "📋 查詢訂單狀態", "text": "查訂單"},
                     "height": "sm"},
                ]
            }
        }
    }
    return flex


def build_selecting_prompt():
    """建構點餐引導文字"""
    menu = get_menu()
    lines = ["請輸入 品項編號 x 數量 來點餐\n例如：1x2（經典原味 2 份）\n"]
    for item in menu:
        lines.append(f"  {item['id']}. {item['name']} ${item['price']}")
    lines.append("\n可一次點多項，用空格或換行分開")
    lines.append("例如：1x2 3x1\n")
    lines.append("輸入「完成」結算 ／「清空購物車」重來")
    return "\n".join(lines)


# ==========================================
# 點餐流程處理
# ==========================================

def handle_message(user_id, reply_token, text):
    """主要訊息處理邏輯"""
    text = text.strip()
    state = get_state(user_id)
    step = state["step"]

    # ---------- 全域指令 ----------
    if text in ["我的ID", "我的id", "myid"]:
        reply_message(reply_token, text_msg(f"你的 User ID：\n{user_id}"))
        return

    if text in ["菜單", "今日菜單", "menu"]:
        reply_message(reply_token, build_menu_flex())
        return

    if text in ["我要點餐", "點餐", "開始點餐"]:
        state = {"step": "selecting", "cart": [], "address": "", "phone": "", "note": ""}
        set_state(user_id, state)
        reply_message(reply_token, text_msg(build_selecting_prompt()))
        return

    if text in ["查訂單", "訂單狀態", "我的訂單"]:
        handle_check_order(user_id, reply_token)
        return

    if text in ["營業資訊", "營業時間", "外送範圍"]:
        info = (
            f"🐙 {BRAND_NAME}\n\n"
            f"⏰ 營業時間：{BUSINESS_HOURS['start']}:00 ~ {BUSINESS_HOURS['end']}:00\n"
            f"🛵 外送範圍：店面周圍 {DELIVERY_RADIUS_KM} 公里內\n"
            f"💰 滿 ${FREE_DELIVERY_MIN} 免運費，未滿收 ${DELIVERY_FEE} 元\n"
            f"📦 每 {BATCH_INTERVAL_MIN} 分鐘一個批次出餐配送\n\n"
            f"下單方式：點選「我要點餐」開始！"
        )
        reply_message(reply_token, text_msg(info))
        return

    if text in ["聯絡我們", "找老闆", "客服"]:
        reply_message(reply_token, text_msg("📞 有問題嗎？老闆收到後會盡快回覆您！\n\n請直接留言，我們會儘快處理 🙏"))
        if BOSS_USER_ID:
            profile = get_profile(user_id)
            push_message(BOSS_USER_ID, text_msg(f"💬 客人 {profile.get('displayName', '?')} 想聯絡您"))
        return

    if text in ["清空購物車", "清空", "取消"]:
        clear_state(user_id)
        reply_message(reply_token, text_msg("🗑️ 購物車已清空！\n\n想重新點餐請輸入「我要點餐」"))
        return

    # ---------- 點餐流程 ----------
    if step == "selecting":
        handle_selecting(user_id, reply_token, text, state)
        return

    if step == "confirm_items":
        handle_confirm_items(user_id, reply_token, text, state)
        return

    if step == "address":
        handle_address(user_id, reply_token, text, state)
        return

    if step == "phone":
        handle_phone(user_id, reply_token, text, state)
        return

    if step == "note":
        handle_note(user_id, reply_token, text, state)
        return

    if step == "confirm_order":
        handle_final_confirm(user_id, reply_token, text, state)
        return

    # ---------- 預設回覆 ----------
    reply_message(reply_token, [
        text_msg(f"歡迎光臨 {BRAND_NAME}！🐙\n\n請點選下方選單，或輸入以下指令：\n\n📋 菜單 — 看菜單\n🛒 我要點餐 — 開始點餐\n📦 查訂單 — 查看訂單狀態\n⏰ 營業資訊 — 營業時間與外送範圍"),
    ])


def handle_selecting(user_id, reply_token, text, state):
    """處理選餐階段"""
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}

    if text in ["完成", "結算", "好了"]:
        if not state["cart"]:
            reply_message(reply_token, text_msg("購物車是空的喔！請先點餐 😊\n\n" + build_selecting_prompt()))
            return
        state["step"] = "confirm_items"
        set_state(user_id, state)
        reply_message(reply_token, build_cart_flex(state))
        return

    if text == "繼續加點":
        reply_message(reply_token, text_msg(build_selecting_prompt()))
        return

    # 解析點餐指令: "1x2 3x1" or "1*2" or "1 x 2"
    import re
    orders = re.findall(r'(\d+)\s*[xX*×]\s*(\d+)', text)

    if not orders:
        # 嘗試純數字（當作點 1 份）
        single = re.findall(r'^(\d+)$', text.strip())
        if single and int(single[0]) in menu_map:
            orders = [(single[0], "1")]

    if not orders:
        reply_message(reply_token, text_msg("看不懂耶 😅 請用這個格式點餐：\n\n編號x數量\n例如：1x2（經典原味 2 份）\n\n" + build_selecting_prompt()))
        return

    added = []
    for item_id_str, qty_str in orders:
        item_id = int(item_id_str)
        qty = int(qty_str)
        if item_id not in menu_map:
            continue
        if qty < 1 or qty > 20:
            continue
        # 合併同品項
        found = False
        for c in state["cart"]:
            if c["id"] == item_id:
                c["qty"] += qty
                found = True
                break
        if not found:
            state["cart"].append({"id": item_id, "qty": qty})
        added.append(f"{menu_map[item_id]['name']} x{qty}")

    if not added:
        reply_message(reply_token, text_msg("找不到這個品項，請確認編號 🤔\n\n" + build_selecting_prompt()))
        return

    set_state(user_id, state)

    # 顯示目前購物車
    cart_summary = "、".join(added)
    total_items = sum(c["qty"] for c in state["cart"])
    reply_message(reply_token, text_msg(
        f"✅ 已加入：{cart_summary}\n"
        f"🛒 購物車共 {total_items} 份\n\n"
        f"繼續點餐或輸入「完成」結算"
    ))


def handle_confirm_items(user_id, reply_token, text, state):
    """確認購物車"""
    if text in ["確認送出", "確認", "送出"]:
        state["step"] = "address"
        set_state(user_id, state)
        reply_message(reply_token, text_msg("📍 請輸入外送地址：\n\n（越詳細越好，例如：中正路100號3樓）"))
        return

    if text == "繼續加點":
        state["step"] = "selecting"
        set_state(user_id, state)
        reply_message(reply_token, text_msg(build_selecting_prompt()))
        return

    if text in ["清空購物車", "清空"]:
        clear_state(user_id)
        reply_message(reply_token, text_msg("🗑️ 已清空！輸入「我要點餐」重新開始"))
        return

    # 可能還在加點
    state["step"] = "selecting"
    set_state(user_id, state)
    handle_selecting(user_id, reply_token, text, state)


def handle_address(user_id, reply_token, text, state):
    """收集地址"""
    if len(text) < 5:
        reply_message(reply_token, text_msg("地址好像太短了，請提供完整地址 😊\n例如：中正路100號3樓"))
        return
    state["address"] = text
    state["step"] = "phone"
    set_state(user_id, state)
    reply_message(reply_token, text_msg("📱 請輸入聯絡電話：\n\n（送達時會撥打通知您）"))


def handle_phone(user_id, reply_token, text, state):
    """收集電話"""
    import re
    phone = re.sub(r'[\s\-]', '', text)
    if not re.match(r'^0\d{8,9}$', phone):
        reply_message(reply_token, text_msg("電話格式好像不對 😅\n請輸入手機或市話，例如：0912345678"))
        return
    state["phone"] = phone
    state["step"] = "note"
    set_state(user_id, state)
    reply_message(reply_token, text_msg("📝 有什麼備註嗎？\n\n（例如：不要美乃滋、管理室代收...等）\n\n沒有的話輸入「無」"))


def handle_note(user_id, reply_token, text, state):
    """收集備註"""
    state["note"] = "" if text in ["無", "沒有", "沒", "不用", "no", "none"] else text
    state["step"] = "confirm_order"
    set_state(user_id, state)

    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}
    items_text = "\n".join([f"  {menu_map.get(c['id'], {}).get('name', '?')} x{c['qty']}" for c in state["cart"]])
    subtotal = sum(menu_map.get(c["id"], {}).get("price", 0) * c["qty"] for c in state["cart"])
    delivery_fee = 0 if subtotal >= FREE_DELIVERY_MIN else DELIVERY_FEE
    total = subtotal + delivery_fee

    confirm_text = (
        f"📋 請確認您的訂單：\n\n"
        f"{items_text}\n\n"
        f"📍 地址：{state['address']}\n"
        f"📱 電話：{state['phone']}\n"
        f"📝 備註：{state['note'] or '無'}\n\n"
        f"💰 小計 ${subtotal}"
    )
    if delivery_fee > 0:
        confirm_text += f" + 外送費 ${delivery_fee}"
    confirm_text += f"\n💰 合計 ${total}（貨到付款）\n\n確認下單請輸入「確認」\n取消請輸入「取消」"

    reply_message(reply_token, text_msg(confirm_text))


def handle_final_confirm(user_id, reply_token, text, state):
    """最終確認下單"""
    if text in ["確認", "確認下單", "OK", "ok", "好"]:
        order_id = save_order(user_id, state)
        profile = get_profile(user_id)
        profile_name = profile.get("displayName", "客人")

        # 回覆客人
        reply_message(reply_token, build_order_confirm_flex(order_id, state, profile_name))

        # 通知老闆
        notify_boss_new_order(order_id, state, profile_name)

        clear_state(user_id)
        return

    if text in ["取消", "不要", "算了"]:
        clear_state(user_id)
        reply_message(reply_token, text_msg("已取消訂單 🙏\n\n想重新點餐隨時輸入「我要點餐」"))
        return

    reply_message(reply_token, text_msg("請輸入「確認」下單，或「取消」重來"))


# ==========================================
# 訂單處理
# ==========================================

def save_order(user_id, state):
    """儲存訂單到資料庫"""
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}
    subtotal = sum(menu_map.get(c["id"], {}).get("price", 0) * c["qty"] for c in state["cart"])
    delivery_fee = 0 if subtotal >= FREE_DELIVERY_MIN else DELIVERY_FEE
    total = subtotal + delivery_fee

    profile = get_profile(user_id)
    now = datetime.now()

    # 計算批次時間
    minutes_to_next = BATCH_INTERVAL_MIN - (now.minute % BATCH_INTERVAL_MIN)
    batch_time = now + timedelta(minutes=minutes_to_next)

    conn = get_db()
    cur = conn.execute(
        """INSERT INTO orders (user_id, user_name, items, total, delivery_fee, address, phone, note, status, batch_time, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
        (user_id, profile.get("displayName", "客人"), json.dumps(state["cart"], ensure_ascii=False),
         total, delivery_fee, state["address"], state["phone"], state["note"],
         batch_time.strftime("%H:%M"), now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d %H:%M:%S"))
    )
    order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id


def notify_boss_new_order(order_id, state, customer_name):
    """新訂單通知老闆"""
    if not BOSS_USER_ID:
        return
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}
    items_text = "\n".join([f"  {menu_map.get(c['id'], {}).get('name', '?')} x{c['qty']}" for c in state["cart"]])
    subtotal = sum(menu_map.get(c["id"], {}).get("price", 0) * c["qty"] for c in state["cart"])
    delivery_fee = 0 if subtotal >= FREE_DELIVERY_MIN else DELIVERY_FEE
    total = subtotal + delivery_fee

    text = (
        f"🔔 新訂單 #{order_id}！\n\n"
        f"👤 {customer_name}\n"
        f"📦 {items_text}\n"
        f"💰 ${total}\n"
        f"📍 {state['address']}\n"
        f"📱 {state['phone']}\n"
        f"📝 {state['note'] or '無'}\n\n"
        f"回覆「接單 {order_id}」開始製作\n"
        f"回覆「完成 {order_id}」標記送達"
    )
    push_message(BOSS_USER_ID, text_msg(text))


def handle_check_order(user_id, reply_token):
    """客人查詢訂單"""
    conn = get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 3", (user_id,)
    ).fetchall()
    conn.close()

    if not orders:
        reply_message(reply_token, text_msg("您還沒有訂單喔！\n\n輸入「我要點餐」開始 🐙"))
        return

    status_map = {
        "pending": "⏳ 等待接單",
        "preparing": "🔥 製作中",
        "delivering": "🛵 外送中",
        "delivered": "✅ 已送達",
        "cancelled": "❌ 已取消",
    }

    lines = ["📦 您最近的訂單：\n"]
    for o in orders:
        items = json.loads(o["items"])
        menu = get_menu()
        menu_map = {m["id"]: m for m in menu}
        items_str = "、".join([f"{menu_map.get(c['id'], {}).get('name', '?')}x{c['qty']}" for c in items])
        lines.append(
            f"#{o['id']} {status_map.get(o['status'], o['status'])}\n"
            f"  {items_str} — ${o['total']}\n"
            f"  {o['created_at']}\n"
        )

    reply_message(reply_token, text_msg("\n".join(lines)))


def handle_boss_command(user_id, reply_token, text):
    """老闆指令處理"""
    import re

    # 接單
    m = re.match(r'接單\s*(\d+)', text)
    if m:
        order_id = int(m.group(1))
        update_order_status(order_id, "preparing", reply_token)
        return True

    # 外送中
    m = re.match(r'外送\s*(\d+)', text)
    if m:
        order_id = int(m.group(1))
        update_order_status(order_id, "delivering", reply_token)
        return True

    # 完成
    m = re.match(r'完成\s*(\d+)', text)
    if m:
        order_id = int(m.group(1))
        update_order_status(order_id, "delivered", reply_token)
        return True

    # 取消
    m = re.match(r'取消訂單\s*(\d+)', text)
    if m:
        order_id = int(m.group(1))
        update_order_status(order_id, "cancelled", reply_token)
        return True

    # 今日訂單
    if text in ["今日訂單", "所有訂單", "訂單列表"]:
        show_boss_orders(reply_token)
        return True

    return False


def update_order_status(order_id, new_status, reply_token):
    """更新訂單狀態並通知客人"""
    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        reply_message(reply_token, text_msg(f"找不到訂單 #{order_id}"))
        conn.close()
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (new_status, now, order_id))
    conn.commit()
    conn.close()

    status_text = {
        "preparing": "🔥 製作中",
        "delivering": "🛵 外送中",
        "delivered": "✅ 已送達",
        "cancelled": "❌ 已取消",
    }

    # 通知老闆
    reply_message(reply_token, text_msg(f"✅ 訂單 #{order_id} 已更新為：{status_text.get(new_status, new_status)}"))

    # 通知客人
    customer_msg = {
        "preparing": f"🔥 您的訂單 #{order_id} 開始製作囉！請稍候～",
        "delivering": f"🛵 您的訂單 #{order_id} 外送中！即將送達～",
        "delivered": f"✅ 您的訂單 #{order_id} 已送達！謝謝惠顧，歡迎再來 🐙",
        "cancelled": f"❌ 很抱歉，您的訂單 #{order_id} 已取消。如有疑問請聯繫我們 🙏",
    }
    if order["user_id"] and new_status in customer_msg:
        push_message(order["user_id"], text_msg(customer_msg[new_status]))


def show_boss_orders(reply_token):
    """顯示今日訂單給老闆"""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    orders = conn.execute(
        "SELECT * FROM orders WHERE created_at LIKE ? ORDER BY id DESC", (f"{today}%",)
    ).fetchall()
    conn.close()

    if not orders:
        reply_message(reply_token, text_msg("今天還沒有訂單 📭"))
        return

    status_emoji = {"pending": "⏳", "preparing": "🔥", "delivering": "🛵", "delivered": "✅", "cancelled": "❌"}
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}

    lines = [f"📋 今日訂單（共 {len(orders)} 筆）\n"]
    total_revenue = 0
    for o in orders:
        emoji = status_emoji.get(o["status"], "❓")
        items = json.loads(o["items"])
        items_str = "、".join([f"{menu_map.get(c['id'], {}).get('name', '?')}x{c['qty']}" for c in items])
        lines.append(f"{emoji} #{o['id']} {o['user_name']} ${o['total']}\n  {items_str}\n  📍{o['address']}\n")
        if o["status"] != "cancelled":
            total_revenue += o["total"]

    lines.append(f"\n💰 今日營收：${total_revenue}")
    reply_message(reply_token, text_msg("\n".join(lines)))


# ==========================================
# Webhook
# ==========================================

@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json()
    if not body or "events" not in body:
        return jsonify({"status": "ok"})

    for event in body["events"]:
        if event.get("type") != "message" or event["message"].get("type") != "text":
            continue

        user_id = event["source"]["userId"]
        reply_token = event["replyToken"]
        text = event["message"]["text"].strip()

        print(f"[MSG] {user_id}: {text}", flush=True)

        # 老闆指令優先處理
        if user_id == BOSS_USER_ID:
            if handle_boss_command(user_id, reply_token, text):
                continue

        handle_message(user_id, reply_token, text)

    return jsonify({"status": "ok"})


# ==========================================
# 後台管理
# ==========================================

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>章魚燒攤 後台</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#1a1a2e;color:#eee}
.topbar{background:#16213e;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #0f3460}
.topbar h1{font-size:18px;color:#e85d04}
.topbar .status{font-size:12px;color:#27ae60}
.tabs{display:flex;background:#16213e;border-bottom:1px solid #0f3460;padding:0 16px}
.tab{padding:10px 16px;font-size:13px;color:#888;text-decoration:none;border-bottom:2px solid transparent}
.tab.active{color:#e85d04;border-color:#e85d04}
.stats{display:flex;gap:10px;padding:16px}
.stat{background:#16213e;border-radius:10px;padding:14px;flex:1;text-align:center;border:1px solid #0f3460}
.stat-n{font-size:28px;font-weight:700}
.stat-n.orange{color:#e85d04}
.stat-n.green{color:#27ae60}
.stat-n.blue{color:#3498db}
.stat-l{font-size:11px;color:#888;margin-top:4px}
.orders{padding:0 16px 20px}
.order{background:#16213e;border-radius:10px;padding:14px;margin-bottom:10px;border:1px solid #0f3460}
.order-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.order-id{font-weight:700;color:#e85d04}
.order-status{font-size:12px;padding:3px 10px;border-radius:10px;font-weight:500}
.s-pending{background:#f39c1220;color:#f39c12}
.s-preparing{background:#e85d0420;color:#e85d04}
.s-delivering{background:#3498db20;color:#3498db}
.s-delivered{background:#27ae6020;color:#27ae60}
.s-cancelled{background:#e74c3c20;color:#e74c3c}
.order-items{font-size:13px;color:#ccc;margin-bottom:6px}
.order-info{font-size:12px;color:#888;line-height:1.8}
.order-actions{margin-top:10px;display:flex;gap:8px}
.btn{border:none;border-radius:6px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer}
.btn-prepare{background:#e85d04;color:#fff}
.btn-deliver{background:#3498db;color:#fff}
.btn-done{background:#27ae60;color:#fff}
.btn-cancel{background:#333;color:#e74c3c;border:1px solid #e74c3c}
.login-wrap{display:flex;align-items:center;justify-content:center;min-height:100vh}
.login-box{background:#16213e;border-radius:12px;padding:32px;width:300px;text-align:center;border:1px solid #0f3460}
.login-box h2{color:#e85d04;margin-bottom:20px;font-size:18px}
.login-box input{width:100%;padding:10px;border:1px solid #0f3460;border-radius:6px;background:#1a1a2e;color:#eee;font-size:14px;margin-bottom:12px;text-align:center}
.login-box button{width:100%;padding:10px;background:#e85d04;color:#fff;border:none;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer}
.err{color:#e74c3c;font-size:12px;margin-top:8px}
.empty{text-align:center;padding:40px;color:#555}
.toast{position:fixed;bottom:20px;right:20px;background:#27ae60;color:#fff;padding:10px 18px;border-radius:6px;font-size:13px;display:none;z-index:999}
</style>
</head>
<body>
{% if not auth %}
<div class="login-wrap">
  <div class="login-box">
    <h2>🐙 章魚燒攤後台</h2>
    <form method="POST" action="/admin/login">
      <input type="password" name="password" placeholder="請輸入密碼" required>
      <button type="submit">登入</button>
    </form>
    {% if error %}<p class="err">密碼錯誤</p>{% endif %}
  </div>
</div>
{% else %}
<div class="topbar">
  <h1>🐙 章魚燒攤</h1>
  <span class="status">● 系統運作中</span>
</div>
<div class="tabs">
  <a href="/admin" class="tab active">訂單管理</a>
  <a href="/admin/menu" class="tab">菜單設定</a>
</div>
<div class="stats">
  <div class="stat"><div class="stat-n orange">{{ pending }}</div><div class="stat-l">待接單</div></div>
  <div class="stat"><div class="stat-n" style="color:#e85d04">{{ preparing }}</div><div class="stat-l">製作中</div></div>
  <div class="stat"><div class="stat-n blue">{{ delivering }}</div><div class="stat-l">外送中</div></div>
  <div class="stat"><div class="stat-n green">{{ delivered }}</div><div class="stat-l">已完成</div></div>
  <div class="stat"><div class="stat-n green">${{ revenue }}</div><div class="stat-l">今日營收</div></div>
</div>
<div class="orders">
  {% if orders %}
  {% for o in orders %}
  <div class="order">
    <div class="order-header">
      <span class="order-id">#{{ o.id }} {{ o.user_name }}</span>
      <span class="order-status s-{{ o.status }}">{{ o.status_text }}</span>
    </div>
    <div class="order-items">{{ o.items_text }}</div>
    <div class="order-info">
      📍 {{ o.address }}<br>
      📱 {{ o.phone }}<br>
      💰 ${{ o.total }}{% if o.note %}<br>📝 {{ o.note }}{% endif %}<br>
      🕐 {{ o.created_at }}{% if o.batch_time %} ｜批次 {{ o.batch_time }}{% endif %}
    </div>
    <div class="order-actions">
      {% if o.status == 'pending' %}
      <button class="btn btn-prepare" onclick="updateStatus({{ o.id }}, 'preparing')">🔥 接單製作</button>
      <button class="btn btn-cancel" onclick="updateStatus({{ o.id }}, 'cancelled')">取消</button>
      {% elif o.status == 'preparing' %}
      <button class="btn btn-deliver" onclick="updateStatus({{ o.id }}, 'delivering')">🛵 出發外送</button>
      {% elif o.status == 'delivering' %}
      <button class="btn btn-done" onclick="updateStatus({{ o.id }}, 'delivered')">✅ 已送達</button>
      {% endif %}
    </div>
  </div>
  {% endfor %}
  {% else %}
  <div class="empty">今天還沒有訂單 📭</div>
  {% endif %}
</div>
<div class="toast" id="toast"></div>
<script>
function updateStatus(id, status) {
  fetch('/admin/order/update', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({order_id: id, status: status})
  }).then(r => r.json()).then(d => {
    const t = document.getElementById('toast');
    t.textContent = d.msg || '已更新';
    t.style.display = 'block';
    setTimeout(() => { t.style.display = 'none'; location.reload(); }, 1000);
  });
}
setTimeout(() => location.reload(), 15000);
</script>
{% endif %}
</body>
</html>"""

MENU_SETTINGS_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>菜單設定</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#1a1a2e;color:#eee}
.topbar{background:#16213e;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #0f3460}
.topbar h1{font-size:18px;color:#e85d04}
.tabs{display:flex;background:#16213e;border-bottom:1px solid #0f3460;padding:0 16px}
.tab{padding:10px 16px;font-size:13px;color:#888;text-decoration:none;border-bottom:2px solid transparent}
.tab.active{color:#e85d04;border-color:#e85d04}
.main{padding:16px;max-width:600px}
.item{background:#16213e;border-radius:10px;padding:14px;margin-bottom:10px;border:1px solid #0f3460}
.item-row{display:flex;gap:10px;margin-bottom:8px;align-items:center}
.item-row input{background:#1a1a2e;border:1px solid #0f3460;border-radius:6px;padding:8px;color:#eee;font-size:13px}
.item-row input.name{flex:2}
.item-row input.price{flex:1;width:80px}
.item-row input.desc{flex:3}
.btn-del{background:none;border:1px solid #e74c3c;color:#e74c3c;border-radius:6px;padding:6px 12px;cursor:pointer;font-size:12px}
.btn-add{background:#0f3460;color:#3498db;border:1px dashed #3498db;border-radius:10px;padding:14px;text-align:center;cursor:pointer;font-size:14px;margin-bottom:16px}
.btn-save{background:#e85d04;color:#fff;border:none;border-radius:8px;padding:12px 32px;font-size:14px;font-weight:600;cursor:pointer;display:block;margin:0 auto}
.toast{position:fixed;bottom:20px;right:20px;background:#27ae60;color:#fff;padding:10px 18px;border-radius:6px;font-size:13px;display:none;z-index:999}
.settings-section{background:#16213e;border-radius:10px;padding:16px;margin-bottom:16px;border:1px solid #0f3460}
.settings-section h3{font-size:14px;color:#e85d04;margin-bottom:12px}
.settings-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.settings-row label{font-size:13px;color:#888;min-width:100px}
.settings-row input{background:#1a1a2e;border:1px solid #0f3460;border-radius:6px;padding:8px;color:#eee;font-size:13px;width:120px}
</style>
</head>
<body>
<div class="topbar"><h1>🐙 章魚燒攤</h1></div>
<div class="tabs">
  <a href="/admin" class="tab">訂單管理</a>
  <a href="/admin/menu" class="tab active">菜單設定</a>
</div>
<div class="main">
  <h3 style="color:#888;font-size:12px;margin-bottom:12px;letter-spacing:2px">菜單品項</h3>
  <div id="menu-list"></div>
  <div class="btn-add" onclick="addItem()">+ 新增品項</div>

  <div class="settings-section">
    <h3>外送設定</h3>
    <div class="settings-row">
      <label>外送費</label>
      <input type="number" id="delivery_fee" value="{{ delivery_fee }}"> 元
    </div>
    <div class="settings-row">
      <label>滿額免運</label>
      <input type="number" id="free_min" value="{{ free_min }}"> 元
    </div>
    <div class="settings-row">
      <label>出餐間隔</label>
      <input type="number" id="batch_interval" value="{{ batch_interval }}"> 分鐘
    </div>
    <div class="settings-row">
      <label>營業開始</label>
      <input type="number" id="hour_start" value="{{ hour_start }}"> 時
    </div>
    <div class="settings-row">
      <label>營業結束</label>
      <input type="number" id="hour_end" value="{{ hour_end }}"> 時
    </div>
  </div>

  <button class="btn-save" onclick="saveAll()">💾 儲存所有設定</button>
</div>
<div class="toast" id="toast"></div>
<script>
let menuData = {{ menu_json|safe }};

function render() {
  const list = document.getElementById('menu-list');
  list.innerHTML = '';
  menuData.forEach((item, i) => {
    list.innerHTML += `
      <div class="item">
        <div class="item-row">
          <input class="name" value="${item.name}" onchange="menuData[${i}].name=this.value" placeholder="品名">
          <input class="price" type="number" value="${item.price}" onchange="menuData[${i}].price=parseInt(this.value)" placeholder="價格">
          <button class="btn-del" onclick="menuData.splice(${i},1);render()">刪除</button>
        </div>
        <div class="item-row">
          <input class="desc" value="${item.desc}" onchange="menuData[${i}].desc=this.value" placeholder="描述" style="flex:1">
        </div>
      </div>`;
  });
}

function addItem() {
  const newId = menuData.length > 0 ? Math.max(...menuData.map(m=>m.id)) + 1 : 1;
  menuData.push({id: newId, name: '', price: 0, desc: ''});
  render();
}

function saveAll() {
  // 重新編號
  menuData.forEach((m, i) => m.id = i + 1);
  const payload = {
    menu: menuData,
    delivery_fee: parseInt(document.getElementById('delivery_fee').value),
    free_min: parseInt(document.getElementById('free_min').value),
    batch_interval: parseInt(document.getElementById('batch_interval').value),
    hour_start: parseInt(document.getElementById('hour_start').value),
    hour_end: parseInt(document.getElementById('hour_end').value),
  };
  fetch('/admin/menu/save', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  }).then(r => r.json()).then(d => {
    const t = document.getElementById('toast');
    t.textContent = '✅ 設定已儲存！';
    t.style.display = 'block';
    setTimeout(() => t.style.display = 'none', 2000);
  });
}

render();
</script>
</body>
</html>"""


@app.route("/admin")
def admin():
    auth = request.cookies.get("admin_auth") == ADMIN_PASSWORD
    if not auth:
        html = render_template_string(ADMIN_HTML, auth=False, error=False,
                                       orders=[], pending=0, preparing=0, delivering=0, delivered=0, revenue=0)
        resp = make_response(html)
        resp.headers['Content-Type'] = 'text/html; charset=utf-8'
        return resp

    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders WHERE created_at LIKE ? ORDER BY id DESC", (f"{today}%",)).fetchall()
    conn.close()

    status_text_map = {"pending": "⏳ 待接單", "preparing": "🔥 製作中", "delivering": "🛵 外送中",
                       "delivered": "✅ 已送達", "cancelled": "❌ 已取消"}
    menu = get_menu()
    menu_map = {m["id"]: m for m in menu}

    orders = []
    revenue = 0
    counts = {"pending": 0, "preparing": 0, "delivering": 0, "delivered": 0}
    for r in rows:
        items = json.loads(r["items"])
        items_text = "、".join([f"{menu_map.get(c['id'], {}).get('name', '?')} x{c['qty']}" for c in items])
        orders.append({
            "id": r["id"], "user_name": r["user_name"], "items_text": items_text,
            "total": r["total"], "address": r["address"], "phone": r["phone"],
            "note": r["note"], "status": r["status"],
            "status_text": status_text_map.get(r["status"], r["status"]),
            "created_at": r["created_at"], "batch_time": r["batch_time"],
        })
        if r["status"] in counts:
            counts[r["status"]] += 1
        if r["status"] not in ("cancelled",):
            revenue += r["total"]

    html = render_template_string(ADMIN_HTML, auth=True, error=False, orders=orders,
                                   pending=counts["pending"], preparing=counts["preparing"],
                                   delivering=counts["delivering"], delivered=counts["delivered"], revenue=revenue)
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route("/admin/login", methods=["POST"])
def admin_login():
    if request.form.get("password") == ADMIN_PASSWORD:
        resp = make_response(redirect("/admin"))
        resp.set_cookie("admin_auth", ADMIN_PASSWORD, max_age=86400 * 7)
        return resp
    html = render_template_string(ADMIN_HTML, auth=False, error=True,
                                   orders=[], pending=0, preparing=0, delivering=0, delivered=0, revenue=0)
    return make_response(html)


@app.route("/admin/order/update", methods=["POST"])
def admin_order_update():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    order_id = data.get("order_id")
    new_status = data.get("status")

    conn = get_db()
    order = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return jsonify({"error": "not found"}), 404

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (new_status, now, order_id))
    conn.commit()
    conn.close()

    # 通知客人
    status_msg = {
        "preparing": f"🔥 您的訂單 #{order_id} 開始製作囉！",
        "delivering": f"🛵 您的訂單 #{order_id} 外送中！即將送達～",
        "delivered": f"✅ 您的訂單 #{order_id} 已送達！謝謝惠顧 🐙",
        "cancelled": f"❌ 您的訂單 #{order_id} 已取消，如有疑問請聯繫我們 🙏",
    }
    if order["user_id"] and new_status in status_msg:
        push_message(order["user_id"], text_msg(status_msg[new_status]))

    return jsonify({"status": "ok", "msg": f"訂單 #{order_id} 已更新"})


@app.route("/admin/menu")
def admin_menu():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return redirect("/admin")
    menu = get_menu()
    settings = get_setting("shop_settings", {})
    html = render_template_string(
        MENU_SETTINGS_HTML,
        menu_json=json.dumps(menu, ensure_ascii=False),
        delivery_fee=settings.get("delivery_fee", DELIVERY_FEE),
        free_min=settings.get("free_min", FREE_DELIVERY_MIN),
        batch_interval=settings.get("batch_interval", BATCH_INTERVAL_MIN),
        hour_start=settings.get("hour_start", BUSINESS_HOURS["start"]),
        hour_end=settings.get("hour_end", BUSINESS_HOURS["end"]),
    )
    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp


@app.route("/admin/menu/save", methods=["POST"])
def admin_menu_save():
    if request.cookies.get("admin_auth") != ADMIN_PASSWORD:
        return jsonify({"error": "unauthorized"}), 401
    data = request.get_json()
    set_setting("menu", data.get("menu", []))
    set_setting("shop_settings", {
        "delivery_fee": data.get("delivery_fee", DELIVERY_FEE),
        "free_min": data.get("free_min", FREE_DELIVERY_MIN),
        "batch_interval": data.get("batch_interval", BATCH_INTERVAL_MIN),
        "hour_start": data.get("hour_start", BUSINESS_HOURS["start"]),
        "hour_end": data.get("hour_end", BUSINESS_HOURS["end"]),
    })
    # 更新全域變數
    global DELIVERY_FEE, FREE_DELIVERY_MIN, BATCH_INTERVAL_MIN, BUSINESS_HOURS
    DELIVERY_FEE = data.get("delivery_fee", DELIVERY_FEE)
    FREE_DELIVERY_MIN = data.get("free_min", FREE_DELIVERY_MIN)
    BATCH_INTERVAL_MIN = data.get("batch_interval", BATCH_INTERVAL_MIN)
    BUSINESS_HOURS["start"] = data.get("hour_start", BUSINESS_HOURS["start"])
    BUSINESS_HOURS["end"] = data.get("hour_end", BUSINESS_HOURS["end"])
    return jsonify({"status": "ok"})


@app.route("/")
def health():
    return "🐙 章魚燒攤自動接單系統運作中 ✅"


# ==========================================
# 啟動
# ==========================================
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
