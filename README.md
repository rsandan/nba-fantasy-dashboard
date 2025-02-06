*January 10, 2025*

# Yahoo NBA Fantasy App League Analysis #

In my first run around with playing fantasy hoops, I wanted to explore the capabilities of Yahoo's Fantasy API and see what insightful extractions I can make out of it -- like analyzing the transaction data and seeing if there are any patterns of players added/dropped at certain times, etc. Hosting this project on render.com made me learn a lot of things, with a lot of *debugging sessions* (a looot). I tackled through a lot of things:
- OAuth 2.0 (specifically OAuth initialization without requiring user input/login)
- Writing Environment variables / secret files for mainly for yahoo's access tokens and refresh tokens (no more Enter Verifier: errors)
- Successful Deployment (via Render.com)
  


## Yahoo! Fantasy League Details ##
**League Name**: Season 2 of Love Island (NBA)

**League Details**: H2H 10T 9CAT
  - H2H - Head to head (Weekly matchup)
  - 10T - 10 Team
  - 9CAT - 9 Categories (FT%, FG%, 3PTM, PTS, TREB, AST, STL, BLK, TO)



  For reference:
  - [Yahoo Fantasy API documentation](https://yahoo-fantasy-api.readthedocs.io/en/latest/yahoo_fantasy_api.html)
  - [Render.com Documentation](https://render.com/docs)

  ---
