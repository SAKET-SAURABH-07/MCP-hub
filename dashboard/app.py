"""Streamlit dashboard over the audit log.  streamlit run dashboard/app.py"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from mcp_hub.metrics import compute_metrics

st.set_page_config(page_title="mcp-hub audit", layout="wide")
st.title("mcp-hub audit dashboard")

db_path = st.sidebar.text_input("Audit DB", os.environ.get("MCPHUB_AUDIT_DB", "data/audit.db"))
if not Path(db_path).exists():
    st.warning(f"No database at {db_path}. Run the server or scripts/seed_demo.py first.")
    st.stop()

conn = sqlite3.connect(db_path)
m = compute_metrics(conn)
df = pd.read_sql_query("SELECT * FROM audit_log ORDER BY ts DESC", conn)
if df.empty:
    st.info("Audit log is empty.")
    st.stop()
df["time"] = pd.to_datetime(df["ts"], unit="s")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total calls", m["total_calls"])
c2.metric("Unique users", m["unique_users"])
c3.metric("Denied", f"{m['denied']} ({m['denied_rate']:.1%})")
c4.metric("OK latency p95", f"{m['ok_latency_p95_ms']} ms")

left, right = st.columns(2)
with left:
    st.subheader("Calls by tool")
    st.bar_chart(df.groupby("tool").size())
with right:
    st.subheader("Status")
    st.bar_chart(df.groupby("status").size())

st.subheader("Calls over time (hourly)")
st.line_chart(df.set_index("time").resample("1h").size())

st.subheader("Per-tool latency")
st.dataframe(pd.DataFrame(m["tools"]).T)

st.subheader("Denied / failed calls")
st.dataframe(
    df[df["status"] != "ok"][
        ["time", "username", "roles", "tool", "status", "error_code", "detail"]
    ].head(200)
)

st.subheader("Recent calls (arguments are redacted before storage)")
st.dataframe(
    df[["time", "username", "roles", "tool", "status", "latency_ms", "args_json"]].head(100)
)
