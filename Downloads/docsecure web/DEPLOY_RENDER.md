# 🚀 Deploy DocShield to Render.com (FREE)

**Render.com** is the BEST and EASIEST free option for deploying DocShield!

---

## ✅ Why Render?
- ✅ **FREE tier** - $0/month to start
- ✅ **Python/Flask support** - Perfect match
- ✅ **PostgreSQL included** - Already have database there
- ✅ **Auto HTTPS** - SSL certificates free
- ✅ **Easy GitHub integration** - Auto-deploy on push
- ✅ **Auto-sleep protection** - Keep your app awake for free

---

## 📋 Prerequisites

Before deploying, you need:

1. **GitHub Repository** ✅ (You already have this!)
   - https://github.com/LeeMwas/DOCSHIELD

2. **Render Account** (Free)
   - Sign up: https://render.com

3. **PostgreSQL URL**
   - Your existing database: `postgresql://doc_shield_user:...@dpg-d6hbii7gi27c73fnjb20-a.oregon-postgres.render.com/doc_shield`

---

## 🎯 Step 1: Push Latest Code to GitHub

Make sure your `main` branch is up to date:

```bash
cd "C:\Users\PHILANI\Downloads\docsecure web"
git add .
git commit -m "Prepare for Render deployment"
git push origin main
```

---

## 🎯 Step 2: Create Render Web Service

1. **Go to:** https://render.com/dashboard
2. **Click:** "New +" → "Web Service"
3. **Select Repository:** LeeMwas/DOCSHIELD
4. **Grant Access:** Authorize GitHub

---

## 🎯 Step 3: Configure Deployment Settings

| Setting | Value |
|---------|-------|
| **Name** | `docshield` |
| **Environment** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python app.py` |
| **Instance Type** | `Free` |
| **Region** | `Ohio` (free tier) |

---

## 🎯 Step 4: Add Environment Variables

Click "Advanced" and add:

```
DATABASE_URL=postgresql://doc_shield_user:KudGYk0cMyczIMSDgpUTkApibFbIxvX9@dpg-d6hbii7gi27c73fnjb20-a.oregon-postgres.render.com/doc_shield?sslmode=require

FLASK_ENV=production

FLASK_DEBUG=False
```

---

## 🎯 Step 5: Deploy!

1. **Click:** "Create Web Service"
2. **Wait:** ~5-10 minutes for deployment
3. **Check:** Logs for any errors
4. **Access:** Your deployment URL (something like `https://docshield.onrender.com`)

---

## ✅ Verify Deployment

Go to your Render dashboard:
- **Service**: docshield
- **Status**: Should show "Live"
- **URL**: Click to visit your deployed app

---

## 📸 Using Your Deployed App

### Public URL
```
https://docshield.onrender.com/
https://docshield.onrender.com/admin
```

### Mobile/Android
1. Open app on phone: `https://docshield.onrender.com`
2. Camera will work (HTTPS enabled)
3. Tap "Advanced" on cert warning

---

## 🔄 Auto-Deploy on Updates

Every time you push to GitHub (`main` branch):
```bash
git push origin main
```

Render **automatically redeploys** your app! 🎉

---

## 🆓 Keep App Awake (Free)

Free tier apps sleep after 15 minutes of inactivity. Keep it awake:

**Option 1: Use Render's Keep-Alive** (Recommended)
- In Render dashboard → Settings
- Add a cron job to ping your app every 5 minutes

**Option 2: Use UptimeRobot** (Free)
1. Sign up: https://uptimerobot.com (free)
2. Add monitor: `https://docshield.onrender.com/`
3. Check every 5 minutes (free plan)

---

## 💾 Database Management

Your PostgreSQL is already set up at Render! Access it:

1. **Render Dashboard** → Your PostgreSQL instance
2. **Internal Database URL** → Use for connections
3. **Backups** → Render does daily backups (free)

---

## 🐛 Troubleshooting

### "Build failed"
```
→ Check Logs in Render dashboard
→ Likely missing dependency in requirements.txt
→ Add it and git push to redeploy
```

### "No module named psycopg2"
```
→ Already in requirements.txt
→ But if missing, run: pip install psycopg2-binary
→ Then git push again
```

### "Static files not serving"
```
→ DocShield doesn't use static files
→ Everything is embedded in HTML
→ Should work fine
```

### "Certificate warning on Android"
```
→ This is NORMAL for self-signed certs
→ Not an issue with proper HTTPS from Render
→ It will show green lock icon
```

---

## 🎉 Success!

Your app is now:
- ✅ **Live online**
- ✅ **Accessible globally**
- ✅ **Auto-scaling**
- ✅ **Free to run**
- ✅ **Auto-deploying**

Share your URL:
```
https://docshield.onrender.com
```

---

## 📈 Scale Up Later

As your app grows:
1. Upgrade to **Paid Plan** ($7/month)
2. Get **more CPU/RAM**
3. Remove sleep timeout
4. Better performance

---

## 🆚 Other Free Options

| Platform | Pros | Cons |
|----------|------|------|
| **Render** | Easy, Python-native | Free tier has limits |
| **Railway** | Generous free tier | Less beginner-friendly |
| **PythonAnywhere** | Simple Python hosting | Limited customization |
| **Vercel** | Super fast | Node.js/Static only |
| **Firebase** | Serverless | Requires restructuring |

---

## 📞 Support

- Render Docs: https://render.com/docs
- Django/Flask Guide: https://render.com/docs/deploy-flask
- GitHub Integration: https://render.com/docs/github

---

**Your DocShield is now LIVE and FREE!** 🚀

Next: Monitor it and collect real verification data!
