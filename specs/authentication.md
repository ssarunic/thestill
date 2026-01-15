# Authentication Specification for thestill

> **Status:** Draft
> **Created:** 2025-01-15
> **Author:** Product & Engineering

---

## Executive Summary

Implement a flexible, opt-in authentication system that maintains zero-friction for self-hosted users while enabling multi-tenant capabilities for a hosted version. The architecture should be "auth-aware" from the start but never require it.

---

## Table of Contents

1. [Product Vision](#product-vision)
2. [Capabilities & Outcomes](#capabilities--outcomes)
3. [Technical Architecture](#technical-architecture)
4. [Implementation Phases](#implementation-phases)
5. [API Design](#api-design)
6. [Security Considerations](#security-considerations)
7. [Migration Strategy](#migration-strategy)
8. [Success Metrics](#success-metrics)
9. [Risks & Mitigations](#risks--mitigations)
10. [Dependencies](#dependencies)
11. [Open Questions](#open-questions)

---

## Product Vision

### User Personas

| Persona | Use Case | Auth Needs |
|---------|----------|------------|
| **Self-hoster** | Runs locally on their machine | None - single implicit user |
| **Power user** | Runs on home server/NAS | Optional - maybe basic protection |
| **Hosted user** | Uses thestill.me | Required - Google/Microsoft SSO |
| **Team user** | Shared instance | Required - multi-user with isolation |

### Core Principles

1. **Zero friction by default** - `AUTH_MODE=none` works out of the box
2. **Progressive complexity** - Add auth only when needed
3. **Data isolation** - When auth is on, users see only their data
4. **No vendor lock-in** - All auth logic is self-hosted, standard OAuth

---

## Capabilities & Outcomes

### Phase 1: Auth-Aware Foundation

**Outcome:** Codebase is ready for multi-tenancy without breaking existing functionality

| Capability | User Outcome | Technical Outcome |
|------------|--------------|-------------------|
| User context in all requests | N/A (invisible) | Every request has a `user_id` (real or synthetic) |
| Data model supports ownership | N/A (invisible) | `user_id` column on podcasts table |
| Auth bypass mode | Self-hosters see no change | `AUTH_MODE=none` returns synthetic user |

**Success Criteria:**

- All existing CLI commands work unchanged
- All existing API endpoints work unchanged
- No user-visible changes in `AUTH_MODE=none`

---

### Phase 2: Local Auth (Optional Basic Protection)

**Outcome:** Power users can password-protect their instance

| Capability | User Outcome | Technical Outcome |
|------------|--------------|-------------------|
| Single-user password | Protect instance with password | Local bcrypt password in config/DB |
| Session persistence | Stay logged in | JWT with configurable expiry |
| Login page | Simple password form | Minimal React component |

**Success Criteria:**

- `AUTH_MODE=local` requires password to access
- Password set via CLI: `thestill auth set-password`
- Session survives browser restart

---

### Phase 3: OAuth SSO (Hosted Version)

**Outcome:** Users can sign in with Google/Microsoft, full multi-tenancy

| Capability | User Outcome | Technical Outcome |
|------------|--------------|-------------------|
| Google login | "Sign in with Google" button | fastapi-sso + Google OAuth |
| Microsoft login | "Sign in with Microsoft" button | fastapi-sso + Microsoft OAuth |
| User registration | Automatic on first login | Create user record from OAuth profile |
| Data isolation | See only my podcasts | All queries filtered by `user_id` |
| User settings | Manage my account | Settings page with logout |

**Success Criteria:**

- New user can sign up and add a podcast in <60 seconds
- Users cannot see each other's data
- OAuth secrets configurable via environment

---

### Phase 4: Enhanced Features (Future)

| Capability | User Outcome | Technical Outcome |
|------------|--------------|-------------------|
| Magic link login | Passwordless email login | Email service integration |
| API keys | Programmatic access | Per-user API key generation |
| Usage quotas | Fair use on hosted version | Rate limiting per user |
| Team workspaces | Share podcasts with team | Workspace model + invitations |

---

## Technical Architecture

### Auth Modes

```
┌─────────────────────────────────────────────────────────────┐
│                      AUTH_MODE                              │
├─────────────┬─────────────────┬─────────────────────────────┤
│    none     │     local       │           oauth             │
├─────────────┼─────────────────┼─────────────────────────────┤
│ No login    │ Password gate   │ Google/Microsoft SSO        │
│ Single user │ Single user     │ Multi-tenant                │
│ No UI       │ Simple login UI │ Full auth UI                │
│ Default     │ Opt-in          │ Opt-in                      │
└─────────────┴─────────────────┴─────────────────────────────┘
```

### Configuration

```bash
# .env

# Auth mode: none | local | oauth
AUTH_MODE=none

# For AUTH_MODE=local
# Set via: thestill auth set-password
LOCAL_PASSWORD_HASH=

# For AUTH_MODE=oauth
OAUTH_GOOGLE_CLIENT_ID=
OAUTH_GOOGLE_CLIENT_SECRET=
OAUTH_MICROSOFT_CLIENT_ID=
OAUTH_MICROSOFT_CLIENT_SECRET=

# JWT settings (all modes except none)
JWT_SECRET=auto-generated-on-first-run
JWT_EXPIRY_DAYS=7
```

### Data Model Changes

```sql
-- New users table
CREATE TABLE users (
    id TEXT PRIMARY KEY,           -- UUID
    email TEXT UNIQUE,             -- From OAuth or local
    name TEXT,                     -- Display name
    provider TEXT,                 -- 'local', 'google', 'microsoft'
    provider_id TEXT,              -- OAuth provider's user ID
    created_at TIMESTAMP,
    last_login_at TIMESTAMP
);

-- Add to existing podcasts table
ALTER TABLE podcasts ADD COLUMN user_id TEXT REFERENCES users(id);

-- Index for query performance
CREATE INDEX idx_podcasts_user_id ON podcasts(user_id);
```

### Code Structure

```
thestill/web/
├── auth/
│   ├── __init__.py
│   ├── models.py              # User model, session model
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py            # AuthProvider ABC
│   │   ├── none.py            # NoAuthProvider (synthetic user)
│   │   ├── local.py           # LocalAuthProvider (password)
│   │   └── oauth.py           # OAuthProvider (Google, Microsoft)
│   ├── dependencies.py        # get_current_user() FastAPI dependency
│   ├── routes.py              # /auth/* endpoints
│   └── jwt.py                 # Token creation/verification
├── frontend/
│   └── src/
│       ├── components/
│       │   ├── LoginPage.tsx
│       │   ├── OAuthButtons.tsx
│       │   └── UserMenu.tsx
│       └── hooks/
│           └── useAuth.ts
```

### Request Flow

```
           ┌──────────────────────────────────────────────────┐
           │                   Request                        │
           └──────────────────────┬───────────────────────────┘
                                  │
                                  ▼
           ┌──────────────────────────────────────────────────┐
           │            get_current_user()                    │
           │         (FastAPI Dependency)                     │
           └──────────────────────┬───────────────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
     ┌────────────────┐  ┌────────────────┐  ┌────────────────┐
     │  AUTH_MODE=    │  │  AUTH_MODE=    │  │  AUTH_MODE=    │
     │     none       │  │     local      │  │     oauth      │
     ├────────────────┤  ├────────────────┤  ├────────────────┤
     │ Return         │  │ Verify JWT     │  │ Verify JWT     │
     │ synthetic user │  │ from cookie    │  │ from cookie    │
     │ id="local"     │  │ Single user    │  │ Multi-tenant   │
     └───────┬────────┘  └───────┬────────┘  └───────┬────────┘
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  │
                                  ▼
           ┌──────────────────────────────────────────────────┐
           │              User object                         │
           │  { id, email, name }                             │
           └──────────────────────┬───────────────────────────┘
                                  │
                                  ▼
           ┌──────────────────────────────────────────────────┐
           │         Route handler                            │
           │  Queries filtered by user.id                     │
           └──────────────────────────────────────────────────┘
```

---

## Implementation Phases

### Phase 1: Auth-Aware Foundation

**Estimated effort:** ~1 week

**Tasks:**

1. Add `users` table to SQLite schema
2. Add `user_id` to podcasts table with migration
3. Create `AuthProvider` abstraction and `NoAuthProvider`
4. Add `get_current_user()` dependency that returns synthetic user
5. Update repository methods to accept `user_id` parameter
6. Update all API routes to use `get_current_user()`
7. Backfill existing podcasts with `user_id="local"`

**Deliverable:** All tests pass, no functional changes visible

---

### Phase 2: Local Auth

**Estimated effort:** ~1 week

**Tasks:**

1. Implement `LocalAuthProvider` with password verification
2. Add `thestill auth set-password` CLI command
3. Create JWT utilities (create, verify, refresh)
4. Add `/auth/login` and `/auth/logout` endpoints
5. Create minimal login page component
6. Add auth middleware for protected routes
7. Store JWT in httpOnly cookie

**Deliverable:** `AUTH_MODE=local` protects instance with password

---

### Phase 3: OAuth SSO

**Estimated effort:** ~2 weeks

**Tasks:**

1. Install and configure `fastapi-sso`
2. Implement `OAuthProvider` for Google
3. Implement `OAuthProvider` for Microsoft
4. Create OAuth callback handlers
5. Implement user creation on first login
6. Build login page with OAuth buttons
7. Add user menu with logout
8. Update all queries to filter by `user_id`
9. Add settings page for user profile

**Deliverable:** Full multi-tenant hosted version ready

---

## API Design

### Auth Endpoints

```
# All modes
GET  /auth/me                    → Current user info (or 401)

# AUTH_MODE=local
POST /auth/login                 → { password } → Set JWT cookie
POST /auth/logout                → Clear JWT cookie

# AUTH_MODE=oauth
GET  /auth/google/login          → Redirect to Google
GET  /auth/google/callback       → Handle OAuth, set JWT cookie
GET  /auth/microsoft/login       → Redirect to Microsoft
GET  /auth/microsoft/callback    → Handle OAuth, set JWT cookie
POST /auth/logout                → Clear JWT cookie
```

### Protected Endpoints (Unchanged URLs)

All existing endpoints remain the same but now respect user context:

```
GET  /api/podcasts               → Returns only current user's podcasts
POST /api/podcasts               → Creates podcast owned by current user
GET  /api/podcasts/{slug}        → 404 if not owned by current user
```

---

## Security Considerations

| Concern | Mitigation |
|---------|------------|
| JWT secret exposure | Auto-generate on first run, store in `.env` |
| CSRF attacks | SameSite=Strict cookies, state parameter in OAuth |
| Session hijacking | httpOnly cookies, short expiry, HTTPS in production |
| OAuth token leakage | Never store OAuth tokens, only use for initial auth |
| Password brute force | Rate limiting on login endpoint |
| Data leakage | All queries include `user_id` filter |

---

## Migration Strategy

### For Existing Self-Hosted Users

```bash
# Before upgrade: podcasts have no user_id
# After upgrade: automatic migration adds user_id="local"

thestill upgrade  # Runs migration automatically
# Output: "Migrated 15 podcasts to local user"
```

### For New Installations

```bash
# Fresh install defaults to AUTH_MODE=none
thestill status
# Output: "Auth: disabled (single-user mode)"

# Enable OAuth
export AUTH_MODE=oauth
export OAUTH_GOOGLE_CLIENT_ID=...
thestill server
# Output: "Auth: OAuth (Google, Microsoft)"
```

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| Self-hoster friction | Zero | No config required for AUTH_MODE=none |
| Time to first login (OAuth) | <30 seconds | From landing page to dashboard |
| Auth code coverage | >90% | Unit tests on all providers |
| Security audit | Pass | No critical vulnerabilities |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| OAuth provider changes API | Low | Medium | Use fastapi-sso (maintained library) |
| JWT secret rotation needed | Medium | Low | Add `thestill auth rotate-secret` command |
| Multi-tenant query bugs | Medium | High | Integration tests with multiple users |
| Migration breaks existing data | Low | High | Backup before migration, dry-run mode |

---

## Dependencies

```toml
# pyproject.toml additions
[project.optional-dependencies]
auth = [
    "fastapi-sso>=0.9.0",      # OAuth providers
    "python-jose[cryptography]>=3.3.0",  # JWT
    "passlib[bcrypt]>=1.7.4",  # Password hashing
]
```

---

## Open Questions

1. **Email verification for OAuth users?** - Probably not needed since Google/Microsoft verify emails
2. **Account deletion (GDPR)?** - Add `thestill auth delete-account` command?
3. **Session invalidation on password change?** - Yes, rotate JWT secret
4. **API keys for CLI access?** - Defer to Phase 4

---

## Appendix: Library Comparison

### Auth Service Pricing (if using external service)

| Users (MAU) | Clerk | Supabase Auth | Auth0 |
|-------------|-------|---------------|-------|
| 100 | Free | Free | Free |
| 1,000 | Free | Free | Free |
| 10,000 | Free | Free | Free |
| 50,000 | $825/mo | Free | ~$2,000+/mo |
| 100,000 | $1,825/mo | $25/mo | Enterprise |

### Python Libraries for Self-Hosted Auth

| Library | Complexity | Best For |
|---------|------------|----------|
| **fastapi-sso** | Low | Just OAuth buttons, minimal code |
| **Authlib** | Medium | More control, multiple providers |
| **fastapi-users** | High | Full user management + OAuth |

**Recommendation:** `fastapi-sso` + `python-jose` for OAuth, self-hosted in your FastAPI app.

---

## Revision History

| Date | Version | Changes |
|------|---------|---------|
| 2025-01-15 | 0.1 | Initial draft |
