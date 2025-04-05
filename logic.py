# Standard library
import datetime
import json
import os
import random
import subprocess
from collections import Counter

# Third-party libraries
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import seaborn as sns
from yahoo_oauth import OAuth2
import yahoo_fantasy_api as yfa

team_ids = {'454.l.74601.t.1': "Sam's Swag Team",
            '454.l.74601.t.2': "Doomenshmirtz Evil Inc.",
            '454.l.74601.t.3': "Chunch's Challengers",
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

def authenticate_yahoo_api(path = "/etc/secrets/keypair.json"):
    # Load keypair.json
    keypair_file_path = path
    
    if os.path.exists(keypair_file_path):
        with open(keypair_file_path, "r") as f:
            keypair = json.load(f)
    else:
        raise FileNotFoundError(f"Secret file {keypair_file_path} not found.")
    
    # Manually initialize OAuth2 with token_time & token_type to prevent verifier request
    try:
        sc = OAuth2(
            keypair["consumer_key"], 
            keypair["consumer_secret"], 
            access_token=keypair["access_token"], 
            refresh_token=keypair["refresh_token"],
            token_time=keypair["token_time"],
            token_type=keypair["token_type"]
        )
    
        # Refresh token if expired
        if not sc.token_is_valid():
            print("🔄 Refreshing expired token...")
            sc.refresh_access_token()
    
            # Save the updated token
            with open(keypair_file_path, "w") as f:
                json.dump(sc.credentials, f)
            print("✅ Token refreshed and saved!")
    except Exception as e:
        raise RuntimeError(f"OAuth authentication failed: {str(e)}")

     # get game object
    gm = yfa.Game(sc, 'nba')
    
    # get league ids (could be multiple if you're in more than 1)
    leagues = gm.league_ids()
    
    # get the league object
    lg = gm.to_league(leagues[0])

    return lg
    
# grabs everyone's stats and data regarding week n using the matchups function
def overall_weekly_matchup_stats(lg, week_num):
    """
    Fetches and processes weekly matchup stats from Yahoo Fantasy API.
    Returns a DataFrame with structured data for all teams in that week.
    """
    
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

def get_full_season_stats(lg):
    curr_week_num = lg.current_week()
    weekly_stats = [overall_weekly_matchup_stats(lg, i) for i in range(1, curr_week_num + 1)]
    final = pd.concat(weekly_stats, ignore_index=True)
    return final

def get_standings(lg):
    df = pd.DataFrame(lg.standings())
    outcome_df = pd.json_normalize(df['outcome_totals'])
    standings = pd.concat([df.drop(columns=['outcome_totals', 'team_key']), outcome_df], axis=1)
    return standings

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

def get_matchups_df(lg):
    curr_week_num = lg.current_week()
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
    return df_matchups


def get_team_logos(lg, team_ids):
    logos = {team_name: lg.to_team(key).details()["team_logos"][0]["team_logo"]["url"]
        for key, team_name in team_ids.items()}
    team_logos = pd.DataFrame(logos.items(), columns=["Team", "Logo URL"])
    return team_logos
