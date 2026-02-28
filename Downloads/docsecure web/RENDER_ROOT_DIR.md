# 🎯 Render Deployment — Root Directory Configuration

## ❓ What Render is Asking

When Render asks for **"Root Directory"**, it's asking:
> *"Where is the Python code in your repository?"*

---

## ✅ THE ANSWER FOR DOCSHIELD

### **Just Leave it BLANK or put: `.`**

That's it! Your files are at the root level.

---

## 📍 Directory Structure

Your GitHub repository looks like:
```
DOCSHIELD/ (root)
├── app.py ✅                    ← Render should find this
├── DOCUMENT_SECURER_WEB.py ✅  
├── requirements.txt ✅
├── Procfile ✅
├── README.md
├── LICENSE
└── (other files)
```

Since `app.py` and `requirements.txt` are at the **TOP level** of your repo, you should:

### **Leave Root Directory: BLANK**
or
### **Put: `.` (single dot)**

Both mean "look in the current/root folder of the repository"

---

## 🔧 Render Configuration Summary

| Field | Value |
|-------|-------|
| **Name** | `docshield` |
| **GitHub Repo** | `LeeMwas/DOCSHIELD` |
| **Branch** | `main` |
| **Root Directory** | ← **BLANK or `.`** |
| **Environment** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python app.py` |

---

## 🚫 DO NOT PUT

❌ `/Downloads/docsecure web`  
❌ `Downloads/docsecure web`  
❌ `c:\Users\PHILANI\...`  
❌ `app.py`  

These are for when your code is in a **subfolder** of the repo. Your code is in the **root**, so just leave it blank!

---

## ✅ AFTER Setting Root Directory

1. **Leave Root Directory BLANK** (important!)
2. **Click "Create Web Service"**
3. **Wait** 5-10 minutes
4. **Done!** Your app is deployed 🎉

---

## 🎯 If You Get an Error

**"Cannot find app.py"** 
→ Render can't see the file
→ Check: Did you put something in Root Directory?
→ **Leave it BLANK**

**"Module not found"**
→ Missing dependency
→ Edit `requirements.txt`
→ `git add .` → `git commit` → `git push`
→ Render auto-redeploys

---

## 📝 TL;DR

When Render asks for Root Directory:
### **Leave it BLANK or put `.`**

That's it! Go deploy! 🚀
