import streamlit as st
import pandas as pd
import datetime
from dateutil.relativedelta import relativedelta
import uuid
from streamlit_calendar import calendar
from collections import Counter
from pathlib import Path
import pytz

# --- Page Config ---
st.set_page_config(page_title="献血カレンダー", page_icon="💉", layout="wide")

# --- Session State Initialization ---
def init_session_state():
    if "history" not in st.session_state:
        st.session_state.history = []
    if "calendar_view_date" not in st.session_state:
        st.session_state.calendar_view_date = datetime.date.today().strftime("%Y-%m-%d")

init_session_state()

# --- Constants & Data Definitions ---
ALL_TYPES = ["400ml全血", "200ml全血", "成分献血"]
MAX_VOLUME = {"男性": 1200, "女性": 800}
today = datetime.date.today()
LOCATIONS_CSV_PATH = Path("locations.csv")

REGIONS = {
    "北海道": ["北海道"],
    "東北": ["青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県"],
    "関東": ["茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県"],
    "中部": ["新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県", "静岡県", "愛知県"],
    "近畿": ["三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県"],
    "中国": ["鳥取県", "島根県", "岡山県", "広島県", "山口県"],
    "四国": ["徳島県", "香川県", "愛媛県", "高知県"],
    "九州・沖縄": ["福岡県", "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県"]
}

# --- Data Loading ---
@st.cache_data
def load_locations():
    if not LOCATIONS_CSV_PATH.exists():
        # Attempt to use a relative path for Streamlit Cloud
        alt_path = Path(__file__).parent / "locations.csv"
        if not alt_path.exists():
            st.error(f"locations.csv が見つかりません。")
            return pd.DataFrame()
        csv_path = alt_path
    else:
        csv_path = LOCATIONS_CSV_PATH

    try:
        df = pd.read_csv(csv_path)
        for col in ["name", "latitude", "longitude", "prefecture"]:
            if col not in df.columns:
                st.error(f"locations.csvに必須の列 '{col}' がありません。")
                return pd.DataFrame()
        df.fillna({"prefecture": "不明"}, inplace=True)
        return df
    except Exception as e:
        st.error(f"locations.csvの読み込みに失敗しました: {e}")
        return pd.DataFrame()

# --- Sidebar ---
st.sidebar.title("献血記録手帳")
app_mode = st.sidebar.selectbox("表示モードを選択", ["カレンダー", "献血マップ"])
st.sidebar.markdown("---")
st.sidebar.header("ユーザー情報")
birthday = st.sidebar.date_input("🎂 生年月日", min_value=datetime.date(1950, 1, 1), max_value=today - relativedelta(years=16), value=datetime.date(2000, 1, 1))
gender = st.sidebar.radio("🚻 性別", ["男性", "女性"])
st.sidebar.markdown("---")

# --- Business Logic ---
def get_volume(donation_type):
    if donation_type == "200ml全血": return 200
    if donation_type == "400ml全血": return 400
    return 0

def check_availability(target_date, history, gender, birthday):
    results = {}
    age_on_date = relativedelta(target_date, birthday).years
    sorted_history = sorted(history, key=lambda x: x['start'])
    donations_before_target = [h for h in sorted_history if datetime.datetime.strptime(h['start'], "%Y-%m-%d").date() < target_date]
    last_donation = donations_before_target[-1] if donations_before_target else None

    for don_type in ALL_TYPES:
        is_age_ok = False
        if don_type == "200ml全血" and 16 <= age_on_date <= 69: is_age_ok = True
        if don_type == "400ml全血" and ((gender == "男性" and 17 <= age_on_date <= 69) or (gender == "女性" and 18 <= age_on_date <= 69)): is_age_ok = True
        if don_type == "成分献血" and 18 <= age_on_date <= 69: is_age_ok = True
        if not is_age_ok:
            results[don_type] = {"available": False, "reason": "年齢制限"}
            continue

        if last_donation:
            last_date = datetime.datetime.strptime(last_donation['start'], "%Y-%m-%d").date()
            next_available = last_date
            if "全血" in last_donation['title']:
                if don_type == "成分献血": next_available += relativedelta(weeks=8)
                elif last_donation['title'] == "400ml全血": next_available += relativedelta(weeks=12 if gender == "男性" else 16)
                elif last_donation['title'] == "200ml全血": next_available += relativedelta(weeks=4)
            elif "成分" in last_donation['title']:
                next_available += relativedelta(weeks=2)
            if target_date < next_available:
                results[don_type] = {"available": False, "reason": "献血間隔", "next": next_available.strftime("%Y-%m-%d")}
                continue

        if "全血" in don_type:
            window_start = target_date - relativedelta(years=1)
            relevant_history = [h for h in sorted_history if window_start < datetime.datetime.strptime(h['start'], "%Y-%m-%d").date() < target_date and "全血" in h['title']]
            volume_in_year = sum(get_volume(h['title']) for h in relevant_history)
            
            if volume_in_year + get_volume(don_type) > MAX_VOLUME[gender]:
                donations_in_window = [h for h in sorted_history if (target_date - relativedelta(years=1)) <= datetime.datetime.strptime(h['start'], "%Y-%m-%d").date() < target_date and "全血" in h['title']]
                if donations_in_window:
                    first_donation_in_window = min(donations_in_window, key=lambda x: x['start'])
                    block_lift_date = datetime.datetime.strptime(first_donation_in_window['start'], "%Y-%m-%d").date() + relativedelta(years=1)
                    results[don_type] = {"available": False, "reason": "年間総採血量上限", "next": block_lift_date.strftime("%Y-%m-%d")}
                    continue
        
        results[don_type] = {"available": True}
    return results

# --- UI Rendering ---
def render_calendar_view():
    st.title("💉 献血カレンダー")
    locations_df = load_locations()
    room_names = locations_df["name"].tolist() if not locations_df.empty else []
    MANUAL_INPUT_OPTION = "その他（手動入力）"

    def show_form(target_date, availability):
        st.sidebar.markdown("### 記録フォーム")
        st.sidebar.write(f"**{target_date.strftime('%Y-%m-%d')}** の記録")
        available_types = [t for t, r in availability.items() if r["available"]]
        
        if not available_types:
            st.sidebar.warning("この日時に追加できる献血種別はありません。")
            for don_type, result in sorted(availability.items()):
                if not result["available"]:
                    reason = result["reason"]
                    next_date_info = f" (次回可能: {result['next']})" if "next" in result else ""
                    st.sidebar.error(f"**{don_type}:** {reason}{next_date_info}")
            return

        with st.sidebar.form("donation_form", clear_on_submit=True):
            donation_type = st.selectbox("種別", available_types)
            location_choice = st.selectbox("場所を選択", [MANUAL_INPUT_OPTION] + room_names)
            final_location = st.text_input("場所を手入力（献血バスなど）") if location_choice == MANUAL_INPUT_OPTION else location_choice
            notes = st.text_area("備考")
            
            if st.form_submit_button("保存"):
                if not final_location:
                    st.sidebar.error("場所を入力してください。")
                    return
                
                color = "#4CAF50" if "成分" in donation_type else "#FF4C4C"
                new_record = {"id": str(uuid.uuid4()), "title": donation_type, "start": target_date.strftime("%Y-%m-%d"), "location": final_location, "notes": notes, "color": color}
                st.session_state.history.append(new_record)
                st.rerun()

    def show_edit_form(record):
        st.sidebar.markdown("### 記録の編集")
        current_location = record.get("location", "")
        all_locations = [MANUAL_INPUT_OPTION] + room_names
        if current_location and current_location not in room_names:
            all_locations.insert(1, current_location)
        try:
            default_index = all_locations.index(current_location)
        except ValueError:
            default_index = 0

        with st.sidebar.form("edit_form"):
            st.write(f"**{record['start']}** の記録")
            donation_type = st.selectbox("種別", ALL_TYPES, index=ALL_TYPES.index(record['title']))
            location_choice = st.selectbox("場所を選択", all_locations, index=default_index)
            final_location = st.text_input("場所を手入力", value=current_location if location_choice == MANUAL_INPUT_OPTION and current_location not in room_names else "") if location_choice == MANUAL_INPUT_OPTION else location_choice
            notes = st.text_area("備考", value=record.get("notes", ""))
            
            c1, c2 = st.columns(2)
            if c1.form_submit_button("更新"):
                if not final_location:
                    st.sidebar.error("場所を入力してください。")
                    return
                
                new_color = "#4CAF50" if "成分" in donation_type else "#FF4C4C"
                st.session_state.history = [r for r in st.session_state.history if r["id"] != record["id"]]
                record.update({"title": donation_type, "location": final_location, "notes": notes, "color": new_color})
                st.session_state.history.append(record)
                st.rerun()
            if c2.form_submit_button("削除", type="primary"):
                st.session_state.history = [r for r in st.session_state.history if r["id"] != record["id"]]
                st.rerun()

    state = calendar(events=st.session_state.history, options={
        "initialDate": st.session_state.calendar_view_date,
        "timeZone": "local", "headerToolbar": {"left": "prev,next today", "center": "title", "right": "dayGridMonth,listYear"},
        "initialView": "dayGridMonth", "selectable": True
    }, custom_css=".fc-event-past { opacity: 0.8; } .fc-event-title { font-weight: 700; }", key=str(st.session_state.history))

    if state.get("datesSet"): st.session_state.calendar_view_date = state["datesSet"]["start"]
    if state.get("dateClick"): 
        # Final Fix for Timezone: Convert UTC date from calendar to JST explicitly.
        dt_obj_utc = datetime.datetime.fromisoformat(state["dateClick"]["date"].replace('Z', '+00:00'))
        jst = pytz.timezone('Asia/Tokyo')
        dt_obj_jst = dt_obj_utc.astimezone(jst)
        actual_date = dt_obj_jst.date()

        st.session_state.calendar_view_date = actual_date.strftime("%Y-%m-01")
        availability = check_availability(actual_date, st.session_state.history, gender, birthday)
        show_form(actual_date, availability)
    if state.get("eventClick"): 
        event = next((e for e in st.session_state.history if e["id"] == state["eventClick"]["event"]["id"]), None)
        if event: show_edit_form(event)

def render_map_view():
    st.title("🗺️ 献血マップ")
    locations_df = load_locations()
    if locations_df.empty:
        return

    visited_locations_counts = Counter(r["location"] for r in st.session_state.history if r.get("location"))
    
    locations_df["visited"] = locations_df["name"].apply(lambda name: name in visited_locations_counts)
    locations_df["donation_count"] = locations_df["name"].apply(lambda name: visited_locations_counts.get(name, 0))
    
    VISITED_COLOR = "#4CAF50" # Green
    UNVISITED_COLOR = "#FF4C4C" # Red
    locations_df["color"] = locations_df["visited"].apply(lambda v: VISITED_COLOR if v else UNVISITED_COLOR)

    st.map(locations_df, latitude="latitude", longitude="longitude", color="color")

    st.markdown("### 全国制覇状況")
    prefecture_stats = locations_df.groupby("prefecture")["visited"].agg(['sum', 'count']).rename(columns={'sum': 'visited', 'count': 'total'})

    def create_progress_bar(progress, color):
        return f"""
        <div style="background-color: #ddd; border-radius: 5px; height: 24px; width: 100%;">
            <div style="background-color: {color}; width: {progress * 100}%; border-radius: 5px; height: 100%;">
            </div>
        </div>
        """

    for region, prefectures_in_region in REGIONS.items():
        with st.expander(f"📍 {region}"):
            for pref in prefectures_in_region:
                if pref in prefecture_stats.index:
                    stats = prefecture_stats.loc[pref]
                    total = int(stats['total'])
                    visited = int(stats['visited'])
                    progress = visited / total if total > 0 else 0

                    if progress == 0:
                        bar_color = UNVISITED_COLOR
                    elif progress == 1:
                        bar_color = VISITED_COLOR
                    else:
                        bar_color = "#FFC107"  # Yellow

                    st.markdown(f"#### {pref}")
                    st.markdown(create_progress_bar(progress, bar_color), unsafe_allow_html=True)
                    st.caption(f"{visited} / {total}")

                    pref_df = locations_df[locations_df["prefecture"] == pref]
                    for _, room in pref_df.iterrows():
                        count = room["donation_count"]
                        if room["visited"]:
                            st.markdown(f"- ✅ **{room['name']}** ({count}回)")
                        else:
                            st.markdown(f"- ❌ {room['name']}")

# --- Main App Router ---
if app_mode == "カレンダー":
    render_calendar_view()
elif app_mode == "献血マップ":
    render_map_view()
elif app_mode == "献血マップ":
    render_map_view()
