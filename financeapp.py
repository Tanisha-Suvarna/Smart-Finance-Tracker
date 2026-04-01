import streamlit as st
import pandas as pd
import plotly.express as px
import gspread
import random
import os
from oauth2client.service_account import ServiceAccountCredentials

# --- 1. CONNECTION (Hybrid Fix) ---
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
except Exception as e:
    st.error(f"Sheet Setup Error: {e}")
    st.stop()

# --- 3. DATA FETCH ---
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

# --- 4. UI CONFIG & GLASSMORPHISM ---
if 'bg' not in st.session_state:
    st.session_state.bg = random.choice(["linear-gradient(135deg, #667eea 0%, #764ba2 100%)", "linear-gradient(135deg, #6a11cb 0%, #2575fc 100%)"])

st.set_page_config(page_title="MMT Pro", layout="wide")
st.markdown(f"""
<style>
    .stApp {{ background: {st.session_state.bg}; color: white; }}
    .metric-card {{ 
        background: rgba(255,255,255,0.1); 
        backdrop-filter: blur(10px); 
        padding: 20px; 
        border-radius: 20px; 
        border: 1px solid rgba(255,255,255,0.2); 
        text-align: center; 
        transition: 0.4s; 
    }}
    .metric-card:hover {{ transform: translateY(-8px); }}
</style>
""", unsafe_allow_html=True)

df_expenses = get_expense_data()
df_goals = get_goal_data()

# --- 5. SIDEBAR (GOALS & LOGS) ---
with st.sidebar:
    st.header("🎯 Permanent Goals")
    with st.expander("➕ Add New Goal"):
        g_name = st.text_input("Goal Name")
        g_total = st.number_input("Total (₹)", min_value=0.0)
        g_months = st.number_input("Months", min_value=1, value=12)
        if st.button("Save Goal"):
            if g_name and g_total > 0:
                goal_sheet.append_row([g_name, g_total, g_months])
                st.cache_data.clear()
                st.rerun()

    total_goal_savings = 0
    if not df_goals.empty:
        for idx, row in df_goals.iterrows():
            monthly = row['total'] / row['months']
            total_goal_savings += monthly
            ca, cb = st.columns([4, 1])
            ca.write(f"✅ **{row['name']}** (₹{monthly:,.0f})")
            if cb.button("🗑️", key=f"g_{idx}"): # RESTORED GOAL DELETE
                goal_sheet.delete_rows(idx + 2)
                st.cache_data.clear()
                st.rerun()

    st.markdown("---")
    st.header("💸 Quick Log")
    with st.form("log_form", clear_on_submit=True):
        item = st.text_input("Item")
        amt = st.number_input("Cost (₹)", min_value=0.0)
        mood = st.selectbox("Mood", ["✨ Happy", "🔋 Necessary", "😫 Stressed", "🚀 Impulse"])
        if st.form_submit_button("Secure Entry"):
            if item and amt > 0:
                expense_sheet.append_row([pd.Timestamp.now().strftime("%d-%b-%Y"), item, amt, mood])
                st.cache_data.clear()
                st.balloons()
                st.rerun()

    st.markdown("---")
    st.header("🛠️ Log History")
    if not df_expenses.empty:
        for i, r in df_expenses.tail(3).iloc[::-1].iterrows():
            ca, cb = st.columns([4, 1])
            ca.write(f"🛒 {r['item']} (₹{r['amount']})")
            if cb.button("🗑️", key=f"l_{i}"): # RESTORED LOG DELETE
                expense_sheet.delete_rows(i + 2)
                st.cache_data.clear()
                st.rerun()

# --- 6. DASHBOARD (RESTORED 4 SQUARES) ---
st.title("💰 My Money Tracker")
total_spent = pd.to_numeric(df_expenses['amount'], errors='coerce').sum() if not df_expenses.empty else 0
monthly_limit = 25000.0
safe_to_spend = monthly_limit - total_spent - total_goal_savings

c1, c2, c3, c4 = st.columns(4)
with c1: st.markdown(f"<div class='metric-card'><h3>Spent</h3><h2>₹{total_spent:,.0f}</h2></div>", unsafe_allow_html=True)
with c2: st.markdown(f"<div class='metric-card'><h3>Goal Savings</h3><h2>₹{total_goal_savings:,.0f}</h2></div>", unsafe_allow_html=True)
with c3: st.markdown(f"<div class='metric-card'><h3>Safe to Spend</h3><h2>₹{safe_to_spend:,.0f}</h2></div>", unsafe_allow_html=True)
with c4: st.markdown(f"<div class='metric-card'><h3>Balance</h3><h2>₹{(monthly_limit - total_spent):,.0f}</h2></div>", unsafe_allow_html=True)

# --- 7. PROGRESS BAR & CHARTS ---
st.write("##")
usage_pct = min(total_spent / monthly_limit, 1.0) if monthly_limit > 0 else 0
st.progress(usage_pct)
st.write(f"Budget used: **{usage_pct*100:.1f}%**")

st.write("---")
col_table, col_pie = st.columns([1.5, 1])
with col_table:
    st.subheader("🕵️ Spending History")
    st.dataframe(df_expenses.iloc[::-1], use_container_width=True)

with col_pie:
    st.subheader("🎯 Mood Insights")
    if not df_expenses.empty and total_spent > 0:
        fig = px.pie(df_expenses, values='amount', names='mood', hole=0.7, 
                     color_discrete_sequence=px.colors.qualitative.Pastel)
        fig.update_layout(paper_bgcolor='rgba(0,0,0,0)', font_color="white", showlegend=False, margin=dict(t=0, b=0, l=0, r=0))
        st.plotly_chart(fig, use_container_width=True)
