---
name: hosting-site-with-tailscale
description: Use when a SharkByte teammate wants to put the sorter dashboard online from their own laptop, share a public link to the arm, run Tailscale Funnel, or debug a ts.net link that won't load, rejects Auth0 login, or breaks the laptop's internet.
---

# Hosting the sorter site with Tailscale Funnel

## Overview
The bridge runs on the laptop the arm is plugged into, and Tailscale Funnel gives it one stable HTTPS address, `https://<machine>.<tailnet>.ts.net`. Nothing is deployed to a cloud host. The full guide is `docs/RUN_ONLINE.md`; this skill covers the steps and the pitfalls.

The bridge opens two ports. Funnel gets **only the public one**:

| Port | Who | Login |
|---|---|---|
| 4173 local | the laptop's browser only | Auth0 (skip with `--no-auth` for offline trials) |
| 4174 public | Funnel → anyone with the link | Auth0 operators; STOP open to everyone |

**Never run `tailscale funnel 4173`.**

## One-time setup (the human does the sign-ins, never the agent)
1. Install Tailscale (Windows: `winget install Tailscale.Tailscale`; Mac: the App Store app) and sign in with **your own** account.
2. If Cloudflare WARP or another VPN/DNS tool is on, turn off Tailscale DNS: `tailscale set --accept-dns=false`. Otherwise the laptop can lose internet.
3. Run `tailscale funnel 4174` once. The first run prints an admin-console link to enable HTTPS and Funnel. Follow it, then press Ctrl+C.
4. Get the host name: `tailscale status --json`, field `Self.DNSName`, minus the trailing dot.
5. Ask **@abhiwasabi** (Auth0 tenant owner) to add `https://<host>/` to Allowed Callback and Logout URLs, and `https://<host>` to Web Origins, and to give your Auth0 user the SharkByte Operator role (`sorter:operate`). You must sign in once before he can find your user.
6. `pip install -r bridge/requirements.txt` into the repo's `.venv` (includes PyJWT for Auth0).

## Every run
Set a long operator password in the same terminal. It is only read from the environment; never commit, post, or print it.

**Windows (PowerShell):**
```powershell
$env:BRIDGE_OPERATOR_PASSWORD = "<long password>"
.\tools\run-online.ps1 -Mock       # all simulated   (-MockArm: real camera + Gemini; none: real arm)
```
The script turns Funnel on for 4174 only, and runs `tailscale funnel reset` on Ctrl+C.

**Mac/Linux (no script yet):**
```sh
export BRIDGE_OPERATOR_PASSWORD='<long password>'
.venv/bin/python -m bridge --mock --public-port 4174 --public-host <host>
tailscale funnel --bg 4174          # second terminal
# when finished:
tailscale funnel reset && tailscale funnel status
```
On a Mac without the CLI on PATH, use `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.

## Check it works
From a phone **on mobile data**, not the laptop's Wi-Fi:
1. `https://<host>` loads and sends you to Auth0. After sign-in you see the camera and arm state.
2. Home and a manual sort (after pausing automatic sorting) work.
3. STOP works.
4. **Log out** in the Operator panel or **Sign out** at the top right locks the controls again.

`tailscale funnel status` must show only port 4174, and show "No serve config" after you stop.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| Auth0 page says callback URL mismatch | This exact URL isn't registered. Locally only `http://127.0.0.1:4173/` and `:4175/` work; use `127.0.0.1`, not `localhost`. Public: ask Abhi to add your ts.net URL. |
| Signed in, but controls say "Operator permission required" (403) | Your Auth0 user lacks the `sorter:operate` role. |
| 403 on every request through the link | `--public-host` doesn't exactly match `Self.DNSName` (no trailing dot). |
| "Too many attempts" on the password | The public password lockout is shared; anyone can trigger it. Use Auth0 or the laptop dashboard. |
| Laptop lost internet after starting Tailscale | DNS clash with WARP or a VPN: `tailscale set --accept-dns=false`. |
| Bridge exits with "Auth0 startup failed" | Partial `AUTH0_*` variables or a missing PyJWT. Unset all three or set all three, and reinstall the requirements. |
| Offline bench trial, no internet | Start with `--no-auth` (script: `-BridgeArgs '--no-auth'`). Auth0 is then off on **both** ports; the public link falls back to the operator password. |

## Rules
- Only one person hosts at a time: the one with the arm. Share their link on the board.
- Keep the physical servo power switch within reach. Web STOP depends on the laptop, network and Funnel.
- Keep the laptop plugged in with sleep off while the link is shared.
- Agents never type passwords, sign in to Tailscale or Auth0, or put the operator password in a file, URL or commit.
