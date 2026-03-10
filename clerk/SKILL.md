---
name: clerk
description: Clerk authentication integration guide. Use when implementing user auth, setting up Clerk for a new project, configuring sign-in/sign-up flows, or handling auth-related security concerns. Covers agency workflow for client handover.
---

# Clerk Authentication Skill

Clerk is a developer-focused authentication platform with pre-built UI components and secure session handling. This skill covers implementation guidance, coding agent best practices, and our agency workflow for client projects.

## Agency Workflow: Client Project Setup

When setting up Clerk for a client project, follow this handover process:

### 1. Create a Dedicated Workspace

In your Clerk dashboard, create a **new workspace** specifically for the client project (e.g., "ClientName App"). Do NOT build in your personal or agency workspace.

### 2. Build & Configure

Set up the application within this workspace:
- Configure API keys
- Set up branding/theming
- Configure auth methods (email/password, social providers, etc.)
- Test the full auth flow in development mode

### 3. Invite Client as Admin

When ready for handover:
1. Go to workspace settings
2. Invite the client's email address with **Admin** role

### 4. Handover Message

Send to client:
> "I've set up a dedicated secure workspace for your user account system. Please accept the invite I just sent from Clerk.com to join as an Admin—this gives you full control to add your billing info and manage the account directly."

Once accepted, the client:
- Becomes co-owner
- Can add their credit card for billing
- Can optionally remove you from the workspace

---

## Pro Tips for Using Clerk Effectively

### Leverage Pre-Built Components

Use Clerk's drop-in components (`<SignIn/>`, `<SignUp/>`, `<UserProfile/>`) instead of building custom auth UI. Start with defaults, customize styling later.

### Use Official SDKs

Clerk provides SDKs for popular frameworks (Next.js, React, SvelteKit, Node). Use these instead of calling APIs directly—they handle token management and verification correctly.

### Separate Dev and Production

- **Development instance**: Relaxed security, all features available for testing
- **Production instance**: Must configure separately (custom domain, social auth keys)

Test fully in dev mode before going live. When deploying, follow Clerk's production checklist.

### Store Keys Securely

- **Publishable Key**: Safe for frontend (identifies your Clerk instance)
- **Secret Key**: Backend only, treat like a password, never in client code or version control

### Start Simple

1. Get basic auth working end-to-end first
2. Layer on customizations incrementally
3. Use Clerk's sensible defaults before overriding

---

## Coding Agent Best Practices

Clerk provides an official **Skills package** for AI coding agents:

```bash
npx skills add clerk/skills
```

This gives agents context about Clerk's APIs and patterns. Works with Claude Code, Cursor, Windsurf, and others.

### Guiding the Agent

Be explicit in prompts:
- Specify the framework (Next.js, SvelteKit, etc.)
- Clarify dev vs prod environment
- Instruct to use environment variables for secrets

**Good prompt example:**
> "Use the Clerk SDK to protect an Express.js API route (the user should be signed in). Use environment variables for the Clerk secret."

### Common Pitfalls to Watch

1. **Exposed Secret Key** - Agent puts secret key in frontend code
2. **Missing verification** - Auth check only on frontend, not backend API routes
3. **Wrong URLs** - Mismatched OAuth redirects or Clerk subdomains
4. **Hardcoded keys** - Keys in source instead of env vars

### Review Checklist for AI-Generated Code

- [ ] Publishable key on frontend, secret key on backend only
- [ ] Session verification on all protected API routes
- [ ] Environment variables for all keys
- [ ] URLs match your Clerk instance configuration
- [ ] Test full sign-up/sign-in flow manually

---

## CLI and Automation

### No General-Purpose CLI

Clerk doesn't provide a full CLI for production management. Use the **Dashboard UI** or **Backend API** for configuration.

### Backend API (BAPI) for Scripting

All admin actions available via REST API with your Secret Key:

```bash
# Example: List users
curl -H "Authorization: Bearer $CLERK_SECRET_KEY" \
  https://api.clerk.com/v1/users

# Example: Delete a user
curl -X DELETE -H "Authorization: Bearer $CLERK_SECRET_KEY" \
  https://api.clerk.com/v1/users/{user_id}
```

Use cases:
- Bulk user imports/migrations
- CI/CD integration for user management
- Custom admin tooling

### CLI Authentication for Your App

If building a CLI tool that needs Clerk auth:

1. CLI opens browser to your app's Clerk sign-in
2. User authenticates (can use any provider)
3. Backend generates JWT via Clerk JWT Template (configure for ~7 day expiry)
4. Redirect to CLI's local server with token
5. CLI stores token in OS keychain

See Clerk's JWT Templates documentation for setup.

---

## Security Responsibilities

### What Clerk Handles

- Password storage and hashing
- OAuth flow security
- Session token management (HttpOnly cookies, short-lived JWTs)
- Multi-factor authentication (if enabled)
- Brute-force protection
- SOC 2 Type II compliance

### What You Must Handle

**Authorization (not just authentication)**
- Clerk tells you WHO the user is
- You decide WHAT they can access
- Implement role/permission checks in your app

**API Route Protection**
- Use Clerk's middleware or `verifyToken()` on ALL protected endpoints
- Never rely only on frontend auth checks

**Secret Management**
- Keep Secret Key in env vars only
- Never log or expose in errors
- Verify webhook signatures

### Edge Cases to Watch

| Scenario | Risk | Mitigation |
|----------|------|------------|
| Social login email changes | Account conflicts | Handle email linking carefully |
| Default 7-day sessions | May be too long for sensitive apps | Configure shorter duration (requires Pro) |
| Clerk service outage | App auth fails | Add graceful error handling |
| User deletion requests | Data in your DB remains | Delete/anonymize associated data |

### Security Checklist

- [ ] All API routes verify Clerk session
- [ ] Secret key in env vars, not code
- [ ] Webhook signatures verified
- [ ] Authorization logic in place (not just auth)
- [ ] Session duration appropriate for your use case
- [ ] Error handling for Clerk outages

---

## Pricing Tiers

*Updated Feb 2026 — [changelog](https://clerk.com/changelog/2026-02-05-new-plans-more-value). Clerk now uses **MRU** (Monthly Retained Users) instead of MAU — a user counts as retained when they return 24+ hours after signing up.*

### Free (Hobby) Tier

| Feature | Limit |
|---------|-------|
| MRU per app | 50,000 |
| Applications | Unlimited |
| Dashboard seats | 3 |
| Social connections (Google, GitHub, etc.) | Up to 3 |
| Email/password, magic links, passkeys | Included |
| Pre-built UI components | Included |
| Custom domain | Included |
| User impersonations | 5/month |
| Session lifetime | Fixed at 7 days (not configurable) |
| SMS authentication | Not included |
| Clerk branding | Required (cannot remove) |

**Note**: Dormant accounts don't count toward MRU. All features testable in dev mode.

### Pro Tier ($25/month or $20/month annual)

| Feature | Detail |
|---------|--------|
| MRU included | 50,000 per app |
| MRU overage | $0.02/MRU (50k-100k), $0.018 (100k-1M), $0.015 (1M-10M), $0.012 (10M+) |
| Enterprise SSO connections | 1 included, $75/mo each additional |
| Dashboard seats | 3 |

**Pro unlocks (over Free):**
- Remove Clerk branding
- MFA (TOTP, SMS OTP, backup codes)
- Passkeys
- Satellite domains
- Simultaneous sessions
- Custom session duration
- Custom email/SMS templates
- User bans
- Allowlist/blocklist (restrict sign-ups by domain)

### Business Tier ($300/month or $250/month annual)

| Feature | Detail |
|---------|--------|
| Dashboard seats | 10 included, $20/mo each additional |
| Compliance | SOC 2 Report & HIPAA |
| Support | Priority email |

**Business unlocks (over Pro):**
- Enhanced dashboard roles
- Audit logs (coming soon)
- SOC 2 & HIPAA compliance artifacts

### Enterprise Tier (Custom, annual only)

- Committed use discounts
- 99.99% uptime SLA
- Premium support with dedicated Slack channel
- Onboarding/migration support

### Pricing Strategy

1. **Start on Free** — Very generous for development and early launch (50k MRU)
2. **Upgrade to Pro** — When you need MFA, branding removal, custom sessions, or enterprise SSO
3. **Upgrade to Business** — When you need 4+ dashboard seats or SOC 2/HIPAA compliance

**Watch out for**: Enterprise SSO (SAML/OIDC) is now metered in Pro ($75/mo per connection beyond the first). If you need 3+ connections, costs add up. Also, social connections are limited to 3 on Free.

---

## Quick Reference

### Environment Variables

```env
# Frontend (safe to expose)
PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...

# Backend (keep secret)
CLERK_SECRET_KEY=sk_test_...

# Optional: Webhook signing secret
CLERK_WEBHOOK_SECRET=whsec_...
```

### SvelteKit Integration

```bash
npm install svelte-clerk
```

**SvelteKit + FastAPI Gotchas:**
- SSO callbacks (Google, etc.) require catch-all routes: `/sign-in/[...rest]/+page.svelte`
- Use `useClerkContext()` with `$effect()` to wait for Clerk before API calls—`onMount` fires too early
- `fastapi-clerk-auth`: wrap `ClerkHTTPBearer` in an async function that invokes it with the request
- If using `@lru_cache` on settings, env var changes require server restart (CORS, JWKS URL, etc.)

### Next.js Integration

```bash
npm install @clerk/nextjs
```

Then wrap your app with `<ClerkProvider>` and use middleware for route protection.

### Useful Links

- [Clerk Dashboard](https://dashboard.clerk.com)
- [Official Docs](https://clerk.com/docs)
- [AI Skills](https://clerk.com/docs/guides/ai/skills)
- [Backend API Reference](https://clerk.com/docs/reference/backend-api)
- [JWT Templates](https://clerk.com/docs/backend-requests/jwt-templates)
