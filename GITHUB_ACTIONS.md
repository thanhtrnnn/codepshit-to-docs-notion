# GitHub Actions Setup Guide

This guide explains how to configure GitHub Actions to automatically run the Python scripts in this repository.

## Overview

Three automated workflows are included:

| Workflow | Schedule | Description |
|----------|----------|-------------|
| Sync to Google Docs | Daily at 2 AM UTC | Scrapes submissions and updates Google Docs table |
| Sync to Notion | Daily at 2 AM UTC | Syncs submissions to Notion database |
| Export Problem Topics | Weekly (Monday 3 AM UTC) | Exports problem topics and commits to repo |

All workflows can also be triggered manually from the GitHub Actions tab.

## Initial Setup

### Step 1: Fork or Clone the Repository

Make sure you have this repository in your GitHub account.

### Step 2: Configure Repository Secrets

Go to your repository on GitHub → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

### Step 3: Add Required Secrets

#### Common Secrets (Required for All Workflows)

These secrets are needed for authentication and web scraping:

```
LIST_URL
  Example: https://code.ptit.edu.vn/student/history
  Your submissions list page URL

AUTO_LOGIN
  Example: true
  Enable Selenium auto-login (set to "true" or "false")

LOGIN_URL
  Example: https://code.ptit.edu.vn/login
  The login page URL

LOGIN_USERNAME
  Example: B23DCCC123
  Your account username

LOGIN_PASSWORD
  Your account password

USERNAME_SELECTOR
  Example: #login__user
  CSS selector for the username input field

PASSWORD_SELECTOR
  Example: #login__pw
  CSS selector for the password input field

SUBMIT_SELECTOR
  Example: button[type='submit']
  CSS selector for the login submit button
```

**Optional Common Secrets:**

```
COOKIE_STRING
  Manual cookie string as fallback if auto-login fails
  Example: sessionid=abc123; token=xyz789

USER_AGENT
  Custom browser user agent string
  Default: Mozilla/5.0 (Windows NT 10.0; Win64; x64)...
```

#### Google Docs Workflow Secrets

Only needed if you want to use the `sync-to-docs.yml` workflow:

```
GOOGLE_APPLICATION_CREDENTIALS
  The ENTIRE contents of your service account JSON file
  Copy and paste the complete JSON from your credentials file

GOOGLE_DOC_ID
  Example: 1a2b3c4d5e6f7g8h9i0j
  The document ID from your Google Docs URL

DOC_SECTION
  Example: CHUONG 2 > Bai tap > codeptit
  The heading text that identifies which table to update
```

#### Notion Workflow Secrets

Only needed if you want to use the `sync-to-notion.yml` workflow:

```
NOTION_API_KEY
  Your Notion integration secret token
  Example: secret_abc123xyz...

NOTION_DATABASE_ID
  Your Notion database ID
  Example: 1a2b3c4d5e6f...
```

**Optional Notion Secrets:**

```
ENABLE_PAGINATION
  Example: true
  Enable multi-page scraping

PAGE_PARAM
  Example: page
  URL query parameter for pagination

MAX_PAGES
  Example: 3
  Maximum number of pages to scrape

NOTION_RATE_DELAY
  Example: 0.5
  Delay in seconds between Notion API calls
```

## How to Get Credentials

### Google Service Account JSON

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable Google Docs API and Google Drive API
4. Create a service account
5. Generate and download JSON key file
6. Share your Google Doc with the service account email (give Editor permissions)
7. Copy the ENTIRE contents of the JSON file and paste it as the `GOOGLE_APPLICATION_CREDENTIALS` secret

### Notion API Key

1. Go to [Notion Integrations](https://www.notion.so/my-integrations)
2. Create a new integration
3. Copy the "Internal Integration Token"
4. Share your Notion database with the integration
5. Get the database ID from the database URL

### CSS Selectors

To find CSS selectors for login form fields:

1. Open your login page in Chrome/Firefox
2. Right-click on the username field → Inspect
3. Look at the HTML element (e.g., `<input id="login__user">`)
4. The selector is `#login__user` for id, or `.classname` for class

## Enable Workflows

1. Go to the **Actions** tab in your GitHub repository
2. If you see a message about workflows, click **"I understand my workflows, go ahead and enable them"**
3. You should now see the three workflows listed

## Manual Trigger

To run a workflow immediately:

1. Go to **Actions** tab
2. Select the workflow (e.g., "Sync Submissions to Google Docs")
3. Click **Run workflow** button
4. Select branch (usually `main`)
5. Click **Run workflow**

## Viewing Results

- Click on any workflow run to see detailed logs
- Artifacts (batch_result.json, problem_topics.json) are available for download
- Failed runs will send you an email notification (if enabled in your GitHub settings)

## Customizing Schedules

Edit the workflow files to change when they run:

```yaml
on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM UTC
```

Common cron examples:
- `0 2 * * *` - Daily at 2 AM
- `0 */6 * * *` - Every 6 hours
- `0 3 * * 1` - Every Monday at 3 AM
- `0 0 * * 0` - Every Sunday at midnight

Use [crontab.guru](https://crontab.guru/) to create custom schedules.

## Troubleshooting

### Workflow fails with "Secret not found"
- Check that all required secrets are added in Settings → Secrets and variables → Actions
- Secret names must match exactly (case-sensitive)

### Auto-login fails
- Verify LOGIN_URL, LOGIN_USERNAME, LOGIN_PASSWORD are correct
- Check CSS selectors are accurate for the current login page
- Add COOKIE_STRING as a fallback

### Google Docs errors
- Ensure the service account JSON is valid
- Verify the Doc is shared with the service account email
- Check DOC_SECTION matches a heading in your document exactly

### Notion errors
- Verify the API key is valid and not expired
- Ensure the database is shared with the integration
- Check that database properties match expected names

### ChromeDriver issues
- The workflow uses `browser-actions/setup-chrome@v1` which handles Chrome and ChromeDriver installation automatically
- If issues persist, check the Actions logs for specific error messages

## Security Notes

- Never commit secrets to the repository
- Use repository secrets for sensitive data
- Secrets are masked in workflow logs
- Consider using environment-specific secrets for testing vs production

## Support

For issues specific to:
- GitHub Actions: Check [GitHub Actions documentation](https://docs.github.com/en/actions)
- This repository: Open an issue on GitHub
- The scripts themselves: See the main README.md
