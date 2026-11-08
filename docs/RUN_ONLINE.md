# Run the sorter site online

The arm stays plugged into the laptop. The laptop bridge keeps serving the dashboard, and **Tailscale Funnel** gives it a stable public address: `https://<laptop>.<tailnet>.ts.net`. Nothing is deployed anywhere else.

## Who can do what

| On the public URL | Allowed |
| --- | --- |
| Anyone with the link | Watch the camera, arm state, counts and history. Press **STOP**. |
| Operator (after logging in with the operator password) | Everything else: Home, manual sort, start/pause automatic sorting, camera and Gemini settings. |

The laptop's own dashboard, `http://127.0.0.1:4173`, works as before, with no password. **Never give port 4173 to Funnel.** Only the public port (4174) enforces the password.

## How it works

```text
phone / judge's browser ──https──► Tailscale Funnel ──► 127.0.0.1:4174  (public port: login required for control)
laptop browser ───────────────────────────────────────► 127.0.0.1:4173  (local port: no password, never exposed)
                                                          both ports ──► one bridge ──► arm (USB)
```

Funnel traffic reaches the bridge from the laptop itself. So the bridge can't tell a remote visitor from someone at the laptop by address or Host header, which is why there are two separate ports.

## One-time setup (Paul)

1. **Install Tailscale:** `winget install Tailscale.Tailscale`, then sign in from the Tailscale tray icon. Use your own account; Claude never handles passwords.
2. **Enable Funnel** for your tailnet: run `tailscale funnel 4174` once. The first time, it prints a link to the admin console to turn on HTTPS certificates and Funnel. Follow it, then press Ctrl+C.
3. **Find your public address:** `tailscale status --json`, field `Self.DNSName` (drop the trailing dot), e.g. `sharkbyte-laptop.tail1234.ts.net`.

## Every time

In PowerShell, in the repository folder:

```powershell
$env:BRIDGE_OPERATOR_PASSWORD = "<choose a long password>"   # only in this window; never commit it
.\tools\run-online.ps1 -MockArm      # real camera + Gemini, simulated arm
.\tools\run-online.ps1               # real arm (needs taught poses, calibration, ARM_PORT, Gemini)
.\tools\run-online.ps1 -Mock         # everything simulated (rehearsal)
```

The script:
- checks the password is set (8+ characters);
- finds your ts.net name;
- starts the bridge with both ports;
- turns Funnel on for port 4174 only;
- turns Funnel off again when you press Ctrl+C.

Share `https://<your-name>.ts.net`. To operate from another device, open that URL, log in with the operator password, and use the controls.

Doing it by hand instead:

```powershell
python -m bridge --mock-arm --public-port 4174 --public-host <your-name>.ts.net
tailscale funnel --bg 4174     # in a second window
tailscale funnel reset         # when finished
tailscale funnel status        # should now show nothing
```

`tailscale funnel reset` clears **all** Serve and Funnel settings on this laptop, not only this one. That's fine unless you've set up other Tailscale Serve or Funnel sites yourself. The script runs it too when you press Ctrl+C. Afterwards, check that `tailscale funnel status` is empty.

## Safety

- **Keep the physical servo power switch within reach.** STOP on the site depends on the laptop, the network and Funnel all working.
- **Keep the laptop awake and online** (plugged in; sleep off). If it sleeps, the site goes offline and the arm holds or goes limp, depending on what it was doing.
- **Use a long, unique operator password, and change it after the event.** It lives only in the PowerShell window's environment.
- **Never run `tailscale funnel 4173`.** The local port has no password and no login lockout. Wrong guesses on the public URL do not lock the laptop dashboard.
- Anyone with the link can press STOP. That's intended: a viewer can always stop the arm, but nothing else.
- Wrong passwords on the public URL share one lockout. It starts at 60 seconds and doubles after each later failure, up to 15 minutes. The count stays until a correct password.
- **Log out** ends operator access in every browser that had logged in. A visitor who is not logged in cannot log the operator out.
- Ctrl+C closes the bridge even if a web request is stuck. The script then turns Funnel off even if the bridge process has not exited.

## Check it works

From a phone on mobile data (not the laptop's Wi-Fi):
1. The URL loads, showing the live camera and arm state.
2. The controls are disabled, and the Operator panel asks for the password.
3. STOP works.
4. After logging in, **Home arm** and a manual sort move the arm.
5. **Log out** disables the controls again.
