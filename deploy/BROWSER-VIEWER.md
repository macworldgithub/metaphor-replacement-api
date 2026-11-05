# Production browser viewer

These files target the supplied Linux service running as cranbourne in
/var/www/cranbourne/current/automation. Review paths against the server before installation.
They require Debian/Ubuntu package paths and an existing HTTPS Nginx virtual host.

Deploy the updated automation files to the active release at
`/var/www/cranbourne/current/automation` first. Pulling a separate checkout does not
update the directory used by the running service. Keep the existing credentials in
`/etc/cranbourne/automation.env`; this service does not use the checkout's `.env` as
its primary environment file. Keep `DEALERSOCKET_PROFILE_DIR` on a persistent path
under `/var/lib/cranbourne`, owned by `cranbourne`, so login survives releases and
the service can write it under its existing filesystem restrictions. Preserve the
existing profile rather than copying a Windows profile or deleting saved sessions.

1. Install packages with `sudo apt-get install xvfb x11vnc novnc websockify apache2-utils`.
2. Create the separate viewer password with `sudo htpasswd -c /etc/nginx/cranbourne-viewer.htpasswd viewer`.
   Run without `-c` when updating an existing password file. Restrict this credential to trusted admins.
3. Copy cranbourne-display.service, cranbourne-viewer.service and cranbourne-novnc.service
   from this directory into /etc/systemd/system/.
4. Put cranbourne-automation-viewer.override.conf at
   /etc/systemd/system/cranbourne-automation.service.d/viewer.conf (create the directory first).
   This replaces xvfb-run with the same Python runner on the fixed display :99.
5. Add `DEALERSOCKET_BROWSER_VIEWER_ENABLED=true`, `DEALERSOCKET_AUTO_LOGIN_ENABLED=true`
   and `DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED=true` to /etc/cranbourne/automation.env.
6. Include the contents of browser-viewer.nginx.conf in the existing automation domain's
   HTTPS server block. Validate with `sudo nginx -t` before reloading Nginx.
7. Run `sudo systemctl daemon-reload`, then
   `sudo systemctl enable --now cranbourne-display cranbourne-viewer cranbourne-novnc`,
   and `sudo systemctl restart cranbourne-automation`.
8. In Vercel set the server-only variable
   `DEALERSOCKET_VIEWER_URL=https://cranbourne-automation.omnisuiteai.com/browser/vnc.html?autoconnect=1&resize=scale&path=browser/websockify`
   and redeploy the frontend.

Commands for steps 3, 4 and installing the Nginx snippet, from the deployed release:

```bash
cd /var/www/cranbourne/current/automation
sudo install -m 644 deploy/cranbourne-display.service deploy/cranbourne-viewer.service deploy/cranbourne-novnc.service /etc/systemd/system/
sudo install -d /etc/systemd/system/cranbourne-automation.service.d
sudo install -m 644 deploy/cranbourne-automation-viewer.override.conf /etc/systemd/system/cranbourne-automation.service.d/viewer.conf
sudo install -d /etc/nginx/snippets
sudo install -m 644 deploy/browser-viewer.nginx.conf /etc/nginx/snippets/cranbourne-browser.conf
```

Inside the existing **HTTPS server block** for
`cranbourne-automation.omnisuiteai.com`, add:

```nginx
include /etc/nginx/snippets/cranbourne-browser.conf;
```

Do not replace the existing API proxy location or install this as a separate
competing virtual host. After editing the environment and Nginx configuration:

```bash
sudo nginx -t
# Continue only if the configuration test succeeds.
sudo systemctl reload nginx
sudo systemctl daemon-reload
sudo systemctl enable --now cranbourne-display cranbourne-viewer cranbourne-novnc
sudo systemctl restart cranbourne-automation
sudo systemctl --no-pager --full status cranbourne-display cranbourne-viewer cranbourne-novnc cranbourne-automation
```

The viewer password is separate from both the portal and DealerSocket passwords.
The portal redirect checks Super Admin access, while Nginx independently protects
direct viewer access. Only share the viewer password with authorized Super Admins.

Only expose /browser/ through authenticated HTTPS. Keep ports 5901 and 6080 on loopback.
The VNC loopback connection has no separate password because Nginx authenticates all public
viewer and WebSocket requests. Other accounts on the server must be trusted.
The viewer gives interactive control of the automation desktop and its CRM session.

Verification: an unauthenticated /browser/ request must return 401, including
/browser/websockify. After authenticating, press manual login in the portal and confirm
Chrome appears in the new tab. Complete login and confirm the connector reports connected.
Then test automatic login from a signed-out session. Do not run scheduled lead reads while
manually interacting with the desktop. Existing retry limits still apply.

If the pop-up is blocked or closed, **View server browser** reopens it without
starting another login. An empty desktop before Chrome launches is expected;
the viewer itself does not launch Chrome. A viewer connection error requires
checking the display/VNC services, not changing the DealerSocket password.

Useful diagnostics (review logs before sharing; never share credentials):

```bash
curl -I https://cranbourne-automation.omnisuiteai.com/browser/vnc.html
curl -I https://cranbourne-automation.omnisuiteai.com/browser/websockify
sudo ss -lntp '( sport = :5901 or sport = :6080 )'
sudo journalctl -u cranbourne-display -u cranbourne-viewer -u cranbourne-novnc -u cranbourne-automation -n 100 --no-pager
```

Both curl requests must return 401 without credentials, and the listening ports
must be loopback only. Verify fresh lead timestamps after login, then restart
`cranbourne-automation` and check that the persistent session is still usable.

Closing the viewer tab leaves automation running. To roll back, remove the systemd override,
restart cranbourne-automation after daemon-reload, and remove the Nginx /browser/ location.
