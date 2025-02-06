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


# **NBA Fantasy Dashboard Deployment Guide**

This project is a **Yahoo NBA Fantasy API dashboard**, hosted on **Render.com**. This guide covers **key lessons learned** during development and deployment.

---

## **1️⃣ OAuth2 Authentication with Yahoo Fantasy API**

### **What I Learned**
- Yahoo Fantasy API uses **OAuth2**, which requires **client credentials** (client ID, client secret).
- A **keypair.json** file is needed to store **access tokens** and **refresh tokens**.
- **Access tokens expire**, so we need to **refresh tokens automatically**.
- The `redirect_uri=oob` (out-of-band) **is deprecated**—we need to specify a valid redirect URI in Yahoo Developer settings.
- **Manually initializing OAuth2** in Python helps avoid interactive prompts:
  
  ```python
  sc = OAuth2(
      keypair["consumer_key"],
      keypair["consumer_secret"],
      access_token=keypair["access_token"],
      refresh_token=keypair["refresh_token"],
      token_time=keypair["token_time"],  
      token_type=keypair["token_type"]  
  )
  ```

- **Refreshing the token** automatically when expired:
  
  ```python
  if not sc.token_is_valid():
      sc.refresh_access_token()
  ```

---

## **2️⃣ Environment Variables & Secrets Management**

### **What I Learned**
- **Do not hardcode API keys** (use environment variables or secret files).
- In **Render.com**, store secrets in **Environment Variables** or `/etc/secrets/`.
- Access them in Python like this:
  
  ```python
  import os
  NGROK_AUTH_TOKEN = os.getenv("NGROK_AUTH_TOKEN")
  ```

- **Updating keypair.json** requires replacing the secret file manually.

---

## **3️⃣ Why We Removed Ngrok**

### **What I Learned**
- **Ngrok is useful for local testing**, but it’s not needed for Render hosting.
- Free Ngrok accounts allow **only one active tunnel at a time**, which caused `ERR_NGROK_108`.
- Render **already provides a public URL**, so **Ngrok is unnecessary**.

### **Steps Taken**
✅ **Removed all Ngrok references** from the code.  
✅ **Used Render's default URL** instead of manually tunneling traffic.  
✅ **Updated the start command** for Render.

---

## **4️⃣ Deploying to Render.com**

### **What I Learned**
- Create a **`render.yaml`** file to define deployment settings.
- Store API keys in **Render's Environment Variables** or **Secret Files**.
- **Start Command for Streamlit apps**:
  
  ```bash
  streamlit run app.py --server.port $PORT --server.address 0.0.0.0
  ```
  
- **Debugging Render Logs** helps fix deployment issues.

---

## **5️⃣ Using Git Properly**

### **What I Learned**
- **Avoid pushing secrets to GitHub** (use `.gitignore`).
- Commands to remove sensitive files from GitHub history:
  
  ```bash
  git rm --cached keypair.json ngrok_token.txt
  echo "keypair.json" >> .gitignore
  echo "ngrok_token.txt" >> .gitignore
  git commit -m "Removed sensitive files from GitHub"
  git push origin main
  ```
  
- Keep **environment variables secure** instead of storing secrets in the repo.

---

## **Conclusion**

🔥 **Now the app is fully deployed on Render.com** and can be shared with others!  
💡 **Biggest lesson:** OAuth2, token handling, and proper deployment practices are crucial for real-world web applications.

---

### **Next Steps**
- ✅ **Share the Render URL** with friends.  
- ✅ **Monitor logs for any errors**.  
- ✅ **Add new features** (e.g., more data visualizations).  

---

This README summarizes everything you’ve learned—**let me know if you want to add anything!** 🚀🔥

