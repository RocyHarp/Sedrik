import streamlit as st
import requests
import time
import urllib.parse
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import RealDictCursor
import pandas as pd
from datetime import datetime
import json

# --- НАЛАШТУВАННЯ СТОРІНКИ ---
st.set_page_config(
    page_title="Sedrik Multi-Tool",
    page_icon="🛠",
    layout="wide",
    initial_sidebar_state="expanded"
)

STEAM_APP_ID_DOTA = 570
STEAM_APP_ID_CS2 = 730
CURRENCY_UAH = 18

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
    'Referer': 'https://liquipedia.net/'
}
session = requests.Session()
session.headers.update(HEADERS)

# --- ІНІЦІАЛІЗАЦІЯ ПАМ'ЯТІ (СЕСІЇ) ---
if 'dota_result' not in st.session_state:
    st.session_state.dota_result = None
if 'cs_result' not in st.session_state:
    st.session_state.cs_result = None
if 'lib_result' not in st.session_state:
    st.session_state.lib_result = None

# --- БАЗА ДАНИХ (NEON) ---
def get_db_connection():
    return psycopg2.connect(st.secrets["DB_URL"])

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Звіти (Продано)
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY, date TEXT, set_name TEXT, 
            strategy TEXT, cost INTEGER, profit INTEGER
        )
    ''')
    # Портфель (Активні лоти)
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id SERIAL PRIMARY KEY, date TEXT, item_name TEXT, 
            buy_price INTEGER, target_price INTEGER
        )
    ''')
    # БІБЛІОТЕКА СЕТІВ (Збережені склади)
    c.execute('''
        CREATE TABLE IF NOT EXISTS library (
            id SERIAL PRIMARY KEY, set_name TEXT UNIQUE, hero TEXT, 
            rarity TEXT, image_url TEXT, components TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_to_reports(set_name, strategy, cost, profit):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    c.execute('INSERT INTO trades (date, set_name, strategy, cost, profit) VALUES (%s, %s, %s, %s, %s)',
              (now, set_name, strategy, cost, profit))
    conn.commit()
    conn.close()

def add_to_portfolio(item_name, buy_price, target_price):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d")
    c.execute('INSERT INTO portfolio (date, item_name, buy_price, target_price) VALUES (%s, %s, %s, %s)',
              (now, item_name, buy_price, target_price))
    conn.commit()
    conn.close()

def add_to_library(set_name, hero, rarity, image_url, components):
    conn = get_db_connection()
    c = conn.cursor()
    # Перевіряємо, чи такий сет вже є
    c.execute("SELECT id FROM library WHERE set_name = %s", (set_name,))
    if not c.fetchone():
        c.execute('''
            INSERT INTO library (set_name, hero, rarity, image_url, components) 
            VALUES (%s, %s, %s, %s, %s)
        ''', (set_name, hero, rarity, image_url, json.dumps(components)))
    conn.commit()
    conn.close()

init_db()

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
def get_clean_income(market_price: float) -> int:
    market_price = int(market_price)
    if market_price <= 0: return 0
    for base in range(market_price, 0, -1):
        dota_fee = max(1, int(base * 0.10 + 0.5))
        steam_fee = max(1, int(base * 0.05 + 0.5))
        if base + dota_fee + steam_fee <= market_price: 
            return base
    return 0

def get_steam_client_url(item_name: str, app_id: int) -> str:
    return f"steam://openurl/https://steamcommunity.com/market/listings/{app_id}/{urllib.parse.quote(item_name)}"

def get_steam_web_url(item_name: str, app_id: int) -> str:
    return f"https://steamcommunity.com/market/listings/{app_id}/{urllib.parse.quote(item_name)}"

# --- КЕШОВАНІ ЗАПИТИ ---
@st.cache_data(ttl=86400, show_spinner=False)
def search_correct_page_name(query: str) -> str:
    url = "https://liquipedia.net/dota2/api.php"
    params = {"action": "query", "list": "search", "srsearch": query, "utf8": "", "format": "json"}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=10)
        results = res.json().get("query", {}).get("search", [])
        if results: return results[0]["title"]
    except: pass
    return query

@st.cache_data(ttl=86400, show_spinner=False)
def get_full_set_info(exact_page_name: str) -> dict:
    url = "https://liquipedia.net/dota2/api.php"
    params = {"action": "parse", "page": exact_page_name.replace(" ", "_"), "format": "json", "prop": "text"}
    result = {"components": [], "hero": "Невідомий", "rarity": "Невідома", "image_url": None}
    try:
        res = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = res.json()
        if "error" in data: return result
        soup = BeautifulSoup(data["parse"]["text"]["*"], "html.parser")
        
        infobox = soup.find(class_=lambda x: x and 'infobox' in x.lower())
        if infobox:
            for tag in infobox.find_all(['div', 'th', 'td']):
                clean_text = tag.get_text(strip=True).replace(":", "").strip()
                if clean_text == "Hero":
                    nxt = tag.find_next_sibling(['div', 'td'])
                    if nxt: result["hero"] = nxt.get_text(strip=True)
                elif clean_text == "Rarity":
                    nxt = tag.find_next_sibling(['div', 'td'])
                    if nxt: result["rarity"] = nxt.get_text(strip=True)
                    
        img_div = soup.find("div", class_="infobox-image")
        if img_div and img_div.find("img"):
            src = img_div.find("img")["src"]
            result["image_url"] = "https://liquipedia.net" + src if src.startswith("/") else src
                    
        header = soup.find(lambda tag: tag.name in ['h2', 'h3'] and 'set items' in tag.get_text(strip=True).lower())
        if header:
            for tag in header.find_all_next():
                if tag.name in ['h2', 'h3']: break
                if tag.name == 'a':
                    name = tag.get_text(strip=True)
                    if len(name) > 2 and "edit" not in name.lower() and "modifier" not in name.lower() and name.lower() != exact_page_name.lower() and name not in result["components"]:
                        result["components"].append(name)
    except: pass
    return result

@st.cache_data(ttl=300, show_spinner=False) # Пам'ятає ціни 5 хвилин для швидкого оновлення
def get_steam_price_data(item_name: str, app_id: int) -> dict:
    url = f"https://steamcommunity.com/market/priceoverview/?appid={app_id}&currency={CURRENCY_UAH}&market_hash_name={urllib.parse.quote(item_name)}"
    result = {"price": 0, "volume": 0, "median": 0}
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                if "lowest_price" in data:
                    result["price"] = int(float(data["lowest_price"].replace("₴", "").replace(" ", "").replace(",", ".")))
                if "median_price" in data:
                    result["median"] = int(float(data["median_price"].replace("₴", "").replace(" ", "").replace(",", ".")))
                if "volume" in data:
                    result["volume"] = int(data["volume"].replace(",", "")) * 7
                elif app_id == STEAM_APP_ID_CS2 and "volume" in data:
                    result["volume"] = int(data["volume"].replace(",", ""))
        time.sleep(1.3) # Захист від бану Steam API
    except: pass
    return result

# --- ФУНКЦІЯ МАЛЮВАННЯ ДАШБОРДУ DOTA (ЩОБ НЕ ДУБЛЮВАТИ КОД) ---
def render_dota_dashboard(res, prefix_key="scan"):
    """Малює картинку, інформацію, кнопки і стратегії для знайденого сету."""
    col_img, col_info = st.columns([1, 3])
    with col_img:
        if res['set_info']["image_url"]:
            try:
                img_res = requests.get(res['set_info']["image_url"], timeout=5)
                if img_res.status_code == 200: st.image(img_res.content, use_container_width=True)
            except: st.info("📷 Помилка фото")
        else: st.info("📷 Фото не знайдено")
            
    with col_info:
        st.subheader(f"📦 {res['exact_name']}")
        st.markdown(f"**🧙‍♂️ Герой:** `{res['set_info']['hero']}` | **💎 Рідкість:** `{res['set_info']['rarity']}`")
        
        # КНОПКИ ПОРУЧ
        col_link, col_lib = st.columns(2)
        with col_link:
            st.markdown(f"<a href='{get_steam_client_url(res['exact_name'], STEAM_APP_ID_DOTA)}' style='display: inline-block; padding: 8px 15px; background-color: #1a2838; color: #66c0f4; text-decoration: none; border-radius: 5px; border: 1px solid #101822; width: 100%; text-align: center;'>Відкрити бандл у Steam 🔗</a>", unsafe_allow_html=True)
        with col_lib:
            if st.button("📚 Зберегти в Бібліотеку", key=f"btn_lib_{prefix_key}", use_container_width=True):
                add_to_library(res['exact_name'], res['set_info']['hero'], res['set_info']['rarity'], res['set_info']['image_url'], res['set_info']['components'])
                st.success("✅ Сет назавжди збережено в Бібліотеці!")

    st.divider()
    tab1, tab2 = st.tabs(["🎁 СТРАТЕГІЯ 1: ПАКУВАННЯ", "✂️ СТРАТЕГІЯ 2: РОЗПАКУВАННЯ"])
    
    safe_key = f"{prefix_key}_{res['exact_name']}"
    
    with tab1:
        col_in1, col_in2 = st.columns(2)
        user_buy_price = col_in1.number_input("За скільки КУПИВ всі частини (сума):", value=float(res['total_parts_price']), step=1.0, key=f"buy_pack_{safe_key}")
        user_sell_price = col_in2.number_input("За скільки ПРОДАШ цілий бандл (брутто):", value=float(res['bundle_data']['price']), step=1.0, key=f"sell_pack_{safe_key}")
        
        actual_bundle_income = get_clean_income(user_sell_price)
        actual_pack_profit = actual_bundle_income - user_buy_price
        
        st.info(f"Твій чистий дохід після комісії Steam: **{actual_bundle_income} ₴**")
        
        if actual_pack_profit > 0: st.metric("Очікуваний профіт", f"{actual_pack_profit} ₴", delta="ПЛЮС")
        else: st.metric("Очікуваний профіт", f"{actual_pack_profit} ₴", delta="МІНУС", delta_color="inverse")

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("📊 Додати у ЗВІТ (Вже продано)", type="primary", use_container_width=True, key=f"rep_pack_{safe_key}"):
                if user_buy_price > 0:
                    save_to_reports(res['exact_name'], "Пакування", int(user_buy_price), int(actual_pack_profit))
                    st.success("✅ Записано в базу Звітів!")
                else: st.warning("Введи ціну закупки!")
        with btn_col2:
            if st.button("💼 Додати у ПОРТФЕЛЬ (Ще лежить)", use_container_width=True, key=f"port_pack_{safe_key}"):
                if user_buy_price > 0:
                    add_to_portfolio(res['exact_name'] + " (Збірка)", int(user_buy_price), int(user_sell_price))
                    st.success("✅ Записано в Портфель!")
                else: st.warning("Введи ціну закупки!")

    with tab2:
        col_in1, col_in2 = st.columns(2)
        user_bundle_buy = col_in1.number_input("За скільки КУПИВ цілий бандл:", value=float(res['bundle_data']['price']), step=1.0, key=f"buy_unpack_{safe_key}")
        user_parts_sell = col_in2.number_input("За скільки ПРОДАШ всі деталі (брутто):", value=float(res['total_parts_price']), step=1.0, key=f"sell_unpack_{safe_key}")
        
        actual_parts_income = get_clean_income(user_parts_sell)
        actual_unpack_profit = actual_parts_income - user_bundle_buy
        
        st.info(f"Твій чистий дохід після комісій Steam: **{actual_parts_income} ₴**")
        
        if actual_unpack_profit > 0: st.metric("Очікуваний профіт", f"{actual_unpack_profit} ₴", delta="ПЛЮС")
        else: st.metric("Очікуваний профіт", f"{actual_unpack_profit} ₴", delta="МІНУС", delta_color="inverse")

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("📊 Додати у ЗВІТ (Вже продано)", type="primary", use_container_width=True, key=f"rep_unpack_{safe_key}"):
                if user_bundle_buy > 0:
                    save_to_reports(res['exact_name'], "Розпакування", int(user_bundle_buy), int(actual_unpack_profit))
                    st.success("✅ Записано в базу Звітів!")
                else: st.warning("Введи ціну закупки!")
        with btn_col2:
            if st.button("💼 Додати у ПОРТФЕЛЬ (Ще лежить)", use_container_width=True, key=f"port_unpack_{safe_key}"):
                if user_bundle_buy > 0:
                    add_to_portfolio(res['exact_name'] + " (Розпаковка)", int(user_bundle_buy), int(user_parts_sell))
                    st.success("✅ Записано в Портфель!")
                else: st.warning("Введи ціну закупки!")

    st.divider()
    html_table = "<style>.st-table { width: 100%; border-collapse: collapse; } .st-table th, .st-table td { padding: 8px; border-bottom: 1px solid #333; } .st-table a { color: #66c0f4; text-decoration: none; }</style><table class='st-table'><tr><th>Деталь</th><th>Ціна (₴)</th><th>Тобі (₴)</th><th>Продажі</th><th>Дія</th></tr>"
    for p in res['parts_data']: html_table += f"<tr><td>{p['Деталь']}</td><td>{p['Ціна']}</td><td>{p['Чистими']}</td><td>~{p['Продажі']}</td><td><a href='{p['Link']}'>Купити 🛒</a></td></tr>"
    st.markdown(html_table + "</table>", unsafe_allow_html=True)


# ==========================================
# БОКОВЕ МЕНЮ
# ==========================================
with st.sidebar:
    st.title("🛠 Sedrik Multi-Tool")
    st.markdown("`v11.0 | Library Edition`")
    st.divider()
    menu_choice = st.radio("РОЗДІЛИ:", ["🔍 Dota 2 Сканер", "📚 Бібліотека", "🔫 CS2 Скан", "💼 Портфель", "📊 Звіти (База)"])
    st.divider()

# ==========================================
# СТОРІНКА 1: DOTA 2 СКАНЕР
# ==========================================
if menu_choice == "🔍 Dota 2 Сканер":
    st.header("🔍 Арбітражний сканер сетів Dota 2")
    with st.sidebar:
        query = st.text_input("Введи назву сету:", placeholder="Наприклад: Guilt of the Survivor")
        analyze_btn = st.button("🚀 ЗНАЙТИ ПРОФІТ", type="primary", use_container_width=True)

    if analyze_btn and query:
        st.session_state.dota_result = None 
        
        with st.sidebar:
            status_text = st.empty()
            progress_bar = st.progress(0)
            
            exact_name = search_correct_page_name(query.strip())
            set_info = get_full_set_info(exact_name)
            items = set_info["components"]
            
        if not items:
            st.error(f"Не вдалося знайти деталі для '{exact_name}'. Перевір назву.")
        else:
            with st.sidebar:
                bundle_data = get_steam_price_data(exact_name, STEAM_APP_ID_DOTA)
                parts_data = []
                total_parts_price = 0
                total_parts_clean_income = 0
                
                for i, item in enumerate(items):
                    status_text.text(f"Сканування ({i+1}/{len(items)}): {item}")
                    data = get_steam_price_data(item, STEAM_APP_ID_DOTA)
                    clean_part = get_clean_income(data['price'])
                    
                    parts_data.append({
                        "Деталь": item, "Ціна": data['price'], "Чистими": clean_part,
                        "Продажі": data['volume'], "Link": get_steam_client_url(item, STEAM_APP_ID_DOTA)
                    })
                    total_parts_price += data['price']
                    total_parts_clean_income += clean_part
                    progress_bar.progress((i + 1) / len(items))
                
                st.session_state.dota_result = {
                    "exact_name": exact_name, "set_info": set_info, "bundle_data": bundle_data,
                    "parts_data": parts_data, "total_parts_price": total_parts_price,
                    "total_parts_clean_income": total_parts_clean_income
                }
                status_text.success("✅ Завершено!")
                time.sleep(1)
                status_text.empty(); progress_bar.empty()

    if st.session_state.dota_result:
        render_dota_dashboard(st.session_state.dota_result, prefix_key="scan")

# ==========================================
# СТОРІНКА 1.5: БІБЛІОТЕКА СЕТІВ
# ==========================================
elif menu_choice == "📚 Бібліотека":
    st.header("📚 Твоя Бібліотека Сетів")
    st.markdown("Збережені сети. Оновлення цін звідси працює **моментально**, бо ми пропускаємо Liquipedia!")
    
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM library ORDER BY set_name ASC")
    lib_sets = c.fetchall()
    conn.close()
    
    if not lib_sets:
        st.info("Бібліотека порожня. Знайди сет у 'Сканері' та натисни 'Зберегти в Бібліотеку'.")
    else:
        set_names = [s['set_name'] for s in lib_sets]
        selected_set_name = st.selectbox("Обери сет з бази:", set_names)
        
        selected_set = next(s for s in lib_sets if s['set_name'] == selected_set_name)
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            update_btn = st.button("🔄 Оновити ціни зі Steam (Швидко)", type="primary", use_container_width=True)
        with col_btn2:
            if st.button("❌ Видалити сет з Бібліотеки", use_container_width=True):
                conn = get_db_connection()
                conn.cursor().execute("DELETE FROM library WHERE id = %s", (selected_set['id'],))
                conn.commit(); conn.close()
                st.success("Сет видалено!")
                time.sleep(1); st.rerun()

        if update_btn:
            st.session_state.lib_result = None
            with st.spinner("Швидке сканування Steam..."):
                components = json.loads(selected_set['components'])
                
                # Відновлюємо set_info з бази
                set_info = {
                    "hero": selected_set['hero'],
                    "rarity": selected_set['rarity'],
                    "image_url": selected_set['image_url'],
                    "components": components
                }
                
                bundle_data = get_steam_price_data(selected_set_name, STEAM_APP_ID_DOTA)
                parts_data = []
                total_parts_price = 0
                total_parts_clean_income = 0
                
                # Швидкий цикл
                my_bar = st.progress(0)
                for i, item in enumerate(components):
                    data = get_steam_price_data(item, STEAM_APP_ID_DOTA)
                    clean_part = get_clean_income(data['price'])
                    
                    parts_data.append({
                        "Деталь": item, "Ціна": data['price'], "Чистими": clean_part,
                        "Продажі": data['volume'], "Link": get_steam_client_url(item, STEAM_APP_ID_DOTA)
                    })
                    total_parts_price += data['price']
                    total_parts_clean_income += clean_part
                    my_bar.progress((i + 1) / len(components))
                my_bar.empty()
                
                st.session_state.lib_result = {
                    "exact_name": selected_set_name, "set_info": set_info, "bundle_data": bundle_data,
                    "parts_data": parts_data, "total_parts_price": total_parts_price,
                    "total_parts_clean_income": total_parts_clean_income
                }
                st.toast("✅ Ціни оновлено!")

        # Якщо є збережений результат для ЦЬОГО сету - малюємо дашборд
        if st.session_state.lib_result and st.session_state.lib_result['exact_name'] == selected_set_name:
            st.divider()
            render_dota_dashboard(st.session_state.lib_result, prefix_key="lib")

# ==========================================
# СТОРІНКА 2: CS2 СКАН
# ==========================================
elif menu_choice == "🔫 CS2 Скан":
    st.header("🔫 Аналіз скінів CS2")
    st.markdown("[🔗 Відкрити Ринок CS2 в Steam, щоб скопіювати точну назву](https://steamcommunity.com/market/search?appid=730)")
    
    cs_item_raw = st.text_input("Введи ТОЧНУ назву (з великими літерами та якістю):", placeholder="Наприклад: AK-47 | Slate (Field-Tested)")
    search_cs_btn = st.button("Знайти скін")
    
    if search_cs_btn and cs_item_raw:
        st.session_state.cs_result = None
        cs_item = cs_item_raw.strip()
        with st.spinner("Отримую дані зі Steam..."):
            cs_data = get_steam_price_data(cs_item, STEAM_APP_ID_CS2)
            st.session_state.cs_result = {"name": cs_item, "data": cs_data}
            
    if st.session_state.cs_result:
        res = st.session_state.cs_result
        if res['data']['price'] > 0 or res['data']['median'] > 0:
            col1, col2, col3 = st.columns(3)
            col1.metric("Ціна продажу (Lowest)", f"{res['data']['price']} ₴")
            col2.metric("Середня ціна (Median)", f"{res['data']['median']} ₴")
            col3.metric("Продано за 24г", f"{res['data']['volume']} шт.")
            
            clean = get_clean_income(res['data']['price'])
            st.info(f"Твій чистий дохід при продажі по мінімалці: **{clean} ₴**")
            
            web_url = get_steam_web_url(res['name'], STEAM_APP_ID_CS2)
            client_url = get_steam_client_url(res['name'], STEAM_APP_ID_CS2)
            
            st.markdown(f"[🔗 Відкрити лот у Браузері (Перевірити Buy Orders)]({web_url})")
            st.markdown(f"[🔗 Відкрити лот у додатку Steam]({client_url})")
            
            st.divider()
            st.subheader("Збереження угоди")
            
            safe_cs_key = str(res['name'])
            col_in1, col_in2 = st.columns(2)
            p_buy = col_in1.number_input("Твоя ціна закупки (₴):", value=float(res['data']['price']), step=1.0, key=f"cs_buy_{safe_cs_key}")
            p_target = col_in2.number_input("Твоя цільова ціна продажу (₴):", value=float(res['data']['price']*1.2), step=1.0, key=f"cs_target_{safe_cs_key}")
            
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("📊 Додати у ЗВІТ (Вже продано)", type="primary", use_container_width=True, key=f"rep_cs_{safe_cs_key}"):
                    cs_profit = get_clean_income(p_target) - p_buy
                    save_to_reports(res['name'], "CS2 Трейд", int(p_buy), int(cs_profit))
                    st.success("✅ Записано в базу Звітів!")
            with btn_col2:
                if st.button("💼 Додати у ПОРТФЕЛЬ (Ще лежить)", use_container_width=True, key=f"port_cs_{safe_cs_key}"):
                    add_to_portfolio(res['name'], int(p_buy), int(p_target))
                    st.success("✅ Додано в Портфель!")
        else:
            st.error("❌ Скін не знайдено. Перевір кожну літеру та пробіл!")

# ==========================================
# СТОРІНКА 3: ПОРТФЕЛЬ
# ==========================================
elif menu_choice == "💼 Портфель":
    st.header("💼 Твої активні інвестиції")
    
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM portfolio ORDER BY id DESC")
    df_p = pd.DataFrame(c.fetchall())
    conn.close()
    
    if df_p.empty:
        st.info("Твій портфель поки що порожній.")
    else:
        df_p_display = df_p.rename(columns={
            "id": "ID", "date": "Дата покупки", "item_name": "Назва Лоту", 
            "buy_price": "Ціна покупки (₴)", "target_price": "Ціль продажу (₴)"
        })
        st.dataframe(df_p_display, hide_index=True, use_container_width=True)
        
        st.divider()
        st.subheader("🛠 Управління лотами")
        
        lot_ids = df_p['id'].tolist()
        selected_id = st.selectbox("Обери ID лоту для управління:", lot_ids)
        
        if selected_id:
            current_row = df_p[df_p['id'] == selected_id].iloc[0]
            item_name = current_row['item_name']
            
            st.markdown(f"**Редагування лоту:** `{item_name}`")
            
            col1, col2 = st.columns(2)
            new_buy = col1.number_input("Ціна покупки (₴):", value=float(current_row['buy_price']), step=1.0, key="edit_buy")
            new_target = col2.number_input("Фактична ціна продажу (брутто ₴):", value=float(current_row['target_price']), step=1.0, key="edit_sell")
            
            if st.button("💾 Оновити ціни в Портфелі"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE portfolio SET buy_price = %s, target_price = %s WHERE id = %s",
                          (int(new_buy), int(new_target), int(selected_id)))
                conn.commit(); conn.close()
                st.success("Ціни успішно оновлено!")
                time.sleep(1); st.rerun()
                
            st.markdown("---")
            btn_col1, btn_col2 = st.columns(2)
            
            with btn_col1:
                if st.button("📊 ПРОДАНО! Відправити у Звіт", type="primary", use_container_width=True):
                    clean_income = get_clean_income(new_target)
                    final_profit = clean_income - new_buy
                    save_to_reports(item_name, "Продаж з портфеля", int(new_buy), int(final_profit))
                    
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM portfolio WHERE id = %s", (int(selected_id),))
                    conn.commit(); conn.close()
                    
                    st.success(f"✅ Успішно продано! Твій чистий прибуток: {final_profit} ₴")
                    time.sleep(1.5); st.rerun()
                    
            with btn_col2:
                if st.button("❌ Просто видалити (Відміна)", use_container_width=True):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM portfolio WHERE id = %s", (int(selected_id),))
                    conn.commit(); conn.close()
                    st.warning("Лот видалено з портфеля.")
                    time.sleep(1); st.rerun()

# ==========================================
# СТОРІНКА 4: ЗВІТИ
# ==========================================
elif menu_choice == "📊 Звіти (База)":
    st.header("📊 Хмарна фінансова звітність")
    
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM trades ORDER BY id DESC")
    df = pd.DataFrame(c.fetchall())
    conn.close()
    
    if df.empty:
        st.info("База поки що порожня. Зафіксуй перший профіт!")
    else:
        total_profit = df['profit'].sum()
        total_invested = df['cost'].sum()
        roi = (total_profit / total_invested * 100) if total_invested > 0 else 0
        
        col1, col2, col3 = st.columns(3)
        col1.metric("💰 Загальний профіт", f"{total_profit} ₴")
        col2.metric("💸 Вкладено коштів", f"{total_invested} ₴")
        col3.metric("📈 Загальний ROI", f"{roi:.1f}%")
        
        st.divider()
        df_display = df.rename(columns={
            "id": "ID", "date": "Дата", "set_name": "Назва", 
            "strategy": "Стратегія", "cost": "Витрати (₴)", "profit": "Профіт (₴)"
        })
        st.dataframe(df_display, hide_index=True, use_container_width=True)
        
        st.divider()
        with st.expander("⚙️ Налаштування бази"):
            del_trade_id = st.number_input("Видалити звіт (введи ID):", step=1, min_value=0)
            if st.button("Видалити запис"):
                if del_trade_id > 0:
                    conn = get_db_connection()
                    conn.cursor().execute("DELETE FROM trades WHERE id = %s", (int(del_trade_id),))
                    conn.commit(); conn.close()
                    st.success("Видалено!"); time.sleep(1); st.rerun()
