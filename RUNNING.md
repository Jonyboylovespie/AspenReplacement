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
cookie values. In a separate tab, sign into `https://aspen.darienps.org/app/`.
Open browser developer tools, then Application → Cookies in Chrome or
Storage → Cookies in Firefox, and select `aspen.darienps.org`.
Copy the **value** of `JSESSIONID` with path `/app` and `VITHAR_CSRF` into
the required fields. Open Aspen's desktop portal and copy the separate
`JSESSIONID` with path `/aspen` to enable desktop averages, attendance, and
recent activity. If `cf_clearance` is present, enter its value too.
Click **Save and connect**. Re-enter fresh values when Aspen expires the session.

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

Verification:

```sh
.venv/bin/python -m unittest discover -s tests
node --test tests/*.js
```
