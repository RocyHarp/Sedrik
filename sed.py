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
import os

# --- НАЛАШТУВАННЯ СТОРІНКИ ---
st.set_page_config(
    page_title="Sedrik Dota Tool",
    page_icon="💎",
    layout="wide",
    initial_sidebar_state="expanded"
)

STEAM_APP_ID_DOTA = 570
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
if 'lib_result' not in st.session_state:
    st.session_state.lib_result = None
if 'scan_count' not in st.session_state:
    st.session_state.scan_count = 0  # Наш лічильник для пасхалки!

# --- БАЗА ДАНИХ (NEON) ---
def get_db_connection():
    return psycopg2.connect(st.secrets["DB_URL"])

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY, date TEXT, set_name TEXT, 
            strategy TEXT, cost INTEGER, profit INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            id SERIAL PRIMARY KEY, date TEXT, item_name TEXT, 
            buy_price INTEGER, target_price INTEGER
        )
    ''')
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
    conn.commit(); conn.close()

def add_to_portfolio(item_name, buy_price, target_price):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d")
    c.execute('INSERT INTO portfolio (date, item_name, buy_price, target_price) VALUES (%s, %s, %s, %s)',
              (now, item_name, buy_price, target_price))
    conn.commit(); conn.close()

def add_to_library(set_name, hero, rarity, image_url, components):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM library WHERE set_name = %s", (set_name,))
    if not c.fetchone():
        c.execute('''
            INSERT INTO library (set_name, hero, rarity, image_url, components) 
            VALUES (%s, %s, %s, %s, %s)
        ''', (set_name, hero, rarity, image_url, json.dumps(components)))
    conn.commit(); conn.close()

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

def get_steam_client_url(item_name: str) -> str:
    return f"steam://openurl/https://steamcommunity.com/market/listings/{STEAM_APP_ID_DOTA}/{urllib.parse.quote(item_name)}"

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

@st.cache_data(ttl=300, show_spinner=False)
def get_steam_price_data(item_name: str) -> dict:
    url = f"https://steamcommunity.com/market/priceoverview/?appid={STEAM_APP_ID_DOTA}&currency={CURRENCY_UAH}&market_hash_name={urllib.parse.quote(item_name)}"
    result = {"price": 0, "volume": 0}
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if data.get("success"):
                if "lowest_price" in data:
                    result["price"] = int(float(data["lowest_price"].replace("₴", "").replace(" ", "").replace(",", ".")))
                if "volume" in data:
                    result["volume"] = int(data["volume"].replace(",", "")) * 7
        time.sleep(1.3)
    except: pass
    return result

# --- ФУНКЦІЯ МАЛЮВАННЯ ТОРГОВОГО ДАШБОРДУ ---
def render_trading_logic(res, prefix_key="dash"):
    st.markdown("### 🧮 Розрахунок прибутку")
    tab1, tab2 = st.tabs(["🎁 СТРАТЕГІЯ 1: ПАКУВАННЯ", "✂️ СТРАТЕГІЯ 2: РОЗПАКУВАННЯ"])
    safe_key = f"{prefix_key}_{res['exact_name']}"
    
    with tab1:
        col_in1, col_in2 = st.columns(2)
        user_buy_price = col_in1.number_input("За скільки КУПИВ всі частини (сума ₴):", value=float(res['total_parts_price']), step=1.0, key=f"buy_pack_{safe_key}")
        user_sell_price = col_in2.number_input("За скільки ПРОДАШ цілий бандл (брутто ₴):", value=float(res['bundle_data']['price']), step=1.0, key=f"sell_pack_{safe_key}")
        
        actual_bundle_income = get_clean_income(user_sell_price)
        actual_pack_profit = actual_bundle_income - user_buy_price
        
        st.info(f"Твій чистий дохід після комісії Steam: **{actual_bundle_income} ₴**")
        
        if actual_pack_profit > 0: st.metric("Чистий Профіт", f"{actual_pack_profit} ₴", delta="Вигідно")
        else: st.metric("Чистий Профіт", f"{actual_pack_profit} ₴", delta="Збиток", delta_color="inverse")

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("📊 Додати у ЗВІТ (Вже продано)", type="primary", use_container_width=True, key=f"rep_pack_{safe_key}"):
                if user_buy_price > 0:
                    save_to_reports(res['exact_name'], "Пакування", int(user_buy_price), int(actual_pack_profit))
                    st.success("✅ Записано в Звіти!")
                else: st.warning("Введи ціну закупки!")
        with btn_col2:
            if st.button("💼 Додати у ПОРТФЕЛЬ (Ще лежить)", use_container_width=True, key=f"port_pack_{safe_key}"):
                if user_buy_price > 0:
                    add_to_portfolio(res['exact_name'] + " (Збірка)", int(user_buy_price), int(user_sell_price))
                    st.success("✅ Записано в Портфель!")
                else: st.warning("Введи ціну закупки!")

    with tab2:
        col_in1, col_in2 = st.columns(2)
        user_bundle_buy = col_in1.number_input("За скільки КУПИВ цілий бандл (₴):", value=float(res['bundle_data']['price']), step=1.0, key=f"buy_unpack_{safe_key}")
        user_parts_sell = col_in2.number_input("За скільки ПРОДАШ всі деталі (брутто ₴):", value=float(res['total_parts_price']), step=1.0, key=f"sell_unpack_{safe_key}")
        
        actual_parts_income = get_clean_income(user_parts_sell)
        actual_unpack_profit = actual_parts_income - user_bundle_buy
        
        st.info(f"Твій чистий дохід після комісій Steam: **{actual_parts_income} ₴**")
        
        if actual_unpack_profit > 0: st.metric("Чистий Профіт", f"{actual_unpack_profit} ₴", delta="Вигідно")
        else: st.metric("Чистий Профіт", f"{actual_unpack_profit} ₴", delta="Збиток", delta_color="inverse")

        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button("📊 Додати у ЗВІТ (Вже продано)", type="primary", use_container_width=True, key=f"rep_unpack_{safe_key}"):
                if user_bundle_buy > 0:
                    save_to_reports(res['exact_name'], "Розпакування", int(user_bundle_buy), int(actual_unpack_profit))
                    st.success("✅ Записано в Звіти!")
                else: st.warning("Введи ціну закупки!")
        with btn_col2:
            if st.button("💼 Додати у ПОРТФЕЛЬ (Ще лежить)", use_container_width=True, key=f"port_unpack_{safe_key}"):
                if user_bundle_buy > 0:
                    add_to_portfolio(res['exact_name'] + " (Розпаковка)", int(user_bundle_buy), int(user_parts_sell))
                    st.success("✅ Записано в Портфель!")
                else: st.warning("Введи ціну закупки!")

    st.divider()
    html_table = "<style>.st-table { width: 100%; border-collapse: collapse; } .st-table th, .st-table td { padding: 10px; border-bottom: 1px solid #2e303e; } .st-table a { color: #66c0f4; text-decoration: none; font-weight: bold; }</style><table class='st-table'><tr><th>Деталь</th><th>Ціна (₴)</th><th>Тобі (₴)</th><th>Продажі (тижд)</th><th>Дія</th></tr>"
    for p in res['parts_data']: html_table += f"<tr><td>{p['Деталь']}</td><td>{p['Ціна']}</td><td style='color:#a3e635;'>{p['Чистими']}</td><td>~{p['Продажі']}</td><td><a href='{p['Link']}'>Купити 🛒</a></td></tr>"
    st.markdown(html_table + "</table>", unsafe_allow_html=True)


# ==========================================
# БОКОВЕ МЕНЮ
# ==========================================
with st.sidebar:
    st.title("🛠 Sedrik Dota Tool")
    st.markdown("`v13.1 | Easter Egg Edition`")
    st.divider()
    menu_choice = st.radio("НАВІГАЦІЯ:", ["🔍 Сканер Сетів", "📚 Бібліотека", "💼 Портфель", "📊 Звіти (База)"])
    st.divider()

# ==========================================
# СТОРІНКА 1: СКАНЕР СЕТІВ
# ==========================================
if menu_choice == "🔍 Сканер Сетів":
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
            st.error(f"Не вдалося знайти деталі для '{exact_name}'. Перевір правильність назви.")
        else:
            # ПАСХАЛКА: Збільшуємо лічильник успішних сканів!
            st.session_state.scan_count += 1
            
            with st.sidebar:
                bundle_data = get_steam_price_data(exact_name)
                parts_data = []
                total_parts_price = 0
                total_parts_clean_income = 0
                
                for i, item in enumerate(items):
                    status_text.text(f"Сканування ({i+1}/{len(items)}): {item}")
                    data = get_steam_price_data(item)
                    clean_part = get_clean_income(data['price'])
                    
                    parts_data.append({
                        "Деталь": item, "Ціна": data['price'], "Чистими": clean_part,
                        "Продажі": data['volume'], "Link": get_steam_client_url(item)
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
        res = st.session_state.dota_result
        
        with st.container(border=True):
            col_img, col_info = st.columns([1, 3])
            
            # --- ЛОГІКА ВІДОБРАЖЕННЯ ФОТО (І ПАСХАЛКИ) ---
            with col_img:
                # Перевіряємо чи це кожен 10-й скан
                if st.session_state.scan_count > 0 and st.session_state.scan_count % 2 == 0:
                    if os.path.exists("easter_egg.jpg"):
                        st.image("easter_egg.jpg", use_container_width=True)
                        st.caption("✨ Опа, пасхалочка! Гарного дня!")
                    else:
                        st.info("📷 Пасхалка не знайдена (закинь файл easter_egg.jpg на GitHub)")
                # Якщо ні - показуємо звичайне фото сету
                elif res['set_info']["image_url"]:
                    try:
                        img_res = requests.get(res['set_info']["image_url"], headers=HEADERS, timeout=5)
                        if img_res.status_code == 200: st.image(img_res.content, use_container_width=True)
                        else: st.info("📷 Помилка фото")
                    except: st.info("📷 Помилка фото")
                else: st.info("📷 Фото не знайдено")
            # ---------------------------------------------
                    
            with col_info:
                st.subheader(f"📦 {res['exact_name']}")
                st.markdown(f"**Герой:** `{res['set_info']['hero']}` &nbsp;|&nbsp; **Рідкість:** `{res['set_info']['rarity']}`")
                
                col_link, col_lib = st.columns(2)
                with col_link:
                    st.markdown(f"<a href='{get_steam_client_url(res['exact_name'])}' style='display: inline-block; padding: 6px 15px; background-color: #1a2838; color: #66c0f4; text-decoration: none; border-radius: 4px; border: 1px solid #101822; width: 100%; text-align: center; font-weight: bold;'>Відкрити бандл у Steam 🔗</a>", unsafe_allow_html=True)
                with col_lib:
                    components_to_save = [{"name": p['Деталь'], "last_price": p['Ціна']} for p in res['parts_data']]
                    if st.button("📚 Зберегти в Бібліотеку", key="btn_lib_scan", use_container_width=True):
                        add_to_library(res['exact_name'], res['set_info']['hero'], res['set_info']['rarity'], res['set_info']['image_url'], components_to_save)
                        st.success("✅ Збережено назавжди!")

        st.write("")
        render_trading_logic(res, prefix_key="scan")

# ==========================================
# СТОРІНКА 2: БІБЛІОТЕКА СЕТІВ
# ==========================================
elif menu_choice == "📚 Бібліотека":
    st.header("📚 Бібліотека Сетів")
    st.markdown("Твоя особиста база для швидкого трейдингу. Вибирай сет зі списку нижче.")
    
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM library ORDER BY set_name ASC")
    lib_sets = c.fetchall()
    conn.close()
    
    if not lib_sets:
        st.info("💡 Твоя Бібліотека поки що порожня. Зайди у 'Сканер Сетів' і збережи цікаві сети сюди.")
    else:
        set_names = [s['set_name'] for s in lib_sets]
        selected_set_name = st.selectbox("📌 Вибери сет з колекції:", set_names)
        
        selected_set = next(s for s in lib_sets if s['set_name'] == selected_set_name)
        components_raw = json.loads(selected_set['components'])
        components_names = [comp['name'] if isinstance(comp, dict) else comp for comp in components_raw]
        
        old_total_value = sum([comp.get('last_price', 0) for comp in components_raw if isinstance(comp, dict)])
        
        st.write("")
        
        with st.container(border=True):
            col_img, col_details = st.columns([1, 2.5])
            
            with col_img:
                if selected_set['image_url']:
                    try:
                        img_res = requests.get(selected_set['image_url'], headers=HEADERS, timeout=5)
                        if img_res.status_code == 200: st.image(img_res.content, use_container_width=True)
                        else: st.info("📷 Помилка фото")
                    except: st.info("📷 Помилка фото")
            
            with col_details:
                head_col1, head_col2 = st.columns([4, 1])
                with head_col1:
                    st.subheader(selected_set['set_name'])
                with head_col2:
                    if st.button("🗑 Видалити", key=f"del_{selected_set['id']}", use_container_width=True):
                        conn = get_db_connection()
                        conn.cursor().execute("DELETE FROM library WHERE id = %s", (selected_set['id'],))
                        conn.commit(); conn.close()
                        if st.session_state.lib_result and st.session_state.lib_result['exact_name'] == selected_set_name:
                            st.session_state.lib_result = None
                        st.rerun()

                st.markdown(f"**Герой:** `{selected_set['hero']}` &nbsp;|&nbsp; **Рідкість:** `{selected_set['rarity']}`")
                if old_total_value > 0:
                    st.markdown(f"💸 **Остання відома сумарна вартість деталей:** `{old_total_value} ₴`")
                
                with st.expander(f"📦 Склад сету ({len(components_names)} шт.) - Старі ціни"):
                    old_table = "<table style='width:100%; border-collapse: collapse;'><tr><th style='border-bottom: 1px solid #444; padding: 4px; text-align: left;'>Деталь</th><th style='border-bottom: 1px solid #444; padding: 4px; text-align: right;'>Остання ціна</th></tr>"
                    for comp in components_raw:
                        if isinstance(comp, str):
                            old_table += f"<tr><td style='padding: 4px;'>{comp}</td><td style='padding: 4px; text-align: right; color: #888;'>Невідомо</td></tr>"
                        else:
                            old_table += f"<tr><td style='padding: 4px;'>{comp['name']}</td><td style='padding: 4px; text-align: right; color: #ccc;'>{comp.get('last_price', 0)} ₴</td></tr>"
                    old_table += "</table>"
                    st.markdown(old_table, unsafe_allow_html=True)

        st.write("")
        
        has_cached_data = (st.session_state.lib_result and st.session_state.lib_result['exact_name'] == selected_set_name)
        update_btn = st.button("🔄 Отримати свіжі ціни маркету", type="primary", use_container_width=True)
        
        if has_cached_data:
            last_upd = st.session_state.lib_result.get('last_updated', datetime.now())
            mins_passed = int((datetime.now() - last_upd).total_seconds() / 60)
            if mins_passed >= 30:
                st.warning(f"⚠️ Дані сканувались {mins_passed} хв тому. Рекомендую оновити.")
            else:
                time_str = "Щойно" if mins_passed == 0 else f"{mins_passed} хв тому"
                st.caption(f"✅ Ціни актуальні (Оновлено: {time_str})")

        if update_btn:
            with st.spinner("Зв'язуюсь зі Steam... Це займе кілька секунд."):
                bundle_data = get_steam_price_data(selected_set_name)
                parts_data = []
                total_parts_price = 0
                total_parts_clean_income = 0
                updated_components_for_db = []
                
                my_bar = st.progress(0)
                for i, item_name in enumerate(components_names):
                    data = get_steam_price_data(item_name)
                    clean_part = get_clean_income(data['price'])
                    
                    parts_data.append({
                        "Деталь": item_name, "Ціна": data['price'], "Чистими": clean_part,
                        "Продажі": data['volume'], "Link": get_steam_client_url(item_name)
                    })
                    total_parts_price += data['price']
                    total_parts_clean_income += clean_part
                    
                    updated_components_for_db.append({"name": item_name, "last_price": data['price']})
                    my_bar.progress((i + 1) / len(components_names))
                my_bar.empty()
                
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("UPDATE library SET components = %s WHERE id = %s", (json.dumps(updated_components_for_db), selected_set['id']))
                conn.commit(); conn.close()
                
                st.session_state.lib_result = {
                    "exact_name": selected_set_name, 
                    "bundle_data": bundle_data,
                    "parts_data": parts_data, 
                    "total_parts_price": total_parts_price,
                    "total_parts_clean_income": total_parts_clean_income,
                    "last_updated": datetime.now()
                }
                st.rerun()

        if has_cached_data:
            render_trading_logic(st.session_state.lib_result, prefix_key="lib_dash")

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
