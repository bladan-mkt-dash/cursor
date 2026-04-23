import streamlit as st
import plotly.express as px
from google_data import get_ga4_data

# Set the page to be wide for better chart viewing
st.set_page_config(page_title="Marketing Dashboard", layout="wide")

st.title("Google Analytics 4 Performance")

# Fetch the data using the function we built in the other file
with st.spinner("Fetching live GA4 Data from Google..."):
    df_ga4 = get_ga4_data()

# Calculate totals for top-line metrics
total_sessions = df_ga4['GA4_Sessions'].sum()
total_users = df_ga4['GA4_Total_Users'].sum()

# Display summary metrics at the top of the dashboard
col1, col2, col3 = st.columns(3)
col1.metric("Total Sessions (30 Days)", f"{total_sessions:,}")
col2.metric("Total Users (30 Days)", f"{total_users:,}")

st.markdown("---")

# Create an interactive Plotly chart
st.subheader("Website Traffic Trends")

# Plotly Express makes it easy to plot multiple lines by passing a list to the 'y' axis
fig = px.line(
    df_ga4,
    x="Date",
    y=["GA4_Sessions", "GA4_Total_Users"],
    labels={"value": "Traffic Count", "variable": "Metrics"},
    color_discrete_sequence=["#1f77b4", "#ff7f0e"] # Blue for sessions, Orange for users
)

# Render the interactive chart in Streamlit
st.plotly_chart(fig, width="stretch")

# Add an expandable section to view the raw numbers if needed
with st.expander("View Raw Data Table"):
    st.dataframe(df_ga4, width="stretch")
    