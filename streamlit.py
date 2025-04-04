import streamlit as st
import pandas as pd
import numpy as np
import os
import plotly.express as px
from datetime import datetime
import pytz
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players

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

# Load Data
final_df = pd.read_csv("final.csv").dropna(how="all")  # Full data for all weeks
standings = pd.read_csv("standings.csv").dropna(how="all")
df_matchups = pd.read_csv("df_matchups.csv").dropna(how="all")
team_logos = pd.read_csv("team_logos.csv").dropna(how="all")

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

# --------------- ⛹🏽 MULTI-PLAYER COMPARISON ---------------
elif selection == "⛹🏽 Multi-player comparison":
    st.title("⛹🏽 Multi-player comparison")

    def get_active_players():
        all_players = players.get_players()
        active_players = {p["full_name"]: p["id"] for p in all_players if p["is_active"]}
        return active_players

    active_players = get_active_players()

    def get_player_headshot(player_id):
        return f"https://cdn.nba.com/headshots/nba/latest/1040x760/{player_id}.png"

    def get_player_stats(player_id, player_name, period, mode):
        from nba_api.stats.endpoints import playergamelog
        gamelog = playergamelog.PlayerGameLog(player_id=player_id, season="2024-25", season_type_all_star="Regular Season")
        df = gamelog.get_data_frames()[0]

        if df.empty:
            return None

        df["GAME_DATE"] = pd.to_datetime(df["GAME_DATE"], format="%b %d, %Y", errors='coerce')

        if period != "season":
            time_cutoff = pd.Timestamp.today() - pd.Timedelta(days=period)
            df = df[df["GAME_DATE"] >= time_cutoff]

        relevant_stats = ["MIN", "FGM", "FGA", "FG_PCT", "FG3M",
                          "FG3A", "FG3_PCT", "FTM", "FTA", "FT_PCT",
                          "OREB", "DREB", "REB", "AST", "STL",
                          "BLK", "TOV", "PF", "PTS", "PLUS_MINUS"]

        df = df[relevant_stats]

        if mode == "Average":
            stats = df.mean()
            stats["FG_PCT"] = round(df.loc[df["FGA"] > 0, "FG_PCT"].mean(), 3) if (df["FGA"] > 0).any() else 0
            stats["FG3_PCT"] = round(df.loc[df["FG3A"] > 0, "FG3_PCT"].mean(), 3) if (df["FG3A"] > 0).any() else 0
            stats["FT_PCT"] = round(df.loc[df["FTA"] > 0, "FT_PCT"].mean(), 3) if (df["FTA"] > 0).any() else 0
            for col in stats.index:
                if col not in ["FG_PCT", "FG3_PCT", "FT_PCT"]:
                    stats[col] = round(stats[col], 1)
        else:
            stats = df.sum()
            stats = stats.drop(["FG_PCT", "FG3_PCT", "FT_PCT"])
            stats = stats.round(1)

        stats["Player"] = player_name
        stats["Headshot"] = get_player_headshot(player_id)
        return stats

    def compare_players(player_list, period, mode):
        all_stats = []
        for player in player_list:
            player_id = active_players.get(player)
            if player_id:
                stats = get_player_stats(player_id, player, period, mode)
                if stats is not None:
                    all_stats.append(stats)
        return pd.DataFrame(all_stats) if all_stats else None

    num_players = st.selectbox("Select Number of Players to Compare", options=[2, 3, 4, 5], index=0)

    selected_players = []
    for i in range(num_players):
        player = st.selectbox(f"Select Player {i+1}", list(active_players.keys()), key=f"player_{i}")
        selected_players.append(player)

    period_options = {
        "Last 7 Days (Average)": (7, "Average"),
        "Last 7 Days (Total)": (7, "Total"),
        "Last 14 Days (Average)": (14, "Average"),
        "Last 14 Days (Total)": (14, "Total"),
        "Last 30 Days (Average)": (30, "Average"),
        "Last 30 Days (Total)": (30, "Total"),
        "Full Season (Average)": ("season", "Average"),
        "Full Season (Total)": ("season", "Total")
    }
    selected_period_label = st.selectbox("Select Time Period", list(period_options.keys()), index=0)
    period_value, mode = period_options[selected_period_label]

    if st.button("Compare Players"):
        with st.spinner("Fetching player stats..."):
            combined_df = compare_players(selected_players, period_value, mode)
            if combined_df is not None:
                st.success("Comparison Generated!")
                ordered_columns = ["Headshot", "Player"] + [col for col in combined_df.columns if col not in ["Headshot", "Player"]]
                combined_df = combined_df[ordered_columns]

                def image_formatter(url):
                    return f'<img src="{url}" width="75">'

                pct_columns = {"FG_PCT": "FG%", "FG3_PCT": "3PT FG%", "FT_PCT": "FT%"}
                combined_df.rename(columns=pct_columns, inplace=True)

                st.markdown(
                    combined_df.to_html(
                        escape=False,
                        formatters={"Headshot": image_formatter},
                        index=False
                    ),
                    unsafe_allow_html=True
                )
            else:
                st.error("No game data available for the selected players and period.")

# --------------- 🗣️ Free Agency ---------------
elif selection == "🗣️ Free Agency":
    st.title("🗣️ Free Agency")

    fig = px.bar(
        final_df[final_df['Week'] == final_df['Week'].max()].sort_values("Adjusted Rank"),
        x="Name", y="Adjusted Rank",
        title="Adjusted Team Rankings",
        text="Adjusted Rank", color="Adjusted Rank",
        color_continuous_scale="Viridis"
    )
    st.plotly_chart(fig, use_container_width=True)

# Footer
st.markdown("""
    <style>
        .footer {position: fixed; bottom: 0; width: 100%; text-align: center; padding: 10px;}
    </style>
    <div class='footer'>© 2025 Ryan Sandan</div>
""", unsafe_allow_html=True)
