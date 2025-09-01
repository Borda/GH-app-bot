# ✅ GitHub App Setup: GitHub CI Bot

This guide helps you set up a GitHub App that listens to repository events and triggers status checks via the **Checks API**. Your bot will:

- Respond to push events and pull request updates (`push` and `pull_request` events)
- Support configurable triggers to run on specific events and branches based on your YAML workflow configuration
- Create a check run for the commit (`status: in_progress`)
- Perform validation tasks (like config validation, testing, or linting)
- Complete the check run with `"success"` or `"failure"`

______________________________________________________________________

## 🛠 1. Create the GitHub App

1. Navigate to [GitHub Developer Settings → GitHub Apps](https://github.com/settings/apps)

2. Click **"New GitHub App"**

3. Fill in:

   - **GitHub App name**: e.g., `pr-checker-bot`
   - **Homepage URL**: your repo or organization URL
   - **Webhook URL**: public URL from Lightning Studio (see below)
   - **Webhook secret**: optional (used if implementing signature validation)

4. Under **Repository permissions**:

   - ✅ `Checks`: Read & write
   - ✅ `Pull requests`: Read-only (or read & write if needed)
   - ✅ `Contents`: Read-only (to fetch files at a specific commit)

5. Under **Subscribe to events**:

   - ✅ `Push` (required for push event validation)
   - ✅ `Pull request` (required for PR validation)
   - ✅ `Check run` (required for rerun failed checks support)

6. Click **Create GitHub App**

______________________________________________________________________

## 🔑 2. Generate App Credentials

1. Click **"Generate a private key"** → download the `.pem` file

2. Note:

   - Your GitHub **App ID**
   - Your private key path

3. Export in your Studio session:

   ```bash
   export GITHUB_APP_ID=your_app_id
   export PRIVATE_KEY_PATH=/full/path/to/private-key.pem
   export WEBHOOK_SECRET=your_webhook_secret
   ```

______________________________________________________________________

## 🌐 3. Deploy on Lightning Studio

📖 Docs: [Deploy on Public Ports](https://lightning.ai/docs/overview/build-with-studios/deploy-on-public-ports)

1. Ensure your bot binds to all interfaces:

   ```python
   web.run_app(app, port=8000)
   ```

2. From your Studio, expose port `8000` to the public internet

3. Copy the generated public HTTPS URL

4. Use that as the **Webhook URL** in your App settings

______________________________________________________________________

## 🔧 4. Install the App on a Repository

1. From your App settings, click **Install App**
2. Choose a repository or organization
3. Confirm installation

______________________________________________________________________

## 🚦 5. How the Bot Works

Your bot handles `push`, `pull_request`, and `check_run` events and:

1. **Event Filtering**: Checks your workflow configurations to determine if the event should trigger validation based on trigger criteria (event type, branch patterns, etc.)
2. **Check Run Creation**: Creates a **Check Run** with status `in_progress` for applicable workflows
3. **Repository Processing**: Downloads the repository content at the specific commit
4. **Workflow Execution**: Runs configured validation logic based on your `.lightning/workflows/*.yml` files
5. **Status Reporting**: Submits the final status with:
   - `conclusion: success` ✅
   - `conclusion: failure` ❌

**For rerun failed checks**: When users click "Re-run" on a failed check, the bot receives a `check_run` event with `rerequested` action and processes only that specific check again.

**Trigger Configuration**: Your YAML workflow files can specify which events and branches should trigger validation, allowing you to run different checks for pushes vs pull requests, or only on specific branches.

______________________________________________________________________

This is your starting point for building scalable, bot-powered CI logic with native GitHub Checks support 🚀
