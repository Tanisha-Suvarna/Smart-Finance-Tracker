import streamlit as st
st.set_page_config(page_title="MMT Pro", page_icon="💰", layout="wide")
import pandas as pd
import plotly.express as px
import gspread
import os
import re
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def get_optional_secret(key: str, default: str = "") -> str:
    """Safely read Streamlit secret without requiring secrets.toml."""
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def suggest_mood_from_text(item_name: str) -> str:
    """Simple text-based classifier for smart mood preselection."""
    text = (item_name or "").lower().strip()
    if not text:
        return "🔋 Necessary"

    necessary_words = [
        "rent", "bill", "medicine", "grocery", "milk", "fuel", "petrol", "diesel",
        "school", "fees", "electricity", "internet", "insurance", "emi", "loan"
    ]
    impulse_words = [
        "sale", "offer", "deal", "swiggy", "zomato", "amazon", "shopping", "snack",
        "coffee", "movie", "game", "subscription", "fashion", "gift"
    ]
    stress_words = [
        "late fee", "penalty", "hospital", "repair", "urgent", "emergency", "fine"
    ]

    if any(word in text for word in stress_words):
        return "😫 Stressed"
    if any(word in text for word in impulse_words):
        return "🚀 Impulse"
    if any(word in text for word in necessary_words):
        return "🔋 Necessary"
    return "✨ Happy"


def get_monthly_forecast(df: pd.DataFrame) -> float:
    """Forecast end-of-month spend using pace so far."""
    if df.empty or "amount" not in df.columns:
        return 0.0
    current_spend = pd.to_numeric(df["amount"], errors="coerce").sum()
    today = datetime.now()
    day_of_month = max(today.day, 1)
    days_in_month = pd.Period(today.strftime("%Y-%m")).days_in_month
    projected = (current_spend / day_of_month) * days_in_month
    return float(projected)


def detect_unusual_spends(df: pd.DataFrame) -> pd.DataFrame:
    """Detect potentially unusual expenses using z-score style rule."""
    if df.empty or "amount" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["amount"] = pd.to_numeric(work["amount"], errors="coerce")
    work = work.dropna(subset=["amount"])
    if work.empty:
        return pd.DataFrame()
    mean_amt = work["amount"].mean()
    std_amt = work["amount"].std()
    if pd.isna(std_amt) or std_amt == 0:
        return pd.DataFrame()
    work["zscore"] = (work["amount"] - mean_amt) / std_amt
    outliers = work[work["zscore"] >= 1.5].sort_values("amount", ascending=False)
    return outliers.head(5)


def local_ai_money_coach(df: pd.DataFrame, monthly_limit: float, goal_savings: float) -> str:
    """Fallback AI-style insights when API key is unavailable."""
    if df.empty:
        return "No spending data yet. Start logging entries to unlock personalized AI insights."

    total = pd.to_numeric(df["amount"], errors="coerce").sum()
    projected = get_monthly_forecast(df)
    essential_ratio = 0.0
    if "mood" in df.columns and total > 0:
        essential_ratio = (
            pd.to_numeric(df[df["mood"] == "🔋 Necessary"]["amount"], errors="coerce").sum() / total
        ) * 100
    safe_now = monthly_limit - total - goal_savings
    risk_line = "on track"
    if projected > monthly_limit:
        risk_line = "at risk of crossing budget"
    if projected > monthly_limit * 1.15:
        risk_line = "likely to significantly overshoot budget"

    tips = [
        f"You have spent ₹{total:,.0f} so far and are {risk_line}.",
        f"Projected end-of-month spend is about ₹{projected:,.0f} vs budget ₹{monthly_limit:,.0f}.",
        f"Current safe-to-spend after goals is ₹{safe_now:,.0f}.",
        f"Necessary-spend share is {essential_ratio:.1f}%; keep impulse spending under 20% for stability.",
        "Action: set a weekly cap and pause non-essential purchases for 3 days after any large spend."
    ]
    return "\n".join([f"- {t}" for t in tips])


def openai_money_coach(df: pd.DataFrame, monthly_limit: float, goal_savings: float) -> str:
    """Generate AI insights from OpenAI if API key is present."""
    api_key = get_optional_secret("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    if not api_key or OpenAI is None:
        return local_ai_money_coach(df, monthly_limit, goal_savings)

    sample_rows = df.tail(40).to_dict("records") if not df.empty else []
    total = pd.to_numeric(df["amount"], errors="coerce").sum() if not df.empty else 0
    projected = get_monthly_forecast(df)
    prompt = f"""
You are a personal finance AI coach.
Give concise, practical recommendations in 5 bullet points.
Data:
- Monthly budget: {monthly_limit}
- Goal savings per month: {goal_savings}
- Current spend: {total}
- Projected month-end spend: {projected}
- Recent logs: {sample_rows}
Focus on risk, savings opportunities, and one behavior change.
"""
    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a smart and concise finance coach."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return local_ai_money_coach(df, monthly_limit, goal_savings)


def parse_natural_expense_rule_based(text: str) -> dict:
    """Extract item, amount, mood, and date from natural language input."""
    raw = (text or "").strip()
    low = raw.lower()
    result = {
        "item": "",
        "amount": 0.0,
        "mood": "🔋 Necessary",
        "date": pd.Timestamp.now().strftime("%d-%b-%Y"),
        "confidence": "medium",
    }

    # amount: supports "450", "₹450", "1,200.50"
    amount_match = re.search(r"(?:₹\s*)?(\d[\d,]*(?:\.\d{1,2})?)", raw)
    if amount_match:
        amount_num = amount_match.group(1).replace(",", "")
        try:
            result["amount"] = float(amount_num)
        except Exception:
            result["amount"] = 0.0

    # date intent
    if "yesterday" in low:
        result["date"] = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime("%d-%b-%Y")
    elif "today" in low:
        result["date"] = pd.Timestamp.now().strftime("%d-%b-%Y")

    # mood hint
    if any(k in low for k in ["necessary", "need", "essential"]):
        result["mood"] = "🔋 Necessary"
    elif any(k in low for k in ["stress", "urgent", "emergency", "penalty", "fine"]):
        result["mood"] = "😫 Stressed"
    elif any(k in low for k in ["impulse", "shopping", "craving", "treat", "snack"]):
        result["mood"] = "🚀 Impulse"
    elif any(k in low for k in ["happy", "fun", "celebration"]):
        result["mood"] = "✨ Happy"

    # item extraction from common patterns
    patterns = [
        r"(?:spent|pay|paid)\s+(?:₹\s*)?\d[\d,]*(?:\.\d{1,2})?\s+(?:on|for)\s+(.+?)(?:,|$)",
        r"(?:on|for)\s+(.+?)(?:,|$)",
    ]
    item = ""
    for pat in patterns:
        m = re.search(pat, low)
        if m:
            item = m.group(1).strip()
            break
    if not item:
        cleaned = re.sub(r"(?:₹\s*)?\d[\d,]*(?:\.\d{1,2})?", "", low).strip(" ,.-")
        item = cleaned if cleaned else "General Expense"
    item = re.sub(r"\b(today|yesterday|necessary|impulse|stressed|happy|urgent|emergency)\b", "", item).strip(" ,.-")
    result["item"] = item.title() if item else "General Expense"

    # If no explicit mood hint, infer from item text
    if result["mood"] == "🔋 Necessary" and "necessary" not in low:
        result["mood"] = suggest_mood_from_text(result["item"])

    if result["amount"] <= 0 or result["item"] == "General Expense":
        result["confidence"] = "low"
    return result


def parse_natural_expense(text: str) -> dict:
    """Try OpenAI parse first (if configured), else use rule-based parser."""
    parsed = parse_natural_expense_rule_based(text)
    api_key = get_optional_secret("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    if not api_key or OpenAI is None:
        return parsed

    try:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[
                {"role": "system", "content": "Extract finance log fields as strict CSV: item,amount,mood,date,confidence. Moods allowed: ✨ Happy | 🔋 Necessary | 😫 Stressed | 🚀 Impulse. Date format: DD-MMM-YYYY. Confidence: low/medium/high."},
                {"role": "user", "content": f"Text: {text}"},
            ],
        )
        line = (resp.choices[0].message.content or "").strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            item, amount_str, mood, date_str, confidence = parts[:5]
            amount = float(amount_str) if amount_str.replace(".", "", 1).isdigit() else parsed["amount"]
            if mood not in ["✨ Happy", "🔋 Necessary", "😫 Stressed", "🚀 Impulse"]:
                mood = parsed["mood"]
            # validate date
            try:
                pd.to_datetime(date_str, format="%d-%b-%Y")
            except Exception:
                date_str = parsed["date"]
            if confidence not in ["low", "medium", "high"]:
                confidence = "medium"
            return {
                "item": item or parsed["item"],
                "amount": amount,
                "mood": mood,
                "date": date_str,
                "confidence": confidence,
            }
    except Exception:
        pass
    return parsed

# --- 1. CONNECTION ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

if os.path.exists("creds.json"):
    creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
elif "gcp_service_account" in st.secrets:
    creds_info = st.secrets["gcp_service_account"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
else:
    st.error("Credential file 'creds.json' not found!")
    st.stop()

client = gspread.authorize(creds)

# --- 2. OPEN THE SHEETS ---
try:
    spreadsheet = client.open("Finances")
    expense_sheet = spreadsheet.sheet1          
    goal_sheet = spreadsheet.worksheet("Goals") 
    settings_sheet = spreadsheet.worksheet("Settings") 
except Exception as e:
    st.error(f"Sheet Setup Error: {e}")
    st.stop()

# --- 3. DATA FETCH FUNCTIONS ---
@st.cache_data(ttl=1)
def get_expense_data():
    data = expense_sheet.get_all_records()
    df = pd.DataFrame(data)
    if not df.empty: df.columns = [c.lower().strip() for c in df.columns]
    return df

@st.cache_data(ttl=1)
def get_goal_data():
    data = goal_sheet.get_all_records()
    df = pd.DataFrame(data)
    if not df.empty: df.columns = [c.lower().strip() for c in df.columns]
    return df

def get_monthly_limit():
    try:
        val = settings_sheet.acell('A2').value
        return float(val) if val else 25000.0
    except:
        return 25000.0

# --- 4. DATA INITIALIZATION ---
df_expenses = get_expense_data()
df_goals = get_goal_data()
monthly_limit = get_monthly_limit()
total_goal_savings = 0.0
goal_rows = []
if not df_goals.empty:
    for _, row in df_goals.iterrows():
        monthly = float(row["total"]) / float(row["months"]) if row["months"] else 0.0
        total_goal_savings += monthly
        goal_rows.append({"name": row["name"], "monthly": monthly})

# --- 5. UI THEME ---
theme_choice = st.sidebar.selectbox("🎨 Theme", ["Finance Dark", "Finance Light"], key="theme_choice")
if theme_choice == "Finance Dark":
    bg_gradient = "linear-gradient(160deg, #081c15 0%, #1b4332 45%, #2d6a4f 100%)"
    text_color = "#f1f5f4"
    soft_text = "#cfe7dc"
    tab_text = "#e9f4ef"
    side_bg = "rgba(8, 28, 21, 0.65)"
    card_bg = "rgba(255,255,255,0.08)"
    border = "rgba(255,255,255,0.16)"
    chart_font = "#eaf6ef"
else:
    bg_gradient = "linear-gradient(160deg, #f8fffb 0%, #e6f4ec 50%, #d8f0e3 100%)"
    text_color = "#163126"
    soft_text = "#315949"
    tab_text = "#1f3b2f"
    side_bg = "rgba(223, 241, 231, 0.9)"
    card_bg = "rgba(255,255,255,0.72)"
    border = "rgba(31, 59, 47, 0.18)"
    chart_font = "#244636"

st.markdown(f"""
<style>
    .stApp {{
        background: {bg_gradient};
        color: {text_color};
    }}
    .block-container {{
        padding-top: 3.2rem;
        padding-bottom: 2rem;
        max-width: 1180px;
    }}
    .app-title {{
        font-size: 2rem;
        font-weight: 700;
        margin-top: 0.2rem;
        margin-bottom: 0.3rem;
        line-height: 1.25;
        letter-spacing: 0.2px;
    }}
    .app-sub {{
        color: {soft_text};
        margin-bottom: 1rem;
    }}
    .hero-badges {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 16px;
    }}
    .hero-badge {{
        background: {card_bg};
        border: 1px solid {border};
        color: {text_color};
        padding: 6px 10px;
        border-radius: 999px;
        font-size: 0.83rem;
        font-weight: 600;
    }}
    .metric-card {{
        background: {card_bg};
        border: 1px solid {border};
        border-radius: 16px;
        padding: 14px 16px;
        min-height: 112px;
        box-shadow: 0 6px 20px rgba(0,0,0,0.2);
    }}
    .metric-label {{
        font-size: 0.9rem;
        color: {soft_text};
        margin: 0;
    }}
    .metric-value {{
        font-size: 1.5rem;
        font-weight: 700;
        margin: 4px 0 0 0;
    }}
    .stTabs [data-baseweb="tab-list"] {{
        gap: 10px;
    }}
    .stTabs [data-baseweb="tab"] {{
        background: {card_bg};
        border-radius: 10px;
        color: {tab_text};
        border: 1px solid {border};
        padding: 8px 16px;
    }}
    .stTabs [aria-selected="true"] {{
        background: rgba(116, 198, 157, 0.3) !important;
        border-color: rgba(116, 198, 157, 0.7) !important;
    }}
    [data-testid="stSidebar"] {{
        background: {side_bg};
        border-right: 1px solid {border};
    }}
</style>
""", unsafe_allow_html=True)

# --- 6. SIDEBAR ---
with st.sidebar:
    st.title("💼 Finance Control")
    st.caption("Quick actions and smart entry")

    with st.container(border=True):
        st.subheader("⚙️ Budget")
        new_limit = st.number_input("Monthly Limit (₹)", value=monthly_limit, step=500.0, key="main_budget_input")
        if st.button("💾 Save Budget", use_container_width=True):
            settings_sheet.update('A2', [[new_limit]])
            st.cache_data.clear()
            st.rerun()

    with st.container(border=True):
        st.subheader("🎯 Goals")
        with st.expander("➕ Add New Goal", expanded=False):
            g_name = st.text_input("Goal Name")
            g_total = st.number_input("Total (₹)", min_value=0.0, key="new_goal_amt")
            g_months = st.number_input("Months", min_value=1, value=12, key="new_goal_months")
            if st.button("Save Goal", use_container_width=True):
                if g_name and g_total > 0:
                    goal_sheet.append_row([g_name, g_total, g_months])
                    st.cache_data.clear()
                    st.rerun()
        if goal_rows:
            for idx, row in enumerate(goal_rows):
                cols = st.columns([3, 1])
                cols[0].caption(f"{row['name']} · ₹{row['monthly']:,.0f}/mo")
                if cols[1].button("🗑️", key=f"g_{idx}", help="Delete goal"):
                    goal_sheet.delete_rows(idx + 2)
                    st.cache_data.clear()
                    st.rerun()
        else:
            st.caption("No goals yet.")

    with st.container(border=True):
        st.subheader("🧠 AI Smart Entry")
        natural_text = st.text_area(
            "Describe expense",
            placeholder="Spent 450 on groceries today, necessary",
            key="natural_expense_text",
            height=90,
        )
        if st.button("✨ Parse with AI", key="parse_ai_expense_btn", use_container_width=True):
            if natural_text.strip():
                st.session_state["parsed_expense"] = parse_natural_expense(natural_text)
            else:
                st.warning("Type a sentence first.")

        parsed_expense = st.session_state.get("parsed_expense")
        if parsed_expense:
            st.caption(f"Confidence: **{parsed_expense['confidence']}**")
            p_item = st.text_input("Parsed Item", value=parsed_expense["item"], key="parsed_item_input")
            p_amount = st.number_input("Parsed Amount (₹)", min_value=0.0, value=float(parsed_expense["amount"]), key="parsed_amount_input")
            p_mood = st.selectbox(
                "Parsed Mood",
                ["✨ Happy", "🔋 Necessary", "😫 Stressed", "🚀 Impulse"],
                index=["✨ Happy", "🔋 Necessary", "😫 Stressed", "🚀 Impulse"].index(parsed_expense["mood"]),
                key="parsed_mood_input",
            )
            p_date = st.text_input("Parsed Date (DD-MMM-YYYY)", value=parsed_expense["date"], key="parsed_date_input")
            if st.button("✅ Add Parsed Entry", key="add_parsed_entry_btn", use_container_width=True):
                if p_item and p_amount > 0:
                    expense_sheet.append_row([p_date, p_item, p_amount, p_mood])
                    st.success("AI parsed entry added.")
                    st.session_state["parsed_expense"] = None
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Parsed item/amount is invalid.")

    with st.container(border=True):
        st.subheader("💸 Quick Log")
        with st.form("log_form", clear_on_submit=True):
            item = st.text_input("Item")
            amt = st.number_input("Cost (₹)", min_value=0.0, key="log_cost_input")
            smart_mood = suggest_mood_from_text(item)
            moods = ["✨ Happy", "🔋 Necessary", "😫 Stressed", "🚀 Impulse"]
            mood = st.selectbox("Mood", moods, index=moods.index(smart_mood))
            st.caption(f"AI suggestion: **{smart_mood}**")
            if st.form_submit_button("🚀 Save Entry", use_container_width=True):
                if item and amt > 0:
                    expense_sheet.append_row([pd.Timestamp.now().strftime("%d-%b-%Y"), item, amt, mood])
                    st.cache_data.clear()
                    st.rerun()

    with st.container(border=True):
        st.subheader("🛠️ Recent Logs")
        if not df_expenses.empty:
            for i, r in df_expenses.tail(3).iloc[::-1].iterrows():
                c1, c2 = st.columns([3, 1])
                c1.caption(f"{r['item']} · ₹{r['amount']}")
                if c2.button("🗑️", key=f"l_{i}", help="Delete log"):
                    expense_sheet.delete_rows(i + 2)
                    st.cache_data.clear()
                    st.rerun()
        else:
            st.caption("No logs yet.")
# --- 7. DASHBOARD ---
st.markdown("<div class='app-title'>💰 Smart Finance Tracker</div>", unsafe_allow_html=True)
st.markdown("<div class='app-sub'>Track spending, plan goals, and use AI to stay financially confident.</div>", unsafe_allow_html=True)
st.markdown(
    """
<div class="hero-badges">
  <span class="hero-badge">🤖 AI Budget Copilot</span>
  <span class="hero-badge">🧠 Natural Language Expense Parsing</span>
  <span class="hero-badge">📈 Month-End Spend Forecasting</span>
  <span class="hero-badge">🚨 Unusual Spend Detection</span>
</div>
""",
    unsafe_allow_html=True,
)

total_spent = pd.to_numeric(df_expenses['amount'], errors='coerce').sum() if not df_expenses.empty else 0
safe_to_spend = monthly_limit - total_spent - total_goal_savings

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"<div class='metric-card'><p class='metric-label'>Total Spent</p><p class='metric-value'>₹{total_spent:,.0f}</p></div>", unsafe_allow_html=True)
with c2:
    st.markdown(f"<div class='metric-card'><p class='metric-label'>Goal Savings / Month</p><p class='metric-value'>₹{total_goal_savings:,.0f}</p></div>", unsafe_allow_html=True)
with c3:
    st.markdown(f"<div class='metric-card'><p class='metric-label'>Safe to Spend</p><p class='metric-value'>₹{safe_to_spend:,.0f}</p></div>", unsafe_allow_html=True)
with c4:
    st.markdown(f"<div class='metric-card'><p class='metric-label'>Budget Balance</p><p class='metric-value'>₹{(monthly_limit - total_spent):,.0f}</p></div>", unsafe_allow_html=True)

usage_pct = min(total_spent / monthly_limit, 1.0) if monthly_limit > 0 else 0
st.progress(usage_pct, text=f"Budget used: {usage_pct*100:.1f}%")

tab_overview, tab_analytics, tab_ai = st.tabs(["📊 Overview", "📈 Analytics", "🤖 AI Copilot"])

with tab_overview:
    col_table, col_pie = st.columns([1.7, 1])
    with col_table:
        st.subheader("Spending History")
        if not df_expenses.empty:
            st.dataframe(df_expenses.iloc[::-1], use_container_width=True, height=360)
        else:
            st.info("No expenses logged yet.")
        st.button("🔄 Sync with Cloud", on_click=st.cache_data.clear, key="sync_btn")
    with col_pie:
        st.subheader("Mood Split")
        if not df_expenses.empty and total_spent > 0:
            fig = px.pie(
                df_expenses,
                values='amount',
                names='mood',
                hole=0.65,
                color_discrete_sequence=["#95d5b2", "#74c69d", "#52b788", "#40916c"],
            )
            fig.update_layout(
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                font_color=chart_font,
                showlegend=True,
                margin=dict(t=10, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Mood chart will appear after logs.")

with tab_analytics:
    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("Goal Plan")
        if goal_rows:
            goal_df = pd.DataFrame(goal_rows)
            st.dataframe(goal_df.rename(columns={"name": "Goal", "monthly": "Monthly Saving"}), use_container_width=True, height=300)
        else:
            st.info("Add goals from the sidebar to see plan breakdown.")
    with col_b:
        st.subheader("Budget Snapshot")
        snapshot = pd.DataFrame(
            [
                {"Metric": "Monthly Budget", "Value": monthly_limit},
                {"Metric": "Spent So Far", "Value": total_spent},
                {"Metric": "Goal Savings", "Value": total_goal_savings},
                {"Metric": "Safe To Spend", "Value": safe_to_spend},
            ]
        )
        fig_bar = px.bar(
            snapshot,
            x="Metric",
            y="Value",
            color="Metric",
            color_discrete_sequence=["#95d5b2", "#74c69d", "#52b788", "#40916c"],
        )
        fig_bar.update_layout(
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color=chart_font,
            showlegend=False,
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig_bar, use_container_width=True)

with tab_ai:
    st.subheader("AI Finance Copilot")
    forecast_spend = get_monthly_forecast(df_expenses)
    unusual_df = detect_unusual_spends(df_expenses)
    a1, a2, a3 = st.columns(3)
    with a1:
        st.metric("Month-end Forecast", f"₹{forecast_spend:,.0f}")
    with a2:
        st.metric("Forecast vs Budget", f"₹{(monthly_limit - forecast_spend):,.0f}")
    with a3:
        st.metric("Unusual Spend Alerts", f"{len(unusual_df)}")

    if len(unusual_df) > 0:
        st.warning("Potentially unusual expenses detected.")
        show_cols = [c for c in ["date", "item", "amount", "mood"] if c in unusual_df.columns]
        st.dataframe(unusual_df[show_cols], use_container_width=True)
    else:
        st.success("No unusual spending spikes detected right now.")

    if st.button("🧠 Generate AI Budget Advice", key="ai_budget_btn"):
        with st.spinner("Analyzing your spending pattern..."):
            advice = openai_money_coach(df_expenses, monthly_limit, total_goal_savings)
        st.markdown(advice)
