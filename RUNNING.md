Run the server with:

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/gunicorn -c gunicorn.conf.py 'app:create_app()'
```

Gunicorn binds to `0.0.0.0:5173` and runs one process with eight threads. Keep
one worker: account stores and background refresh live in that process. Refresh
starts automatically, including after a worker restart. No systemd service is
included.

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `BETTERASPEN_PUBLIC_URL` in
the repository's `.env` or the server environment. Gunicorn and the app load
`.env` automatically; explicitly exported environment variables take priority.
Create a Google OAuth **web application** client with
`BETTERASPEN_PUBLIC_URL/auth/google/callback` as an authorized redirect URI.
Use the actual HTTPS address you will visit. `.env.example` lists the settings.
For this instance, register
`https://aspen.jonyserver.ddnsfree.com/auth/google/callback` as the redirect URI
and `https://aspen.jonyserver.ddnsfree.com` as the JavaScript origin.
If your Google consent screen is
in testing mode, add the school Google accounts as test users.

If HTTPS terminates at one trusted proxy, set `BETTERASPEN_PROXY_HOPS=1` and
configure that proxy to preserve Host and forward the protocol. Otherwise leave
it unset. `BETTERASPEN_BIND` can override the listening address/port.

Sign in with Google on BetterAspen, open Connection, and enter your Aspen
cookie values. In a separate tab, sign into `https://aspen.darienps.org/`.
Open browser developer tools, then Application → Cookies in Chrome or
Storage → Cookies in Firefox, and select `aspen.darienps.org`.
Copy the **value** of `JSESSIONID` with path `/app` and `VITHAR_CSRF` into
the required fields. Open Aspen's desktop portal and copy the separate
`JSESSIONID` with path `/aspen` to enable desktop averages, attendance, and
recent activity. If `cf_clearance` is present, enter its value too.
Click **Save and connect**. If Aspen goes down or denies access, use **Retry saved
session** in Connection (or Refresh) to try the saved cookies again. Your last
successful data stays available if the retry fails. Re-enter fresh values if
Aspen continues to reject the session.

Each Google account has separate saved grades and an encrypted Aspen session.
The server verifies the authenticated Aspen student before saving cookies.
The first successful connection pairs that student with the Google account;
the Google and school email addresses do not need to match. A paired account
cannot switch to a different student, and a student cannot be paired with
another Google account. Another device can view the same account's grades
by signing into Google after the initial connection.

Keep the state directory (default `.state`, configurable with
`BETTERASPEN_STATE_DIR`) across restarts; it contains the account database,
saved grades, encryption key, and login-signing secret. Existing prototype data
is left in place but is not exposed or assigned to any Google account. Account
login lasts up to 30 days; Google and Aspen can still require reauthentication.
Disconnect deletes that account's saved Aspen session. Clear deletes its saved
grades too. Google sign-out revokes that browser's access while the account's
background refresh continues.

Use **Enable notifications** beside Recent activity and allow the browser
permission prompt to receive browser notifications for new or changed entries
while the dashboard is open. This setting is saved per account and student in
that browser; **Disable notifications** turns
browser alerts off. Browser notifications require HTTPS (or localhost) and browser
support. Clicking an alert opens Recent activity and clears its search/type filters.
The first feed load establishes a quiet baseline; subsequent checks do not repeat
alerts for entries already seen, including after reload. Sample data and failed or
stale activity responses do not generate alerts. Keep a tab open to receive updates;
this does not deliver push notifications after all site tabs are closed.

Verification:

AI chat is available only to accounts explicitly listed in
`BETTERASPEN_AI_WHITELIST`, as comma-separated verified Google email addresses.
An empty list grants nobody access. For example:

```dotenv
BETTERASPEN_AI_API_ENDPOINT=https://api.openai.com/v1/responses
BETTERASPEN_AI_API_KEY=your-server-api-key
BETTERASPEN_AI_WHITELIST=alice@school.example,bob@school.example
```

The endpoint is the full URL of an OpenAI Responses API compatible service;
HTTPS is required except for localhost development. Requests use `gpt-6.1-sol`
with low reasoning effort. The key, endpoint, and whitelist stay on the server.
Restart Gunicorn after changing these settings. Until a key and whitelist are
configured, nobody sees the chat button. The server checks access on every chat
request, independently of button visibility, and sends only that account's saved
academic records. No Google or Aspen credentials are sent to the AI service.
AI requests set `store=false`; the configured provider's own retention policies
still apply. Conversations stay in the current browser tab and are cleared on
reload, sign-out, or a change of student/data mode. New chat clears them manually.
The assistant receives all saved school years and quarters, assignments,
attendance records, and activity entries, regardless of the dashboard's selected
grade period. Records that Aspen has not supplied are unavailable to the assistant.
Requests use a lossless compact JSON format: repeated objects and long text are
stored once and referenced, while tables declare their columns once and reuse
common field values. All descriptions, dates, scores, flags, event occurrences,
and differences between period views are preserved; no records are summarized
or truncated. The model receives instructions for reading that format. Small
payloads keep ordinary JSON if encoding overhead would make them larger.
The 300,000-character guard applies to the compact records, rather than the
uncompressed records. Stable academic data comes before changing sync metadata
to improve reuse of unchanged prompt prefixes where the provider supports caching.
One reply at a time per account is allowed,
with at least three seconds between requests.

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.js
```
