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
cookie values. In a separate tab, open `https://aspen.darienps.org/aspen/logon.do`,
choose **Students and Staff Login**, and sign in with your school Google account.
Open browser developer tools, then Application → Cookies in Chrome or
Storage → Cookies in Firefox, and select `aspen.darienps.org`.
Copy the **value** of `JSESSIONID` with path `/app` and `VITHAR_CSRF` into
the required fields. Chrome may show only cookies applicable to the current
page's path. If only the `/aspen` session appears, open
`https://aspen.darienps.org/app/api/index.html` after signing in and inspect
cookies in that tab to find `/app`. The API page is not a sign-in flow; if
BetterAspen rejects the session, sign in again through **Students and Staff
Login** using the sign-in link above.
Open Aspen's desktop portal and copy the separate
`JSESSIONID` with path `/aspen` to enable desktop averages, attendance, and
recent activity. If `cf_clearance` is present, enter its value too.
Click **Save and connect**. If Aspen goes down or denies access, use **Retry saved
session** in Connection (or Refresh) to try the saved cookies again. Your last
successful data stays available if the retry fails. Re-enter fresh values if
Aspen continues to reject the session.

Current grades and recent activity refresh every minute. Previous-year and
individual-quarter views are cached for up to an hour; their last-updated time
appears beside the grade-period controls. **Refresh** and **Retry saved session**
force a complete update of every period. Every downloaded period stays available
for instant switching while a refresh runs or Aspen is unavailable.

Dashboard polling downloads records only when the account's snapshot revision
changes. Saved snapshots and browser downloads share repeated courses and
assignments losslessly; existing snapshot files upgrade automatically on their
next successful save. Versioned static assets are cached; private API responses
remain uncached.

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

Use **Enable notifications** beside Recent activity and allow the permission
prompt. With Web Push support, the server sends new or changed activity during
its background Aspen refresh, even with every dashboard tab closed. Existing
enabled browsers with granted permission register for push on their next visit.
Browsers without Web Push show a message explaining that alerts require an open
page. HTTPS is required outside localhost.

On iPhone or iPad, use iOS/iPadOS 16.4 or later, add BetterAspen to the Home Screen,
then open that app and tap **Enable notifications**. If an existing shortcut opens
in Safari, remove that shortcut and add it again after this update. Allow alerts
and badges in the app's iOS notification settings. Supported installed apps show
the number of unread activity entries on their icon; viewing Home clears that
device's badge. Desktop delivery requires browser/OS notifications to be enabled;
a fully quit browser may defer delivery until it runs again.

Preferences remain per account/student/browser. **Disable notifications** removes
that device's subscription. Sign-out revokes subscriptions for that browser login;
other logged-in devices stay subscribed. Login expiry (30 days) also stops delivery
until you sign in again. Push requires a valid saved Aspen session and recent
activity access; reconnect if Aspen expires your cookies. Clicking an alert opens
Recent activity and clears search/type filters. The first feed establishes a quiet
baseline. Repeat checks, reloads, and server restarts do not replay seen activity.
Sample data and failed or stale feeds do not generate alerts. Temporary push
failures retry on the next successful refresh; expired subscriptions are removed.
Push delivery runs in a separate worker after sync saves its results, so slow
notification services do not block connection controls. Pending deliveries remain
in the account database and resume after a server restart.

Install the updated requirements and restart Gunicorn to deploy notification
changes. VAPID keys are generated automatically in `.state/push-vapid.pem`; keep
this file and the account database across restarts so existing subscriptions work.
No paid push service or manually configured VAPID keys are needed. The configured
public URL identifies the sender to browser push services. Notifications contain
only an activity count, with no student name, grades, or credentials.

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
with Fast mode (`service_tier=fast`) and low reasoning effort. The configured
provider must support Fast mode; OpenAI bills it at a premium over Standard processing.
The key, endpoint, and whitelist stay on the server.
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
