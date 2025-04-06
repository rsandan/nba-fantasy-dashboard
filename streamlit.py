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
curr_week = lg.current_week()

# Load data / Fetch dataframes
final_df = get_full_season_stats(lg)
standings = get_standings(lg)
df_matchups = get_matchups_df(lg)
team_logos = get_team_logos(lg, team_ids)

# Format column names for standings and matchups
standings.columns = standings.columns.str.replace("_", " ").str.title()
standings["Record"] = standings["Wins"].astype(str) + "-" + standings["Losses"].astype(str) + "-" + standings["Ties"].astype(str)
standings = standings.drop(columns=["Playoff Seed", "Games Back", "Wins", "Losses", "Ties", "Percentage"])
df_matchups.columns = df_matchups.columns.str.replace("_", " ").str.title()

# Define columns to exclude from formatting
exclude_columns = ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "STL", "BLK", "TO"]

# Custom formatting for final_df columns: do not change rank columns.
def format_col(col):
    if col.endswith("_Rank"):
        return col
    elif col in exclude_columns:
        return col
    else:
        return col.replace("_", " ").title()

final_df.columns = [format_col(col) for col in final_df.columns]


# --------------- 🏠 HOME PAGE ---------------
if selection == "🏠 Home":
    # Update Stats Button - Reloads Streamlit Script
    if st.button("🔄 Update Stats"):
        st.rerun()  # Reloads the script to refresh data
    # Convert logos into a list (for grid placement)
    logo_urls = team_logos["Logo URL"].tolist()
    
    # Create a row of images using markdown (CSS for tight spacing)
    logo_html = "<div style='display: flex; justify-content: flex-start; gap: 10px;'>"
    for url in logo_urls:
        logo_html += f"<img src='{url}' width='30' style='border-radius: 10px;'>"
    logo_html += "</div>"

    # Render the HTML in Streamlit
    st.markdown(logo_html, unsafe_allow_html=True)

    st.markdown(f"### Week 18🏀")
    st.markdown(f"""
        <p style="font-size: 14px; font-style: italic; color: white; opacity: 0.7; margin-top: -10px;">
            Score snapshot as of {current_time} EST
        </p>
    """, unsafe_allow_html=True)

    # Layout: Two equal columns for Leaderboard and Matchups
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Leaderboard")
        st.dataframe(standings, use_container_width=True, hide_index=True)
    with col2:
        st.subheader("Current Matchups")
        st.dataframe(df_matchups, use_container_width=True, hide_index=True)

     # Place the week filter on the main page (above the statistics table)
    week_options = sorted(final_df['Week'].unique())
    selected_week = st.selectbox("Choose Week Number to filter table below:", week_options, index=len(week_options)-1)

    # Filter the data for the selected week and sort by Adjusted_Rank
    week_data = final_df[final_df['Week'] == selected_week].sort_values(by=['Adjusted_Rank'], ascending=True)

    # List of stat categories to format
    stat_categories = ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO']

    # Combine raw values and their rank for each stat category
    for category in stat_categories:
        week_data[category] = (
            week_data[category].round(3).astype(str) +
            " (" +
            week_data[category + "_Rank"].astype(str) +
            ")"
        )

    # Combine remaining/live/completed columns
    week_data["Rem/Live/Comp"] = week_data["Remaining Games"].astype(str) + "/" + week_data["Live Games"].astype(str) + "/" + week_data["Completed Games"].astype(str)
    
    # Combine Aggregate Rank and Adjusted Rank into a single column
    week_data['Adjusted Rank'] = week_data['Adjusted_Rank'].astype(str) + " (" + week_data['Aggregate Rank'].astype(str) + ")"

   
    # Remove unwanted columns
    columns_to_remove = ["Team Key", "Team Id", "Fgm/A", "Ftm/A", "Remaining Games", "Live Games", "Completed Games", "Aggregate Rank"]
    
    # Also remove any column whose name contains "_Rank" except "Adjusted_Rank"
    rank_columns = [col for col in week_data.columns if "_Rank" in col]
    columns_to_remove.extend(rank_columns)

    
    week_data = week_data.drop(columns=columns_to_remove)
    
    # Assuming week_data is your DataFrame
    desired_order = ['Week', 'Name', 'Rem/Live/Comp', 'Adjusted Rank', 'FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO']
    week_data = week_data[desired_order]

    # Full-Width Traditional Statistics Table
    st.subheader("Traditional Statistics")
    st.markdown(f"""
        <p style="font-size: 14px; color: white; opacity: 0.7; margin-top: -10px;">
            <b>Adjusted Rank</b> – Summarizes all individual rankings into one total score. The lower the number, the better.
        </p>
    """, unsafe_allow_html=True)
    st.dataframe(week_data, use_container_width=True, hide_index=True)

# Footer
st.markdown("""
    <style>
        .footer {position: fixed; bottom: 0; width: 100%; text-align: center; padding: 10px;}
    </style>
    <div class='footer'>© 2025 Ryan Sandan</div>
""", unsafe_allow_html=True)
