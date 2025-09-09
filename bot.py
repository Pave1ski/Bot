import telebot
import requests
import schedule
import time
import threading
import pickle
import os
from datetime import datetime

# ============ НАСТРОЙКИ ============
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Не найден токен! Установи переменную окружения BOT_TOKEN на Railway.")

bot = telebot.TeleBot(TOKEN)
user_criteria = {}
seen_ads = set()

# ============ СОХРАНЕНИЕ / ЗАГРУЗКА ============
def load_data():
    global user_criteria, seen_ads
    if os.path.exists('user_criteria.pkl'):
        with open('user_criteria.pkl', 'rb') as f:
            user_criteria = pickle.load(f)
    if os.path.exists('seen_ads.pkl'):
        with open('seen_ads.pkl', 'rb') as f:
            seen_ads = pickle.load(f)

def save_data():
    with open('user_criteria.pkl', 'wb') as f:
        pickle.dump(user_criteria, f)
    with open('seen_ads.pkl', 'wb') as f:
        pickle.dump(seen_ads, f)

load_data()

# ============ КУРС USD ============
def get_usd_rate():
    try:
        url = "https://www.nbrb.by/api/exrates/rates/USD?parammode=2"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return float(data["Cur_OfficialRate"])
    except:
        pass
    return 3.2

# ============ ПАРСИНГ KUFAR ============
def parse_kufar():
    try:
        url = "https://api.kufar.by/search-api/v1/search/rendered-paginated?cat=1010&typ=let&lang=ru&rgn=7"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return []
        data = response.json()
        ads = []
        for item in data.get("ads", [])[:10]:
            rooms = "Неизвестно"
            for p in item.get("ad_parameters", []):
                if p.get("p") == "rooms":
                    rooms = p.get("v")
            time_raw = item.get("list_time")
            time_str = datetime.fromisoformat(time_raw.replace("Z", "+00:00")).strftime("%d.%m.%Y %H:%M") if time_raw else "Неизвестно"
            price_usd = item.get("price_usd", None)
            ads.append({
                "source": "Kufar",
                "title": item.get("subject", "Нет заголовка"),
                "price_usd": float(price_usd) if price_usd else None,
                "location": item.get("region_name", "Нет локации"),
                "rooms": rooms,
                "description": item.get("body", "Нет описания"),
                "link": f"https://www.kufar.by/item/{item.get('ad_id')}",
                "time": time_str
            })
        return ads
    except:
        return []

# ============ ПАРСИНГ ONLINER ============
def parse_onliner():
    try:
        url = "https://ak.api.onliner.by/search/apartments?rent_type[]=1_room"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            return []
        data = response.json()
        ads = []
        for item in data.get("apartments", [])[:10]:
            time_raw = item.get("last_time_up")
            time_str = datetime.fromisoformat(time_raw).strftime("%d.%m.%Y %H:%M") if time_raw else "Неизвестно"
            price_usd = float(item["price"]["amount"]) if "price" in item else None
            ads.append({
                "source": "Onliner",
                "title": item.get("title", "Нет заголовка"),
                "price_usd": price_usd,
                "location": item.get("location", {}).get("address", "Нет локации"),
                "rooms": item.get("rent_type", "Неизвестно"),
                "description": item.get("contact", {}).get("owner", "Нет описания"),
                "link": item.get("url", ""),
                "time": time_str
            })
        return ads
    except:
        return []

# ============ ПРОВЕРКА И ОТПРАВКА ============
def check_and_send_ads(chat_id):
    criteria = user_criteria.get(chat_id, {})
    max_price_usd = criteria.get("max_price", float("inf"))
    rooms = criteria.get("rooms", None)
    city = criteria.get("city", "").lower()
    usd_rate = get_usd_rate()
    all_ads = parse_kufar() + parse_onliner()
    new_ads = []

    for ad in all_ads:
        if ad["link"] in seen_ads:
            continue
        price_usd = ad.get("price_usd")
        if not price_usd:
            continue
        if price_usd > max_price_usd:
            continue
        if city and city not in ad["location"].lower():
            continue
        if rooms and rooms not in str(ad["rooms"]):
            continue
        new_ads.append(ad)
        seen_ads.add(ad["link"])

    save_data()

    for ad in new_ads:
        try:
            price_usd = ad["price_usd"]
            price_byn = round(price_usd * usd_rate, 2)
            message = (
                f"🏠 Новое объявление ({ad['source']})\n"
                f"<b>{ad['title']}</b>\n"
                f"💰 Цена: {price_usd} USD (~{price_byn} BYN)\n"
                f"📍 Локация: {ad['location']}\n"
                f"🛏 Комнаты: {ad['rooms']}\n"
                f"🕒 Опубликовано: {ad['time']}\n"
                f"📖 {ad['description']}\n"
                f"🔗 <a href='{ad['link']}'>Смотреть</a>"
            )
            bot.send_message(chat_id, message, parse_mode="HTML", disable_web_page_preview=True)
        except:
            pass

# ============ КОМАНДЫ ============
@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "Привет! Я ищу квартиры на Kufar и Onliner.\n"
                          "Установи критерии: /set_criteria <город> <макс_цена_USD> <комнаты?>")

@bot.message_handler(commands=["set_criteria"])
def set_criteria(message):
    try:
        args = message.text.split()[1:]
        city = args[0]
        max_price = float(args[1])
        rooms = args[2] if len(args) > 2 else None
        user_criteria[message.chat.id] = {"city": city, "max_price": max_price, "rooms": rooms}
        setup_schedule()
        save_data()
        bot.reply_to(message, f"✅ Критерии: Город={city}, Цена до {max_price} USD, Комнаты={rooms or 'любые'}")
    except:
        bot.reply_to(message, "❌ Используй: /set_criteria <город> <макс_цена_USD> <комнаты?>")

@bot.message_handler(commands=["check_now"])
def check_now(message):
    if message.chat.id in user_criteria:
        check_and_send_ads(message.chat.id)
        bot.reply_to(message, "🔎 Проверка выполнена!")
    else:
        bot.reply_to(message, "Сначала установи критерии через /set_criteria")

# ============ РАСПИСАНИЕ ============
def setup_schedule():
    schedule.clear()
    for chat_id in user_criteria.keys():
        schedule.every(1).minutes.do(check_and_send_ads, chat_id=chat_id)

def scheduler_thread():
    while True:
        schedule.run_pending()
        time.sleep(1)

# ============ ЗАПУСК ============
if __name__ == "__main__":
    setup_schedule()
    threading.Thread(target=scheduler_thread, daemon=True).start()
    bot.infinity_polling()
