# import libraries
import os
import subprocess
import json
import pandas as pd
import numpy as np
from plotly.subplots import make_subplots
import matplotlib.pyplot as plt
import random
import plotly.graph_objects as go
import datetime
import seaborn as sns
from collections import Counter

# import api stuff
from yahoo_oauth import OAuth2
import yahoo_fantasy_api as yfa
from pyngrok import ngrok, conf

# Set the maximum number of columns to display to None
pd.set_option('display.max_columns', None)

# Load secrets from Render environment variables
keypair_data = os.getenv("KEYPAIR_JSON")
if keypair_data:
    keypair = json.loads(keypair_data)
else:
    raise ValueError("KEYPAIR_JSON environment variable is missing!")

NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN")
if not NGROK_AUTH_TOKEN:
    raise ValueError("NGROK_AUTH_TOKEN environment variable is missing!")

# Use OAuth2 authentication without interactive input
try:
    sc = OAuth2(None, None, from_dict=keypair)
    if not sc.token_is_valid():  # If token expired, refresh automatically
        sc.refresh_access_token()
except Exception as e:
    raise RuntimeError(f"OAuth authentication failed: {str(e)}")

subprocess.run(["ngrok", "authtoken", NGROK_AUTH_TOKEN], check=True)



team_ids = {'454.l.74601.t.1': "Sam's Swag Team",
            '454.l.74601.t.2': "Dooms's Dazzling Team",
            '454.l.74601.t.3': "Diddy Disciple",
            '454.l.74601.t.4': "Han Da Dons",
            '454.l.74601.t.5': "Tyshiii",
            '454.l.74601.t.6': "Neal's Fascinating Team",
            '454.l.74601.t.7': "Driton's Dazzling Team",
            '454.l.74601.t.8': "ariel's Wonderful Team",
            '454.l.74601.t.9': "THE REAL MIAMI HEAT",
            '454.l.74601.t.10': "darius"}

# Define stat labels
stat_labels = {
    '9004003': 'FGM/A',
    '5': 'FG%',
    '9007006': 'FTM/A',
    '8': 'FT%',
    '10': '3PTM',
    '12': 'PTS',
    '15': 'REB',
    '16': 'AST',
    '17': 'STL',
    '18': 'BLK',
    '19': 'TO'
}

# get game object
gm = yfa.Game(sc, 'nba')

# get league ids (could be multiple if you're in more than 1)
leagues = gm.league_ids()

# get the league object
lg = gm.to_league(leagues[0])

# get your team key
teamkey = lg.team_key()

# get your team object
team = lg.to_team(teamkey)

# return current week
curr_week_num = lg.current_week()

# grabs everyone's stats and data regarding week n using the matchups function
def overall_weekly_matchup_stats(week_num):
    matchups = lg.matchups(week=week_num)
    matchup_keys = list(matchups['fantasy_content']['league'][1]['scoreboard']['0']['matchups'].keys())

    temp = []

    for key in matchup_keys:
        if key != 'count':
            t1 = matchups['fantasy_content']['league'][1]['scoreboard']['0']['matchups'][str(key)]['matchup']['0']['teams']['1']['team']
            t2 = matchups['fantasy_content']['league'][1]['scoreboard']['0']['matchups'][str(key)]['matchup']['0']['teams']['0']['team']
            temp.append(t1)
            temp.append(t2)

    list_of_dfs = []

    for i in range(len(temp)):
        data = temp[i]
        # Initialize the output dictionary with relevant columns
        output = {
            "week": None,
            # 'winner_team_key': None,
            "team_key": None,
            "team_id": None,
            "name": None,
            "remaining_games": None,
            "live_games": None,
            "completed_games": None
        }

        # Populate the dictionary
        team_stats_dict = {label: None for label in stat_labels.values()}

        for item in data[0]:
            # Extract flat fields
            if "team_key" in item:
                output["team_key"] = item["team_key"]
            if "team_id" in item:
                output["team_id"] = item["team_id"]
            if "name" in item:
                output["name"] = item["name"]

        for item in data:
            if isinstance(item, dict):
                # Extract nested fields
                if "team_stats" in item:
                    output["week"] = item['team_stats']["week"]
                    for stat in item["team_stats"]["stats"]:
                        stat_id = stat["stat"]["stat_id"]
                        if stat_id in stat_labels:
                            team_stats_dict[stat_labels[stat_id]] = stat["stat"]["value"]
                if "team_remaining_games" in item:
                    output["remaining_games"] = item["team_remaining_games"]["total"]["remaining_games"]
                    output["live_games"] = item["team_remaining_games"]["total"]["live_games"]
                    output["completed_games"] = item["team_remaining_games"]["total"]["completed_games"]

        # Merge team stats into output dictionary
        output.update(team_stats_dict)

        # Convert to a single-row DataFrame
        df = pd.DataFrame([output])
        list_of_dfs.append(df)

    # Concatenate them vertically
    result = pd.concat(list_of_dfs, ignore_index=True)

    # Convert relevant columns to numeric types
    categories = ['week', 'team_id', 'remaining_games', 'live_games', 'completed_games',
                  'FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO']

    for col in categories:
        result[col] = pd.to_numeric(result[col], errors='coerce')

    stat_categories = ['FG%', 'FT%', '3PTM', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TO']

    # Rank each team within each category (lower is better for TO, higher is better for others)
    for category in stat_categories:
        if category == 'TO':  # Reverse ranking for TO since lower is better
            result[category + '_Rank'] = result[category].rank(ascending=True)
        else:
            result[category + '_Rank'] = result[category].rank(ascending=False)

    # Sum the ranks to get a total score (lower total rank is better)
    result['Aggregate Rank'] = result[[cat + '_Rank' for cat in stat_categories]].sum(axis=1)

    # Create a new column 'Adjusted_Rank' by ranking the 'Aggregate Rank' column
    result['Adjusted_Rank'] = result['Aggregate Rank'].rank(ascending=True, method='min')

    # Convert the ranks to integers
    result['Adjusted_Rank'] = result['Adjusted_Rank'].astype(int)

    # Sort teams by their total rank
    result = result.sort_values(by='Adjusted_Rank')
    return result

overall_stats = []
for i in range(1, curr_week_num + 1):
    result = overall_weekly_matchup_stats(i)
    overall_stats.append(result)

# Concatenate them vertically
final = pd.concat(overall_stats, ignore_index=True)

# Save the full data for all weeks (instead of just the current week)
final.to_csv("final.csv", index=False)

df = pd.DataFrame(lg.standings())

# Expand the 'outcome_totals' column into separate columns
outcome_totals_df = pd.json_normalize(df['outcome_totals'])

# Combine with the original DataFrame
standings = pd.concat([df.drop(columns=['outcome_totals', 'team_key']), outcome_totals_df], axis=1)

def extract_stat_winners(data):
    """
    Extracts the count of stat_winner occurrences for each matchup.
    """
    results = {}
    matchups = data.get("0", {}).get("matchups", {})

    for matchup_id, matchup_data in matchups.items():
        if not isinstance(matchup_data, dict):
            continue

        matchup = matchup_data.get("matchup", {})
        stat_winners = matchup.get("stat_winners", [])

        team_win_count = Counter(
            stat_winner["stat_winner"]["winner_team_key"]
            for stat_winner in stat_winners
            if isinstance(stat_winner, dict) and "stat_winner" in stat_winner and "winner_team_key" in stat_winner["stat_winner"]
        )
        results[matchup_id] = dict(team_win_count)

    return results

matchups = lg.matchups(week=curr_week_num)
test1 = matchups['fantasy_content']['league'][1]['scoreboard']
matchup_winners = extract_stat_winners(test1)  # Example usage

# Flatten matchup_winners into a list of dictionaries
flat_matchup_winners = []
for matchup_id, winners in matchup_winners.items():
    teams = list(winners.keys())
    scores = list(winners.values())

    if scores[0] >= scores[1]:
        team_a, team_b = teams[0], teams[1]
        score_a, score_b = scores[0], scores[1]
    else:
        team_a, team_b = teams[1], teams[0]
        score_a, score_b = scores[1], scores[0]

    winner = team_a if score_a > score_b else "Tie"

    flat_matchup_winners.append({
        "Matchup": f"{team_ids[team_a]} vs. {team_ids[team_b]}",
        "Score": f"{score_a} - {score_b}",
        "Lead": team_ids[winner] if winner != "Tie" else "Tie"
    })

df_matchups = pd.DataFrame(flat_matchup_winners)

standings.to_csv("standings.csv", index=False)
df_matchups.to_csv("df_matchups.csv", index=False)

with open('app.py', 'w') as f:
    f.write('''
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
from datetime import datetime
import pytz
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import players

# Get current date and time
current_datetime = datetime.now().strftime("%B %d, %Y - %I:%M %p")

# Page Configuration
st.set_page_config(
    page_title="Season 2 of Love Island (NBA)",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load Data
final_df = pd.read_csv("final.csv").dropna(how="all")  # Full data for all weeks
standings = pd.read_csv("standings.csv").dropna(how="all")
df_matchups = pd.read_csv("df_matchups.csv").dropna(how="all")

# Format column names for standings and matchups
standings.columns = standings.columns.str.replace("_", " ").str.title()
standings = standings.drop(columns=["Playoff Seed"])
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


# Convert percentage column to 2 decimals
standings['Percentage'] = np.round(standings['Percentage'], 2)

# Streamlit App
st.title("Season 2 of Love Island (NBA)")

# Sidebar Navigation
st.sidebar.title("🌐 Navigation")
pages = ["🏠 Home", "⛹🏽 Multi-player comparison", "🗣️ Free Agency"]
selection = st.sidebar.radio("Go to", pages)

# Get Current EST Time
est = pytz.timezone('US/Eastern')
current_time = datetime.now(est).strftime("%B %d, %Y - %I:%M %p")

# --------------- 🏠 HOME PAGE ---------------
if selection == "🏠 Home":

    st.markdown(f"### Week 15 🏀")
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

    # Remove unwanted columns: "Team Key", "Team Id", "Fgm/A", "Ftm/A"
    columns_to_remove = ["Team Key", "Team Id", "Fgm/A", "Ftm/A"]
    # Also remove any column whose name contains "_Rank" except "Adjusted_Rank"
    rank_columns = [col for col in week_data.columns if "_Rank" in col and col != "Adjusted_Rank"]
    columns_to_remove.extend(rank_columns)

    week_data = week_data.drop(columns=columns_to_remove)

    # Full-Width Traditional Statistics Table
    st.subheader("Traditional Statistics")
    st.markdown(f"""
        <p style="font-size: 14px; color: white; opacity: 0.7; margin-top: -10px;">
            <b>Aggregate Rank</b> – Summarizes all individual rankings into one total score. The lower the number, the better.
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
''')



conf.get_default().auth_token = NGROK_AUTH_TOKEN  # Set the token
public_url = ngrok.connect(8501).public_url
print(f"Streamlit app is live at: {public_url}")
