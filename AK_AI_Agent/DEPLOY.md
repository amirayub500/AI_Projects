# Deploy on Oracle Cloud (Always Free)

Put the Research AI Agent on a free Oracle Cloud server so anyone with the link (and your password) can use it at `http://YOUR-SERVER-IP`.

**What you get:** an Arm (Ampere A1) Ubuntu server running two Docker containers: Ollama with `qwen3:1.7b`, and the web app.
**Time:** about an hour the first time.
**Cost:** free within Oracle's Always Free limits. Oracle has changed these limits before, so check them at sign-up, and set a budget alert (step 9).

> Commands marked **PowerShell** run on your Windows PC. Commands marked **server** run on the Oracle server after you connect with SSH.

---

## 1. Create an Oracle Cloud account

1. Go to <https://www.oracle.com/cloud/free/> and click **Start for free**.
2. Pick your **home region** carefully: Always Free Arm servers can only be created there, and it can't be changed later. A region near you is fine; if it often reports "out of capacity" (step 2), there's no fix except waiting and retrying.
3. Sign-up usually asks for a card to verify your identity. Always Free resources are not charged.

## 2. Create the server

In the Oracle Cloud console: **☰ Menu → Compute → Instances → Create instance**.

| Setting | Choose |
| --- | --- |
| Name | `research-ai` |
| Image | **Canonical Ubuntu 24.04** (click *Change image*) |
| Shape | *Change shape* → **Ampere** → **VM.Standard.A1.Flex** → **2 OCPUs, 12 GB memory** (stay within the Always Free allowance shown on screen) |
| Networking | Create a new virtual cloud network with a **public subnet**; **Assign a public IPv4 address** = Yes |
| SSH keys | **Generate a key pair for me** → **Save private key** (e.g. to `C:\Users\YOU\.ssh\oracle.key`) |
| Boot volume | Leave the default |

Click **Create**. When the instance shows **Running**, copy its **Public IP address**.

> **"Out of capacity"?** Free Arm servers are popular. Try a different *Availability domain* in the Placement section, or try again later (early morning often works).

## 3. Open port 80 (two places)

Oracle blocks web traffic twice: in the cloud network and inside Ubuntu.

**3a. Cloud network.** On the instance page, click the **subnet** link → **Security** (or *Security Lists*) → the default security list → **Add Ingress Rules**:

| Field | Value |
| --- | --- |
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Destination port range | `80` |

**3b. Ubuntu firewall** is done in step 5, once you're connected.

## 4. Connect to the server

**PowerShell** (Windows has SSH built in):

```powershell
# Let only you read the key (SSH refuses keys others can read)
icacls "$HOME\.ssh\oracle.key" /inheritance:r /grant:r "$($env:USERNAME):(R)"

# Connect (replace with your server's public IP)
ssh -i "$HOME\.ssh\oracle.key" ubuntu@YOUR-SERVER-IP
```

Type `yes` the first time. You're in when the prompt shows `ubuntu@research-ai`.

## 5. Prepare the server

**server:**

```bash
# Updates
sudo apt update && sudo apt -y upgrade

# 3b. Allow port 80 through Ubuntu's firewall (Oracle images block it by default)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo netfilter-persistent save

# Install Docker
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
```

Then **log out and back in** so the Docker group applies: type `exit`, then run the `ssh` command from step 4 again.

## 6. Get the code and set your password

**server:**

```bash
git clone https://github.com/amirayub500/AI_Projects.git
cd AI_Projects/AK_AI_Agent

cp .env.example .env
nano .env
```

In the editor, change `APP_PASSWORD=change-me` to a password of your own. Save with **Ctrl+O**, **Enter**, exit with **Ctrl+X**.

## 7. Start it

**server:**

```bash
docker compose up -d --build
```

The first start downloads the Ollama image and the `qwen3:1.7b` model (over 1 GB), so give it several minutes. Check progress:

```bash
docker compose ps              # app should become "healthy"
docker compose logs -f app     # Ctrl+C stops watching (the app keeps running)
```

Open **http://YOUR-SERVER-IP** in a browser. Enter any user name and your password, then a topic.

## 8. Day-to-day commands

**server** (from `~/AI_Projects/AK_AI_Agent`):

| Task | Command |
| --- | --- |
| See what's running | `docker compose ps` |
| Watch the app's log | `docker compose logs -f app` |
| Restart after editing `.env` | `docker compose up -d` |
| Update to the latest code from GitHub | `git pull && docker compose up -d --build` |
| Stop everything | `docker compose down` |
| Start again | `docker compose up -d` |

The containers restart automatically if the server reboots. The model stays downloaded between restarts.

## 9. Keep it safe and free

- **Use a password** (`APP_PASSWORD`). Without one, anyone who finds the IP can use your server.
- **The password is not encrypted over `http://`.** Don't reuse an important password. For real HTTPS you need a domain name and a reverse proxy such as Caddy; that's a good next step.
- **Set a budget alert:** console → **Billing & Cost Management → Budgets → Create budget**, e.g. alert at $1, so you hear about any charge.
- **Update now and then:** `sudo apt update && sudo apt -y upgrade`.
- One research runs at a time, each visitor gets `RATE_LIMIT_PER_HOUR` runs per hour, and a run stops after `RESEARCH_TIMEOUT` seconds. All are set in `.env`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Browser can't reach `http://IP` | Check both firewall steps (3a ingress rule, 5 iptables), and that `docker compose ps` shows the app running |
| `permission denied ... docker.sock` | You didn't log out and back in after step 5 |
| App stuck at "waiting" / not starting | `docker compose logs model-pull` - the model download may still be running or failed (re-run `docker compose up -d`) |
| "Another research is running" | Only one runs at a time; wait for it to finish |
| Very slow | Expected on 2 Arm CPUs; keep `MAX_STEPS` at 5 and the model at `qwen3:1.7b` |
| `ssh: ... UNPROTECTED PRIVATE KEY FILE` | Run the `icacls` command in step 4 |

## Load company data on the server

Client files live only on the server (`data/` is never in Git).

**1. Create the folder** (server, once, before the first `docker compose up` after this update):

```bash
cd ~/AI_Projects/AK_AI_Agent
mkdir -p data/inbox
```

**2. Copy files from your laptop** (PowerShell; replace the IP and folder):

```powershell
scp -i "$HOME\.ssh\ak-ai-agent-key.pem" -r "C:\ClientFiles\*" ubuntu@SERVER-IP:~/AI_Projects/AK_AI_Agent/data/inbox/
```

**3. Import** (server):

```bash
docker compose exec app python ingest.py data/inbox
docker compose exec app python ingest.py --status
```

Then ask in the web page with `/` in front, e.g. `/ Who pledged the most in 2026?`

Re-running the import only adds new files. To start over: `rm data/company.db` and import again.
