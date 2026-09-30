Run the server with:

```sh
.venv/bin/pip install -r requirements.txt
.venv/bin/gunicorn -c gunicorn.conf.py 'app:create_app()'
```

Gunicorn binds to `0.0.0.0:5173` and runs one process with eight threads. Keep
one worker: account stores and background refresh live in that process. Refresh
starts automatically, including after a worker restart. No systemd service is
included.

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and `BETTERASSPEN_PUBLIC_URL` in
the repository's `.env` or the server environment. Gunicorn and the app load
`.env` automatically; explicitly exported environment variables take priority.
Create a Google OAuth **web application** client with
`BETTERASSPEN_PUBLIC_URL/auth/google/callback` as an authorized redirect URI.
Use the actual HTTPS address you will visit. `.env.example` lists the settings.
For this instance, register
`https://aspen.jonyserver.ddnsfree.com/auth/google/callback` as the redirect URI
and `https://aspen.jonyserver.ddnsfree.com` as the JavaScript origin.
If your Google consent screen is
in testing mode, add the school Google accounts as test users.

If HTTPS terminates at one trusted proxy, set `BETTERASSPEN_PROXY_HOPS=1` and
configure that proxy to preserve Host and forward the protocol. Otherwise leave
it unset. `BETTERASSPEN_BIND` can override the listening address/port.

Install BetterASSpen Connect once in each browser, open its options, and save
this same BetterASSpen address. Chromium: load `extension/chromium` as an
unpacked extension using the browser's extension developer mode. Firefox/Zen:
load `extension/firefox/manifest.json` using `about:debugging` for testing;
normal Firefox installations require an AMO-signed extension for installation
that survives browser restarts. The Firefox bundle is not signed or published.

Then sign in with your school Google account on BetterASSpen. The extension
connects an existing Aspen session automatically, or opens school sign-in if
needed. It reads only Aspen login cookies and sends them only to your configured
BetterASSpen origin. It never reads Google cookies. Browser extensions are
needed only to connect/reconnect Aspen; after connecting, another device can
view the same account's grades by signing into Google.

Each Google account has separate saved grades and an encrypted Aspen session.
The server matches Aspen's authenticated email to Google's verified email and
binds that student to the account. If the current-user response uses a different
email field, configure its exact path with `BETTERASSPEN_ASPEN_EMAIL_FIELD`
(e.g. `user.emailAddress` or `student.schoolEmail` after confirming that field).
Connection fails closed if it cannot verify the identity.

Keep the state directory (default `.state`, configurable with
`BETTERASSPEN_STATE_DIR`) across restarts; it contains the account database,
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
BETTERASSPEN_EXTENSION_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_extension_browser.py
```

The extension browser checks use installed Playwright Chromium and local
fixtures; they never sign into Google or Aspen. A real school-login check still
requires your OAuth configuration and an active school account.
