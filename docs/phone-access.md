# Using ninety-five from your phone

The app runs on this PC and stays private: Tailscale links the PC and your
phone in a private network that only your own signed-in devices can reach.
Nothing is opened to the internet.

## Once, on the PC

1. Install Tailscale:

       curl -fsSL https://tailscale.com/install.sh | sh

2. Connect and sign in (a browser link appears; use the account you want, e.g. Google):

       sudo tailscale up

3. Let your user manage Tailscale without sudo:

       sudo tailscale set --operator=$USER

4. Publish the app inside your tailnet (HTTPS, your devices only):

       tailscale serve --bg 8000

   If it asks you to enable HTTPS certificates or MagicDNS, follow the link it
   prints and switch them on in the Tailscale admin page, then run it again.
   `tailscale serve status` prints the address, like
   `https://vonneumann.<your-tailnet>.ts.net`.

Never use `tailscale funnel`: that would make the app public.

## Once, on the phone (Pixel)

1. Install **Tailscale** from the Play Store and sign in with the same account.
2. Open the `https://…ts.net` address in Chrome.
3. Chrome menu ⋮ → **Add to home screen**, so it opens like an app.

## When travelling

Tailscale on the phone must be switched on (it usually stays on). The PC must
be on, which it is unless it's unplugged or shut down. If the page doesn't
load, check the Tailscale app shows the PC as connected.
