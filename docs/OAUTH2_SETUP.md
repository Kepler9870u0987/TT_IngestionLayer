# OAuth2 Setup Guide - Google Cloud Console

This guide walks you through setting up OAuth2 credentials for Gmail IMAP access.

## Prerequisites

- Google account
- Access to [Google Cloud Console](https://console.cloud.google.com/)

---

## Step 1: Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Enter project name: `Email Ingestion` (or your preferred name)
4. Click "Create"
5. Wait for project creation to complete

---

## Step 2: Enable Gmail API

1. With your project selected, go to "APIs & Services" → "Library"
2. Search for "Gmail API"
3. Click on "Gmail API"
4. Click "Enable"
5. Wait for API to be enabled

---

## Step 3: Configure OAuth Consent Screen

1. Go to "APIs & Services" → "OAuth consent screen"
2. Select User Type:
   - **External**: For personal Gmail accounts (recommended for development)
   - **Internal**: Only if using Google Workspace domain
3. Click "Create"

### Configure App Information:

- **App name**: `Email Ingestion System`
- **User support email**: Your email address
- **Developer contact information**: Your email address
- Leave other fields as default

4. Click "Save and Continue"

### Scopes:

5. Click "Add or Remove Scopes"
6. Filter for "Gmail API"
7. Select: `https://mail.google.com/` (Full Gmail access)
8. Click "Update" → "Save and Continue"

### Test Users (for External type):

9. Click "Add Users"
10. Enter your Gmail address
11. Click "Add" → "Save and Continue"

12. Click "Back to Dashboard"

---

## Step 4: Create OAuth2 Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. Application type: **Desktop app**
4. Name: `Email Producer`
5. Click "Create"

### Download Credentials:

6. A dialog appears with your credentials:
   - **Client ID**: `xxxxx.apps.googleusercontent.com`
   - **Client secret**: `GOCSPX-xxxxx`
7. **Copy these values** - you'll need them for `.env` file
8. Click "OK"

---

## Step 5: Configure Local Environment

1. Open your `.env` file (copy from `.env.example` if needed):

```bash
cp .env.example .env
```

2. Edit `.env` and add your OAuth2 credentials:

```bash
# OAuth2 Google Configuration
GOOGLE_CLIENT_ID=your-client-id-here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your-client-secret-here
GOOGLE_REDIRECT_URI=http://localhost:8080
GOOGLE_TOKEN_FILE=tokens/gmail_token.json
```

3. Add your Gmail address:

```bash
IMAP_USER=your-email@gmail.com
```

---

## Step 6: Authenticate

Run the OAuth2 setup command:

```bash
python producer.py --auth-setup
```

**What happens:**

1. A browser window opens
2. You're asked to sign in to your Google account
3. Grant permission to the app
4. You'll see "Authentication successful!"
5. Token is saved to `tokens/gmail_token.json`

**Browser Output:**
```
The authentication flow has completed. You may close this window.
```

**Terminal Output:**
```
✓ OAuth2 setup complete!
Token saved to: tokens/gmail_token.json
```

---

## Step 7: Verify Setup

Check token information:

```bash
python -m src.auth.oauth2_gmail --info
```

**Expected Output:**
```json
{
  "status": "valid",
  "has_token": true,
  "has_refresh_token": true,
  "scopes": ["https://mail.google.com/"],
  "expiry": "2026-02-16T16:30:00Z",
  "expires_in_seconds": 3600
}
```

---

## Troubleshooting

### "Access blocked: This app's request is invalid"

**Cause**: OAuth consent screen not properly configured

**Solution**:
1. Go back to "OAuth consent screen"
2. Ensure "Publishing status" is "Testing"
3. Add your email to "Test users"

---

### "invalid_grant" error

**Cause**: Token expired or revoked

**Solution**:
```bash
# Revoke old token
python -m src.auth.oauth2_gmail --revoke

# Re-authenticate
python producer.py --auth-setup
```

---

### "Permission denied" on token file

**Cause**: Token directory doesn't exist or has wrong permissions

**Solution**:
```bash
mkdir -p tokens
chmod 700 tokens
```

---

## Token Refresh

Tokens automatically refresh when expired. The system will:

1. Detect token expiration (with 5-minute buffer)
2. Use refresh_token to get new access_token
3. Save updated token to file
4. Continue operation seamlessly

**Manual refresh:**
```bash
python -m src.auth.oauth2_gmail --refresh
```

---

## Security Best Practices

1. **Never commit `.env` or `tokens/` to git**
   - Already in `.gitignore`

2. **Keep client_secret private**
   - Don't share in public repositories

3. **Rotate credentials periodically**
   - Delete old credentials in Google Cloud Console
   - Create new ones every 90 days (recommended)

4. **Use environment-specific credentials**
   - Different credentials for dev/staging/production

5. **Monitor OAuth consent screen**
   - Check "APIs & Services" → "OAuth consent screen" → "View app details"
   - Review granted permissions regularly

---

## Production Considerations

For production deployment:

1. **Move to Google Workspace** (if possible):
   - Internal OAuth app (no verification needed)
   - Better quotas and support

2. **Or complete OAuth verification** (for External apps):
   - Required for >100 users
   - Process takes 4-6 weeks
   - https://support.google.com/cloud/answer/9110914

3. **Use secrets management**:
   - HashiCorp Vault
   - AWS Secrets Manager
   - Azure Key Vault

4. **Implement token encryption at rest**:
   - Encrypt `tokens/gmail_token.json`
   - Use OS-level encryption or dedicated key management

---

## Additional Resources

- [Google OAuth2 Documentation](https://developers.google.com/identity/protocols/oauth2)
- [Gmail API Guides](https://developers.google.com/gmail/api/guides)
- [OAuth 2.0 Scopes for Google APIs](https://developers.google.com/identity/protocols/oauth2/scopes#gmail)
- [Best Practices for OAuth2](https://developers.google.com/identity/protocols/oauth2/production-ready)

---

## Quick Reference

| Task | Command |
|------|---------|
| Initial setup | `python producer.py --auth-setup` |
| Check token status | `python -m src.auth.oauth2_gmail --info` |
| Refresh token | `python -m src.auth.oauth2_gmail --refresh` |
| Revoke token | `python -m src.auth.oauth2_gmail --revoke` |
| Start producer | `python producer.py` |

---

**Setup Complete!** You're now ready to run the email producer. 🎉
