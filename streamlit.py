import streamlit as st
import pandas as pd
import numpy as np
import os
import plotly.express as px
from datetime import datetime
import pytz
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players

from logic import (
    authenticate_yahoo_api,
    get_full_season_stats,
    get_standings,
    get_matchups_df,
    get_team_logos,
    team_ids  # if you need the mapping
)

# Page Configuration
st.set_page_config(
    page_title="Season 2 of Love Island (NBA)",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Sidebar Navigation

# Get the absolute path of the logo
logo_path = os.path.join(os.path.dirname(__file__), "ryanlogo.png")
st.logo(
    logo_path,
    link="https://rsandan.github.io",
    size="large"
)
st.sidebar.title("🌐 Navigation")
pages = ["🏠 Home", "⛹🏽 Multi-player comparison", "🗣️ Free Agency"]
selection = st.sidebar.radio("Go to", pages)

# Get Current EST Time
est = pytz.timezone('US/Eastern')
current_time = datetime.now(est).strftime("%B %d, %Y - %I:%M %p")

# Streamlit App
st.title("Season 2 of Love Island (NBA)")

# Authenticate and get league object
lg = authenticate_yahoo_api()
print(lg)
curr_week = lg.current_week()
print(curr_week)

# Fetch dataframes
season_df = get_full_season_stats(lg, curr_week)
standings_df = get_standings(lg)
matchups_df = get_matchups_df(lg, curr_week)
logos_df = get_team_logos(lg, team_ids)


# Display Week Summary
st.subheader(f"📈 Weekly Stats (Up to Week {curr_week})")
st.dataframe(season_df)

# Display Standings
st.subheader("🏆 Current League Standings")
st.dataframe(standings_df)

# Display Current Matchups
st.subheader(f"🤜🤛 Week {curr_week} Matchups & Winners")
st.dataframe(matchups_df)

# Display Team Logos (Optional - can use in visuals)
st.subheader("🏷️ Team Logos")
st.dataframe(logos_df)


# Footer
st.markdown("""
    <style>
        .footer {position: fixed; bottom: 0; width: 100%; text-align: center; padding: 10px;}
    </style>
    <div class='footer'>© 2025 Ryan Sandan</div>
""", unsafe_allow_html=True)
