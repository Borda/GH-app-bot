# 🤖 Trivial GitHub Bot: Issue & PR Commenter

This README will help you install and run a GitHub bot that automatically posts comments on issue creation and PR closure.
It's lightweight, easy to run, and hosted securely via **Lightning Studio**.

______________________________________________________________________

## 🚀 Overview

This bot does the following:

- 💬 Posts a thank-you comment when someone opens an issue
- 🙏 Posts a PR acknowledgment when a pull request is closed (merged or not)

The Python implementation is already included in the file `greeter-bot.py`.

______________________________________________________________________

## 📦 Dependencies

Make sure Python 3.8+ is installed in your Studio environment. Then run:

```bash
pip install aiohttp gidgethub
```

______________________________________________________________________

## 🔐 GitHub Token Authentication

1. Go to: **GitHub → Settings → Developer Settings → Personal Access Tokens**
2. Click **“Fine-grained tokens”** or classic token
3. Generate a token with the following scopes:
   - ✅ `public_repo` or `repo` (if needed for private repos)
4. In your Studio session, set this token as an environment variable:
   ```bash
   export GH_AUTH=ghp_YourTokenGoesHere
   ```

If your project allows, you can store this securely via Studio configuration.

______________________________________________________________________

## 🌐 Expose Bot via Lightning Studio

Lightning Studio gives you a **public URL** when you expose a port.

👉 Follow this guide:\
📖 [Deploy on Public Ports – Lightning Studio Docs](https://lightning.ai/docs/overview/build-with-studios/deploy-on-public-ports)

In short:

1. Ensure your Python app binds to all interfaces:
   ```python
   web.run_app(app, host="0.0.0.0", port=8000)
   ```
2. Use Studio’s Port Exposer to make port `8000` public
3. Copy the public HTTPS URL provided

______________________________________________________________________

## 🔧 Register GitHub Webhook

Go to your GitHub repository → **Settings → Webhooks** → **Add Webhook**

- **Payload URL**: `https://<your-studio-url>.lightning.ai/`
- **Content type**: `application/json`
- **Secret**: _(leave blank or implement in code)_
- **Events**:
  - ✅ `Issues`
  - ✅ `Pull requests`

______________________________________________________________________

## 🧪 Run It

Start your bot in Studio:

```bash
python greeter-bot.py
```

Once live, test by:

- Opening a new issue
- Merging or closing a PR

You should see comments posted automatically by your bot!

______________________________________________________________________

## ✅ You're Done!

This is the simplest possible GitHub bot to get started—and now it’s ready to grow.
Want it to assign labels? Parse issue content? Trigger CI jobs?

PRs welcome. Bots await 🫡
