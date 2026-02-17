# Outlook / Microsoft 365 OAuth2 Setup Guide

This guide explains how to configure OAuth2 authentication for **Outlook / Microsoft 365** email ingestion via IMAP.

## Prerequisites

- A Microsoft 365 or Outlook.com account
- Access to [Azure Portal](https://portal.azure.com) (Azure Active Directory)
- Admin consent may be required for organizational accounts

## Step 1: Register an Application in Azure AD

1. Go to [Azure Portal → App registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Click **"New registration"**
3. Fill in:
   - **Name**: `TT Email Ingestion` (or your preferred name)
   - **Supported account types**: Choose based on your needs:
     - *Accounts in this organizational directory only* — single tenant
     - *Accounts in any organizational directory and personal Microsoft accounts* — multi-tenant + personal
   - **Redirect URI**: Select **Web** → `http://localhost:8080`
4. Click **Register**
5. Note the **Application (client) ID** — this is your `MICROSOFT_CLIENT_ID`
6. Note the **Directory (tenant) ID** — this is your `MICROSOFT_TENANT_ID`

## Step 2: Configure API Permissions

1. In your app registration, go to **API permissions**
2. Click **Add a permission**
3. Select **APIs my organization uses** → search for **Office 365 Exchange Online**
4. Select **Delegated permissions**
5. Check **IMAP.AccessAsUser.All**
6. Click **Add permissions**
7. If you see a warning about admin consent, click **Grant admin consent for [your org]** (requires admin role)

> **Note**: `IMAP.AccessAsUser.All` is required for OAuth2-based IMAP access. This is under the Office 365 Exchange Online API, not Microsoft Graph.

## Step 3: Create a Client Secret (Optional)

For **confidential client** apps (server-side, no user interaction after initial setup):

1. Go to **Certificates & secrets**
2. Click **New client secret**
3. Add a description and choose an expiry period
4. Click **Add**
5. Copy the secret **Value** immediately (it won't be shown again) — this is your `MICROSOFT_CLIENT_SECRET`

> For **public client** apps (device code flow without a secret), you can leave `MICROSOFT_CLIENT_SECRET` empty. Make sure to enable "Allow public client flows" under **Authentication → Advanced settings**.

## Step 4: Enable IMAP for the Mailbox

For Microsoft 365 organizational accounts, IMAP must be enabled:

1. Go to [Microsoft 365 Admin Center](https://admin.microsoft.com)
2. Navigate to **Users → Active users → Select user → Mail → Manage email apps**
3. Ensure **IMAP** is checked
4. Click **Save changes**

For personal Outlook.com accounts, IMAP is enabled by default.

## Step 5: Configure Environment Variables

Add the following to your `.env` file:

```env
# Email provider selection
EMAIL_PROVIDER=outlook

# Microsoft OAuth2 credentials
MICROSOFT_CLIENT_ID=your-application-client-id
MICROSOFT_CLIENT_SECRET=your-client-secret    # Leave empty for public client apps
MICROSOFT_TENANT_ID=your-directory-tenant-id  # Or 'common' for multi-tenant

# IMAP settings for Outlook
IMAP_HOST=outlook.office365.com
IMAP_PORT=993
IMAP_USER=your-email@outlook.com
```

### Settings Reference

| Environment Variable | Description | Default |
|---|---|---|
| `EMAIL_PROVIDER` | Provider selection | `gmail` |
| `MICROSOFT_CLIENT_ID` | Azure AD Application (client) ID | _(required)_ |
| `MICROSOFT_CLIENT_SECRET` | Client secret (empty for public apps) | `""` |
| `MICROSOFT_TENANT_ID` | Azure AD Directory (tenant) ID | `common` |
| `MICROSOFT_REDIRECT_URI` | OAuth2 redirect URI | `http://localhost:8080` |
| `MICROSOFT_TOKEN_FILE` | Path to token cache file | `tokens/outlook_token.json` |
| `IMAP_HOST` | IMAP server hostname | `imap.gmail.com` |
| `IMAP_PORT` | IMAP server port | `993` |
| `IMAP_USER` | Email address | _(required)_ |

## Step 6: Run Initial Authentication

```bash
# Run the OAuth2 setup flow
python producer.py --provider outlook --auth-setup

# Or use the standalone auth module
python -m src.auth.oauth2_outlook --setup
```

The device code flow will display instructions like:

```
============================================================
Microsoft Account Authentication
============================================================

To sign in, use a web browser to open the page
https://microsoft.com/devicelogin and enter the code XXXXXXXX
to authenticate.

============================================================
```

1. Open the URL in a browser
2. Enter the device code
3. Sign in with your Microsoft account
4. Grant the requested permissions
5. The token will be saved automatically

## Step 7: Start the Producer

```bash
# Using CLI args
python producer.py --provider outlook --username your-email@outlook.com

# Or using environment variables (EMAIL_PROVIDER=outlook in .env)
python producer.py --username your-email@outlook.com
```

## Troubleshooting

### "AUTHENTICATE failed" error
- Ensure IMAP is enabled for the mailbox (Step 4)
- Verify the `IMAP.AccessAsUser.All` permission is granted (Step 2)
- Check that admin consent has been granted for organizational accounts

### "AADSTS700016: Application not found"
- Verify `MICROSOFT_CLIENT_ID` matches the Application (client) ID in Azure portal
- Check `MICROSOFT_TENANT_ID` matches your directory

### "AADSTS65001: The user or administrator has not consented"
- An admin needs to grant consent for the `IMAP.AccessAsUser.All` permission
- Or the user needs to consent during the device code flow

### Token refresh failures
- Re-run `python producer.py --provider outlook --auth-setup` to get a new token
- Check if the client secret has expired (they have configurable expiry in Azure AD)

### Difference from Gmail setup
| Aspect | Gmail | Outlook |
|---|---|---|
| Auth library | `google-auth` / `google-auth-oauthlib` | `msal` |
| OAuth2 flow | Browser redirect (localhost:8080) | Device code flow |
| IMAP host | `imap.gmail.com` | `outlook.office365.com` |
| Scope | `https://mail.google.com/` | `IMAP.AccessAsUser.All` |
| Token storage | `tokens/gmail_token.json` | `tokens/outlook_token.json` |
| XOAUTH2 format | Identical (RFC standard) | Identical (RFC standard) |
