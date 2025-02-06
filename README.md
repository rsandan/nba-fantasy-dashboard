# **Yahoo! NBA Fantasy Dashboard Deployment Guide**

This is a **Yahoo NBA Fantasy API dashboard**, hosted on **Render.com**. This guide covers **key lessons learned** during development and deployment.

---

## **1️⃣ OAuth2 Authentication with Yahoo Fantasy API**

### **What I Learned**
- Yahoo Fantasy API uses **OAuth2**, which requires **client credentials** (client ID, client secret). So make sure you create a Yahoo Developer account.
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

