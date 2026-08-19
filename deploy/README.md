# Deploying the CT Keno dashboard to Oracle Cloud (Always Free)

This app is a Python server that scrapes ctlottery.org every 30s and writes to
a local CSV. It uses **only the Python standard library** — no pip installs.

## 1. Create the Oracle Cloud account (you must do this)

1. Go to https://www.oracle.com/cloud/free/ and click "Start for free".
2. Sign up with your email. You'll be asked for a **credit card for identity
   verification only** — you are never charged on the Always Free tier.
3. After signup, create an **Always Free** VM:
   - Shape: **VM.Standard.E2.1.Micro** (AMD, 1 OCPU / 1 GB RAM) — this is the
     one that's reliably free. (The ARM "Ampere A1" 4-core is also free but
     frequently "out of capacity".)
   - Image: **Ubuntu 22.04 LTS** (or 24.04).
   - Generate/download an **SSH key pair** when prompted (keep the private key).
4. Note the VM's **public IP address**.

> ⚠️ Oracle's free tier is the most finicky to sign up for. If you get "out of
> capacity" on the ARM shape, use the AMD Micro shape. If the card is rejected,
> it's usually a bank/region issue — retry or use a different card.

## 2. Open the firewall port

In the Oracle console, for your VM's **Virtual Cloud Network (VCN)**:
- Add an **Ingress rule** allowing **TCP port 8000** from `0.0.0.0/0`.

(Without this, the dashboard won't be reachable from your phone.)

## 3. Copy the code to the VM

From your Windows machine (git-bash), scp the project up:

```bash
scp -i ~/.ssh/your-key -r C:/Users/thesa/ct-keno-sim ubuntu@<VM-IP>:/home/ubuntu/
```

(Or `git clone` the repo if you've pushed it to GitHub.)

## 4. Deploy

```bash
ssh -i ~/.ssh/your-key ubuntu@<VM-IP>
cd /home/ubuntu/ct-keno-sim
sudo bash deploy/deploy.sh
```

That installs a systemd service that:
- runs `server.py --interval 30` (scrapes every 30s, 24/7)
- auto-restarts if it crashes
- auto-starts on VM reboot

## 5. Open it on your phone

```
http://<VM-IP>:8000        # full dashboard
http://<VM-IP>:8000/today  # today-only analysis
```

## Notes / caveats

- **No HTTPS** — the free VM serves plain HTTP. That's fine for viewing, but
  don't put anything sensitive on it. (Adding a free domain + Let's Encrypt is
  possible later if you want.)
- **Data persists** — the CSV lives on the VM's boot volume, so it survives
  restarts (unlike free PaaS hosts).
- **The VM is always on** — no sleep, no credit card charges on the free tier.
- To update the code later: scp the changed files, then
  `sudo systemctl restart keno`.
