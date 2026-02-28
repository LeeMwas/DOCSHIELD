# 🚀 Deploy DocShield to PythonAnywhere (EASIEST)

**PythonAnywhere** - The simplest way to deploy Flask apps!

---

## ✅ Why PythonAnywhere?
- ✅ **Designed for Python** - Makes it super easy
- ✅ **Free tier available** - Just sign up
- ✅ **Web-based editor** - No terminal needed
- ✅ **Automatic SSL** - HTTPS included
- ✅ **Perfect for Flask** - Native support

---

## 📋 Prerequisites

1. **GitHub Repository** ✅
   - LeeMwas/DOCSHIELD

2. **PythonAnywhere Account** (Free)
   - Sign up: https://www.pythonanywhere.com
   - Free account available

---

## 🎯 Step 1: Create PythonAnywhere Account

1. Go to: https://www.pythonanywhere.com/pricing/
2. Click: **Create a Beginner account** (Free)
3. Sign up with email or GitHub
4. **Verify** your email

---

## 🎯 Step 2: Clone Your Repository

In PythonAnywhere **Bash Console**:

1. **Dashboard** → **Consoles** → **Bash**
2. Run these commands:

```bash
cd /home/your_username

git clone https://github.com/LeeMwas/DOCSHIELD.git

cd DOCSHIELD

pip install -r requirements.txt
```

---

## 🎯 Step 3: Configure Web App

1. **Dashboard** → **Web**
2. **Add a new web app**
3. **Choose Framework**: Flask
4. **Choose Python**: 3.10
5. **Full path**: `/home/your_username/DOCSHIELD/app.py`

---

## 🎯 Step 4: Edit WSGI Configuration

The WSGI file connects your app to PythonAnywhere:

1. **Web** → **WSGI configuration file**
2. **Find and edit** section:

```python
import sys

path = '/home/your_username/DOCSHIELD'
if path not in sys.path:
    sys.path.append(path)

from app import flask_app as application
```

---

## 🎯 Step 5: Set Environment Variables

1. **Web** → **Environment variables**
2. **Add these:**

```
DATABASE_URL=postgresql://doc_shield_user:KudGYk0cMyczIMSDgpUTkApibFbIxvX9@dpg-d6hbii7gi27c73fnjb20-a.oregon-postgres.render.com/doc_shield?sslmode=require

FLASK_ENV=production
FLASK_DEBUG=False
```

---

## 🎯 Step 6: Reload & Deploy

1. **Web** → **Reload** (big green button)
2. **Wait** for app to start
3. **Visit** your URL: `https://your_username.pythonanywhere.com`

**Done!** 🎉

---

## ✅ Verify Deployment

Open: `https://your_username.pythonanywhere.com`
- Should see DocShield interface
- Click "Camera Scanner"
- Should work on mobile too!

---

## 🔄 Update Your Code

After making changes:

```bash
# In PythonAnywhere Bash Console:
cd /home/your_username/DOCSHIELD
git pull origin main
```

Then:
1. **Web** → **Reload** (the green button)
2. **Done!** App is updated

---

## 📱 Access Your App

### Web
```
https://your_username.pythonanywhere.com/
https://your_username.pythonanywhere.com/admin
```

### Mobile
1. Open URL on phone
2. Internet works (your WiFi/4G)
3. HTTPS works automatically
4. Camera works on Android/iOS

---

## 💾 Database Settings

Your PostgreSQL:
- **Host**: dpg-d6hbii7gi27c73fnjb20-a.oregon-postgres.render.com
- **Port**: 5432
- **Database**: doc_shield
- **User**: doc_shield_user
- **Password**: KudGYk0cMyczIMSDgpUTkApibFbIxvX9

PythonAnywhere connects via your DATABASE_URL environment variable.

---

## 🐛 Troubleshooting

### "502 Bad Gateway"
```
→ App crashed
→ Check Error logs: Web → Error log
→ Fix in Bash: cd DOCSHIELD && python -c "import DOCUMENT_SECURER_WEB"
→ Reload app
```

### "ImportError: No module named..."
```
→ Dependency not installed
→ In Bash Console:
pip install missing_module
→ Reload app
```

### "Cannot connect to database"
```
→ Check DATABASE_URL is correct
→ Test with: psql [your DATABASE_URL]
→ Make sure PostgreSQL IP allows connections
```

### "No such file or directory"
```
→ Check path in WSGI file
→ Should be: /home/YOUR_USERNAME/DOCSHIELD/app.py
→ Replace YOUR_USERNAME with actual username
```

---

## 🎯 Free Tier Limitations

PythonAnywhere Free Account:
- ✅ **Web app hosting** - Unlimited
- ✅ **Python version** - Full access
- ✅ **HTTPS** - Automatic
- ⚠️ **External databases** - Must allow connections
- ⚠️ **Custom domain** - Not in free tier

**Upgrade to Paid**:
- $5/month - Custom domain + more features
- $20/month - Full professional

---

## 🔐 Security

Keep your DATABASE_URL:
- ✅ In Environment Variables (NOT code)
- ✅ Secret from `.py` files
- ✅ Safe in PythonAnywhere dashboard

Never:
- ❌ Put in code
- ❌ Commit to GitHub
- ❌ Share with anyone

---

## 📊 Monitor Your App

PythonAnywhere Dashboard shows:
- **Web** tab → Server status
- **Error log** → Any crash errors
- **Server log** → Request logs
- **CPU/Memory** → Usage stats

---

## 🆙 When You Need More

**Free tier limitations reached?**

Upgrade options:
- **$5/month** - Custom domain
- **$20/month** - Professional tier
- **Switch to Render/Railway** - If cheaper

---

## 🎉 You're Live!

Your DocShield:
- ✅ Is running on PythonAnywhere
- ✅ Has HTTPS (secure)
- ✅ Is accessible from anywhere
- ✅ Can scan QR codes on phones
- ✅ Has admin dashboard
- ✅ Is completely FREE

---

## 📧 Support

- PythonAnywhere Help: https://www.pythonanywhere.com/help/
- FAQ: https://www.pythonanywhere.com/faq/
- Forums: https://www.pythonanywhere.com/forums/

---

**Your DocShield is LIVE and FREE!** 🚀

Share the URL and start verifying documents!
