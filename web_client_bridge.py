from flask import Flask, request, jsonify
from flask_cors import CORS
import socket
import json
import threading
import secrets
import string
import re
import time

app = Flask(__name__)
CORS(app)

# -----------------------------
# Config
# -----------------------------
VANILLACOIN_SERVER_HOST = "127.0.0.1"
VANILLACOIN_SERVER_PORT = 5050
HEADER = 64
FORMAT = "utf-8"

# Used only for faucet fallback (when AIR_DROP is unsupported).
FAUCET_ACCOUNT = "FAUCET"   # auto-created / auto-mined if needed
FAUCET_MINING_STEP_SECONDS = 2   # seconds per mining attempt during auto-fund
FAUCET_MINING_MAX_STEPS   = 15  # upper bound (total ~30s) to avoid infinite loops

# -----------------------------
# Socket bridge (tolerant of framed/unframed)
# -----------------------------
class VanillaCoinBridge:
    def __init__(self, host="127.0.0.1", port=5050):
        self.server_host = host
        self.server_port = port
        self.client = None
        self.connected = False
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            if self.connected and self.client:
                return True
            try:
                self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.client.settimeout(5)
                self.client.connect((self.server_host, self.server_port))
                self.connected = True
                return True
            except Exception as e:
                print(f"Error connecting to server: {e}")
                self.connected = False
                self.client = None
                return False

    def _recv_exact(self, n: int) -> bytes:
        chunks, remaining = [], n
        while remaining > 0:
            chunk = self.client.recv(remaining)
            if not chunk:
                raise ConnectionError("Socket closed while receiving data")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_until_quiet(self, first_chunk: bytes = b"", quiet_timeout=0.25, max_total=1_048_576) -> bytes:
        data = bytearray(first_chunk)
        self.client.settimeout(quiet_timeout)
        try:
            while len(data) < max_total:
                try:
                    chunk = self.client.recv(4096)
                    if not chunk:
                        break
                    data.extend(chunk)
                except socket.timeout:
                    break
        finally:
            self.client.settimeout(5)
        return bytes(data)

    def send_message(self, msg: str):
        if not self.connected:
            if not self.connect():
                return None
        try:
            payload = msg.encode(FORMAT)
            send_len = str(len(payload)).encode(FORMAT)
            send_len += b" " * (HEADER - len(send_len))
            self.client.sendall(send_len)
            self.client.sendall(payload)

            header_bytes = self.client.recv(HEADER)
            if not header_bytes:
                return ""

            header_str = header_bytes.decode(FORMAT, errors="ignore").strip()
            if header_str.isdigit():
                resp_len = int(header_str)
                if resp_len <= 0:
                    return ""
                data = self._recv_exact(resp_len)
                return data.decode(FORMAT, errors="replace")
            else:
                data = self._recv_until_quiet(first_chunk=header_bytes)
                return data.decode(FORMAT, errors="replace")
        except Exception as e:
            print(f"Error sending message: {e}")
            self.connected = False
            try:
                if self.client:
                    self.client.close()
            finally:
                self.client = None
            return None

    def disconnect(self):
        if self.connected and self.client:
            try:
                try:
                    payload = "!DISCONNECT".encode(FORMAT)
                    send_len = str(len(payload)).encode(FORMAT)
                    send_len += b" " * (HEADER - len(send_len))
                    self.client.sendall(send_len)
                    self.client.sendall(payload)
                except Exception:
                    pass
                self.client.close()
            except:
                pass
            self.connected = False
            self.client = None

bridge = VanillaCoinBridge(VANILLACOIN_SERVER_HOST, VANILLACOIN_SERVER_PORT)

# -----------------------------
# Socket command helpers
# -----------------------------
def cmd_check_username(username: str):
    return bridge.send_message(f"CHECK_USERNAME|{username}")

def cmd_register(username: str, password: str, word_list_json: str, hardware_json: str):
    return bridge.send_message(f"REGISTER|{username}|{password}|{word_list_json}|{hardware_json}")

def cmd_login(username: str, password: str, word_list_json: str, hardware_json: str):
    return bridge.send_message(f"LOGIN|{username}|{password}|{word_list_json}|{hardware_json}")

def cmd_get_balance(username: str):
    return bridge.send_message(f"GET_BALANCE|{username}")

def cmd_send_transaction(from_user: str, to_user: str, amount: float):
    return bridge.send_message(f"SEND_TRANSACTION|{from_user}|{to_user}|{amount}")

def cmd_get_history(username: str):
    return bridge.send_message(f"GET_HISTORY|{username}")

def cmd_mine(username: str, seconds: int):
    return bridge.send_message(f"MINE|{username}|{seconds}")

def cmd_airdrop(to_user: str, amount: float):
    return bridge.send_message(f"AIR_DROP|{to_user}|{amount}")

# Utility: strong random password for auto-created accounts
def _rand_password(n=24):
    alphabet = string.ascii_letters + string.digits + "!@#%^*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(n))

# Utility: ensure username exists (create if missing)
def ensure_user(username: str) -> bool:
    resp = cmd_check_username(username) or ""
    up = resp.upper()
    if "USERNAME_AVAILABLE" in up or ("AVAILABLE" in up and "NOT" not in up):
        pw = _rand_password()
        reg = cmd_register(username, pw, json.dumps([]), json.dumps({})) or ""
        return "REGISTER_SUCCESS" in reg.upper() or "CREATED" in reg.upper() or "REGISTERED" in reg.upper()
    return "NOT FOUND" not in up and "DOES NOT EXIST" not in up

def parse_required_available(msg: str):
    """
    Parse strings like:
    'TRANSACTION_FAILED: Insufficient balance. Required: 10.10000000, Available: 0.00000000'
    Returns (required, available) as floats when found, else (None, None).
    """
    try:
        m = re.search(r"Required:\s*([0-9.]+)\s*,\s*Available:\s*([0-9.]+)", msg, re.IGNORECASE)
        if m:
            return float(m.group(1)), float(m.group(2))
    except:
        pass
    return None, None

# -----------------------------
# Minimal root
# -----------------------------
@app.route("/")
def index():
    return '''
    <!doctype html>
    <html><head><meta charset="utf-8"><title>VanillaCoin</title>
    <style>body{font-family:system-ui;background:#eef2ff;padding:24px}
    a{display:inline-block;margin-top:10px;background:linear-gradient(135deg,#4c51bf,#667eea);color:#fff;padding:10px 14px;border-radius:10px;text-decoration:none;font-weight:700}
    .pill{display:inline-block;padding:6px 12px;border-radius:999px;color:#fff;background:#ef4444}</style></head>
    <body>
      <h1>🪙 VanillaCoin</h1>
      <div id="status" class="pill">Use /wallet for full UI</div><br/>
      <a href="/wallet">Open Wallet</a>
      <script>
        fetch('/api/ping').then(r=>r.json()).then(j=>{
          const el=document.getElementById('status');
          el.textContent = j && j.ok ? 'Server OK — open /wallet' : 'Open /wallet';
          el.style.background = '#10b981';
        }).catch(()=>{});
      </script>
    </body></html>
    '''

@app.route("/api/ping")
def api_ping():
    return jsonify({"ok": True})

# -----------------------------
# JSON API
# -----------------------------
@app.route("/api/connect", methods=["POST"])
def api_connect():
    ok = bridge.connect()
    return jsonify({"success": ok})

@app.route("/api/check_username/<username>")
def api_check_username(username):
    if not username:
        return jsonify({"success": False, "message": "Username required"}), 400
    resp = cmd_check_username(username)
    if resp is None:
        return jsonify({"success": False, "message": "Server unreachable"}), 503
    low = resp.strip().upper()
    if "USERNAME_AVAILABLE" in low and "TAKEN" not in low:
        return jsonify({"success": True, "available": True, "message": "Available"})
    if "USERNAME_TAKEN" in low:
        return jsonify({"success": True, "available": False, "message": "Taken"})
    if "AVAILABLE" in low and "NOT" not in low:
        return jsonify({"success": True, "available": True, "message": resp})
    if "NOT FOUND" in low or "DOES NOT EXIST" in low:
        return jsonify({"success": True, "available": True, "message": resp})
    return jsonify({"success": False, "message": resp}), 400

@app.route("/api/register", methods=["POST"])
def api_register():
    try:
        data = request.get_json(force=True)
        username = data.get("username", "").strip()
        password = data.get("password", "")
        word_list = data.get("word_list", [])
        hardware_info = data.get("hardware_info", {})
        if not username or not password:
            return jsonify({"success": False, "message": "Username and password required"}), 400

        resp = cmd_register(username, password, json.dumps(word_list), json.dumps(hardware_info))
        if resp is None:
            return jsonify({"success": False, "message": "Server unreachable"}), 503

        low = resp.strip().upper()
        if "REGISTER_SUCCESS" in low or "REGISTERED" in low or "CREATED" in low:
            return jsonify({"success": True, "message": "Account created"})
        if "USERNAME_TAKEN" in low or "ALREADY EXISTS" in low:
            return jsonify({"success": False, "message": "Username already taken"}), 409
        return jsonify({"success": False, "message": resp}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500

@app.route("/api/login", methods=["POST"])
def api_login():
    try:
        data = request.get_json(force=True)
        username = data.get("username", "").strip()
        password = data.get("password", "")
        word_list = data.get("word_list", [])
        hardware_info = data.get("hardware_info", {})
        if not username:
            return jsonify({"success": False, "message": "Username required"}), 400

        resp = cmd_login(username, password or "__HWID_ONLY__", json.dumps(word_list), json.dumps(hardware_info))
        if resp is None:
            return jsonify({"success": False, "message": "Server unreachable"}), 503

        low = resp.strip().upper()
        if "LOGIN_SUCCESS" in low or "SUCCESS" in low:
            return jsonify({"success": True, "message": "Login successful"})
        if "HARDWARE_MISMATCH" in low:
            return jsonify({"success": False, "message": "Hardware mismatch"}), 403
        if "LOGIN_FAILED" in low or "INVALID CREDENTIALS" in low or "USER NOT FOUND" in low:
            return jsonify({"success": False, "message": "Invalid credentials or user not found"}), 401
        if "ERROR" in low:
            return jsonify({"success": False, "message": "Server error during login"}), 500
        return jsonify({"success": False, "message": resp}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500

@app.route("/api/balance/<username>")
def api_balance(username):
    if not username:
        return jsonify({"success": False, "message": "Username required"}), 400
    resp = cmd_get_balance(username)
    if resp is None:
        return jsonify({"success": False, "message": "Server unreachable"}), 503
    try:
        j = json.loads(resp)
        return jsonify({"success": True, "balance": float(j.get("balance", 0.0))})
    except Exception:
        try:
            val = float(resp.strip())
            return jsonify({"success": True, "balance": val})
        except:
            return jsonify({"success": False, "message": resp}), 400

@app.route("/api/send", methods=["POST"])
def api_send():
    try:
        data = request.get_json(force=True)
        from_user = data.get("from", "").strip()
        to_user = data.get("to", "").strip()
        amount = float(data.get("amount", 0.0))
        if not from_user or not to_user or amount <= 0:
            return jsonify({"success": False, "message": "Invalid fields"}), 400

        resp = cmd_send_transaction(from_user, to_user, amount)
        if resp is None:
            return jsonify({"success": False, "message": "Server unreachable"}), 503

        low = resp.strip().upper()
        if "SEND_SUCCESS" in low or "SENT" in low or "OK" in low:
            return jsonify({"success": True, "message": "Transaction sent"})
        return jsonify({"success": False, "message": resp}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500

@app.route("/api/history/<username>")
def api_history(username):
    if not username:
        return jsonify({"success": False, "message": "Username required"}), 400
    resp = cmd_get_history(username)
    if resp is None:
        return jsonify({"success": False, "message": "Server unreachable"}), 503
    try:
        history = json.loads(resp)
        if not isinstance(history, list):
            raise ValueError("History not list")
        for tx in history:
            if "amount" in tx:
                try:
                    tx["amount"] = float(tx["amount"])
                except:
                    pass
        return jsonify({"success": True, "history": history})
    except Exception:
        lines = [ln.strip() for ln in resp.splitlines() if ln.strip()]
        hist = []
        for ln in lines:
            low = ln.lower()
            item = {"raw": ln}
            if "sent" in low:
                item["type"] = "sent"
            elif "received" in low:
                item["type"] = "received"
            hist.append(item)
        return jsonify({"success": True, "history": hist})

@app.route("/api/airdrop", methods=["POST"])
def api_airdrop():
    """
    +10 VNC:
      1) Try AIR_DROP|<user>|<amount>.
      2) If unsupported/failed, ensure FAUCET exists, auto-mine FAUCET until it has enough (amount + fee),
         then SEND_TRANSACTION FAUCET->user.
    """
    try:
        data = request.get_json(force=True)
        to_user = data.get("to", "").strip()
        amount = float(data.get("amount", 10.0))
        if not to_user or amount <= 0:
            return jsonify({"success": False, "message": "Invalid fields"}), 400

        # First try native airdrop
        resp = cmd_airdrop(to_user, amount)
        if resp is not None:
            low = resp.strip().upper()
            if ("AIR_DROP_SUCCESS" in low) or ("AIRDROP_SUCCESS" in low) or ("OK" in low):
                return jsonify({"success": True, "message": f"Airdropped +{amount} VNC"})
            # if it's a hard non-unknown failure and not balance-related, fall through to faucet

        # Faucet fallback: ensure accounts
        if not ensure_user(FAUCET_ACCOUNT):
            return jsonify({"success": False, "message": "Faucet account could not be created"}), 500
        ensure_user(to_user)

        # Try the transfer once
        tr = cmd_send_transaction(FAUCET_ACCOUNT, to_user, amount)
        if tr is None:
            return jsonify({"success": False, "message": "Server unreachable"}), 503
        tr_up = tr.strip().upper()
        if "SEND_SUCCESS" in tr_up or "SENT" in tr_up or "OK" in tr_up:
            return jsonify({"success": True, "message": f"Airdropped +{amount} VNC (faucet)"} )

        # If insufficient balance, parse required and auto-mine FAUCET until enough
        if "INSUFFICIENT" in tr_up or "REQUIRED:" in tr_up:
            required, available = parse_required_available(tr)
            target = required if required is not None else (amount + 0.2)  # add cushion for fees if unknown
            # poll/mine loop
            steps = 0
            while steps < FAUCET_MINING_MAX_STEPS:
                # check current balance
                b = cmd_get_balance(FAUCET_ACCOUNT) or "0"
                try:
                    cur_bal = float(json.loads(b).get("balance")) if b.strip().startswith("{") else float(b.strip())
                except:
                    cur_bal = 0.0
                if cur_bal >= (target or amount):
                    break
                # mine step
                cmd_mine(FAUCET_ACCOUNT, FAUCET_MINING_STEP_SECONDS)
                # small delay to let server update
                time.sleep(0.2)
                steps += 1

            # try transfer again
            tr2 = cmd_send_transaction(FAUCET_ACCOUNT, to_user, amount) or ""
            up2 = tr2.strip().upper()
            if "SEND_SUCCESS" in up2 or "SENT" in up2 or "OK" in up2:
                return jsonify({"success": True, "message": f"Airdropped +{amount} VNC (faucet)"} )
            return jsonify({"success": False, "message": tr2}), 400

        # some other failure
        return jsonify({"success": False, "message": tr}), 400

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500

@app.route("/api/mine", methods=["POST"])
def api_mine():
    """
    Mine for the full requested duration, even if blocks are found early.
    We do this by calling the server in smaller steps and looping until time is up.
    """
    try:
        data = request.get_json(force=True)
        username = data.get("username", "").strip()
        seconds = int(data.get("seconds", 10))
        if not username or seconds <= 0:
            return jsonify({"success": False, "message": "Username and seconds required"}), 400

        # Step size: 1–5 seconds is usually responsive without spamming
        step = int(data.get("step", 2))
        step = max(1, min(step, 10))

        start = time.time()
        end = start + seconds

        blocks_found = 0
        last_resp = ""

        # Keep mining until time is up
        while time.time() < end:
            remaining = end - time.time()
            this_step = step if remaining >= step else max(1, int(remaining))

            resp = cmd_mine(username, this_step)
            if resp is None:
                return jsonify({"success": False, "message": "Server unreachable"}), 503

            last_resp = resp
            up = resp.strip().upper()

            # Count blocks if your server output includes any of these keywords
            # (adjust if your server uses different wording)
            if ("BLOCK" in up and ("MINED" in up or "FOUND" in up)) or ("MINE_SUCCESS" in up):
                blocks_found += 1

            # Tiny sleep to avoid hammering the socket loop; optional
            time.sleep(0.05)

        elapsed = time.time() - start
        return jsonify({
            "success": True,
            "message": f"Mined for {elapsed:.1f}s (requested {seconds}s). Blocks found: {blocks_found}.",
            "blocks_found": blocks_found,
            "last_response": last_resp
        })

    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {e}"}), 500

# -----------------------------
# Full UI
# -----------------------------
HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VanillaCoin - Professional Wallet</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh;
    color: #1f2937;
    padding-bottom: 40px;
  }
  
  .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
  
  /* Header */
  .header {
    background: rgba(255, 255, 255, 0.98);
    backdrop-filter: blur(20px);
    border-radius: 20px;
    padding: 20px 30px;
    margin-bottom: 24px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.12);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
  }
  
  .header h1 {
    font-size: 1.8rem;
    background: linear-gradient(135deg, #667eea, #764ba2);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    font-weight: 800;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  
  .header h1::before {
    content: '';
    width: 32px;
    height: 32px;
    background: linear-gradient(135deg, #667eea, #764ba2);
    border-radius: 50%;
    display: inline-block;
  }
  
  .header-left { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .header-right { display: flex; gap: 12px; flex-wrap: wrap; }
  
  .badge {
    padding: 8px 16px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 0.875rem;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    transition: all 0.3s;
  }
  
  .badge-success { background: #10b981; color: white; }
  .badge-error { background: #ef4444; color: white; }
  .badge-info { background: #3b82f6; color: white; }
  .badge-warning { background: #f59e0b; color: white; }
  
  .btn {
    padding: 10px 20px;
    border: none;
    border-radius: 12px;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.3s;
    font-size: 0.875rem;
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }
  
  .btn-primary {
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white;
    box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
  }
  
  .btn-primary:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(102, 126, 234, 0.5);
  }
  
  .btn-success {
    background: linear-gradient(135deg, #10b981, #059669);
    color: white;
    box-shadow: 0 4px 12px rgba(16, 185, 129, 0.4);
  }
  
  .btn-success:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(16, 185, 129, 0.5);
  }
  
  .btn-secondary {
    background: #f3f4f6;
    color: #374151;
  }
  
  .btn-secondary:hover { background: #e5e7eb; }
  
  /* Tabs */
  .tabs {
    display: flex;
    gap: 12px;
    margin-bottom: 24px;
    background: rgba(255, 255, 255, 0.15);
    backdrop-filter: blur(10px);
    padding: 8px;
    border-radius: 16px;
  }
  
  .tab {
    flex: 1;
    padding: 14px 20px;
    text-align: center;
    border-radius: 12px;
    cursor: pointer;
    font-weight: 700;
    color: rgba(255, 255, 255, 0.7);
    transition: all 0.3s;
    font-size: 0.95rem;
  }
  
  .tab:hover { color: rgba(255, 255, 255, 0.9); background: rgba(255, 255, 255, 0.1); }
  .tab.active { background: white; color: #667eea; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15); }
  
  /* Cards */
  .card {
    background: rgba(255, 255, 255, 0.98);
    backdrop-filter: blur(20px);
    border-radius: 20px;
    padding: 28px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.12);
    margin-bottom: 20px;
  }
  
  .card-title {
    font-size: 1.25rem;
    font-weight: 800;
    color: #1f2937;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  
  .grid { display: grid; gap: 20px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
  
  /* Balance Display */
  .balance-display {
    font-size: 3rem;
    font-weight: 900;
    background: linear-gradient(135deg, #10b981, #059669);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    text-align: center;
    margin: 24px 0;
    text-shadow: 0 2px 4px rgba(0, 0, 0, 0.05);
  }
  
  /* Forms */
  .form-group { margin-bottom: 18px; }
  .form-group label {
    display: block;
    margin-bottom: 8px;
    font-weight: 600;
    color: #374151;
    font-size: 0.9rem;
  }
  
  input, select, textarea {
    width: 100%;
    padding: 14px 16px;
    border: 2px solid #e5e7eb;
    border-radius: 12px;
    font-size: 15px;
    transition: all 0.3s;
    font-family: inherit;
  }
  
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: #667eea;
    box-shadow: 0 0 0 4px rgba(102, 126, 234, 0.1);
  }
  
  /* Alerts */
  .alert {
    padding: 14px 18px;
    border-radius: 12px;
    margin-top: 16px;
    display: none;
    font-weight: 500;
    animation: slideIn 0.3s;
  }
  
  @keyframes slideIn {
    from { opacity: 0; transform: translateY(-10px); }
    to { opacity: 1; transform: translateY(0); }
  }
  
  .alert-success { background: #d1fae5; color: #047857; border: 2px solid #6ee7b7; }
  .alert-error { background: #fee2e2; color: #dc2626; border: 2px solid #fca5a5; }
  .alert-info { background: #dbeafe; color: #1e40af; border: 2px solid #93c5fd; }
  
  /* Transaction History */
  .transaction-item {
    background: #f9fafb;
    border-radius: 14px;
    padding: 18px;
    margin-bottom: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-left: 5px solid #667eea;
    transition: transform 0.2s, box-shadow 0.2s;
  }
  
  .transaction-item:hover {
    transform: translateX(4px);
    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  }
  
  .transaction-sent { border-left-color: #ef4444; }
  .transaction-received { border-left-color: #10b981; }
  .transaction-mining { border-left-color: #f59e0b; }
  
  .transaction-amount {
    font-weight: 800;
    font-size: 1.3rem;
  }
  
  .transaction-sent .transaction-amount { color: #ef4444; }
  .transaction-received .transaction-amount { color: #10b981; }
  .transaction-mining .transaction-amount { color: #f59e0b; }
  
  /* Mining Terminal */
  .mining-terminal {
    background: #1f2937;
    border-radius: 14px;
    padding: 20px;
    color: #10b981;
    font-family: 'Courier New', monospace;
    font-size: 0.85rem;
    max-height: 400px;
    overflow-y: auto;
    margin-top: 16px;
    box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.3);
  }
  
  .terminal-line {
    margin: 4px 0;
    opacity: 0;
    animation: fadeInLine 0.3s forwards;
  }
  
  @keyframes fadeInLine {
    to { opacity: 1; }
  }
  
  .terminal-line.success { color: #10b981; }
  .terminal-line.error { color: #ef4444; }
  .terminal-line.info { color: #60a5fa; }
  .terminal-line.warning { color: #fbbf24; }
  
  /* Mining Stats */
  .mining-stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 16px;
    margin: 20px 0;
  }
  
  .stat-box {
    background: linear-gradient(135deg, #667eea15, #764ba215);
    border-radius: 14px;
    padding: 18px;
    text-align: center;
    border: 2px solid #e5e7eb;
  }
  
  .stat-label {
    font-size: 0.8rem;
    color: #6b7280;
    font-weight: 600;
    margin-bottom: 8px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  
  .stat-value {
    font-size: 1.8rem;
    font-weight: 900;
    color: #667eea;
  }
  
  /* Auth Forms */
  .auth-tabs {
    display: flex;
    gap: 8px;
    margin-bottom: 20px;
  }
  
  .auth-tab {
    flex: 1;
    padding: 12px;
    border-radius: 10px;
    text-align: center;
    cursor: pointer;
    font-weight: 700;
    background: #f3f4f6;
    color: #6b7280;
    transition: all 0.3s;
  }
  
  .auth-tab.active {
    background: linear-gradient(135deg, #667eea, #764ba2);
    color: white;
  }
  
  .hide { display: none !important; }
  
  .loading-spinner {
    border: 3px solid #f3f4f6;
    border-top: 3px solid #667eea;
    border-radius: 50%;
    width: 20px;
    height: 20px;
    animation: spin 1s linear infinite;
    display: inline-block;
  }
  
  @keyframes spin {
    0% { transform: rotate(0deg); }
    100% { transform: rotate(360deg); }
  }
  
  .progress-bar {
    width: 100%;
    height: 8px;
    background: #e5e7eb;
    border-radius: 999px;
    overflow: hidden;
    margin: 16px 0;
  }
  
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, #667eea, #764ba2);
    border-radius: 999px;
    transition: width 0.3s;
    animation: shimmer 2s infinite;
  }
  
  @keyframes shimmer {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
  }
  
  @media (max-width: 768px) {
    .header { padding: 16px; }
    .header h1 { font-size: 1.4rem; }
    .balance-display { font-size: 2.2rem; }
    .card { padding: 20px; }
    .tabs { flex-direction: column; }
    .tab { padding: 12px; }
  }
</style>
</head>
<body>
<div class="container">
  <!-- Header -->
  <div class="header">
    <div class="header-left">
      <h1>VanillaCoin</h1>
      <span id="connBadge" class="badge badge-error">Disconnected</span>
      <span id="userBadge" class="badge badge-info">Not signed in</span>
    </div>
    <div class="header-right">
      <button id="btnConnect" class="btn btn-primary">Connect</button>
      <button id="btnLogout" class="btn btn-secondary hide">Logout</button>
    </div>
  </div>

  <!-- Auth Card -->
  <div id="authCard" class="card">
    <div class="auth-tabs">
      <div id="tabLogin" class="auth-tab active">Login</div>
      <div id="tabSignup" class="auth-tab">Create Account</div>
    </div>

    <!-- Login Pane -->
    <div id="loginPane">
      <div class="form-group">
        <label>Username</label>
        <input id="loginUser" type="text" placeholder="Enter your username" autocomplete="username"/>
      </div>
      <div class="form-group">
        <label>Password</label>
        <input id="loginPass" type="password" placeholder="Enter your password" autocomplete="current-password"/>
      </div>
      <div style="display: flex; align-items: center; gap: 8px; margin: 12px 0;">
        <input type="checkbox" id="rememberMe" style="width: auto;"/>
        <label for="rememberMe" style="margin: 0; cursor: pointer;">Remember me on this device</label>
      </div>
      <button id="btnLogin" class="btn btn-primary" style="width: 100%;">Login</button>
      <div id="loginAlert" class="alert"></div>
    </div>

    <!-- Signup Pane -->
    <div id="signupPane" class="hide">
      <div class="form-group">
        <label>Username</label>
        <input id="suUser" type="text" placeholder="Choose a unique username"/>
      </div>
      <div style="display: flex; gap: 10px; margin-bottom: 16px;">
        <button id="btnCheck" class="btn btn-secondary">Check Availability</button>
        <span id="checkMsg" class="badge badge-info">—</span>
      </div>
      <div class="form-group">
        <label>Password</label>
        <input id="suPass" type="password" placeholder="Create a strong password" autocomplete="new-password"/>
      </div>
      <div class="form-group">
        <label>Confirm Password</label>
        <input id="suPass2" type="password" placeholder="Confirm your password" autocomplete="new-password"/>
      </div>
      <button id="btnSignup" class="btn btn-primary" style="width: 100%;">Create Account</button>
      <div id="signupAlert" class="alert"></div>
    </div>
  </div>

  <!-- Main App Tabs -->
  <div class="tabs hide" id="appTabs">
    <div class="tab active" data-view="walletView">Wallet</div>
    <div class="tab" data-view="miningView">Mining</div>
    <div class="tab" data-view="historyView">History</div>
  </div>

  <!-- Wallet View -->
  <div id="walletView" class="view active">
    <div class="grid">
      <div class="card">
        <div class="card-title">Your Balance</div>
        <div id="balance" class="balance-display">0.00000000 VNC</div>
        <div style="display: flex; gap: 10px; flex-wrap: wrap;">
          <button id="btnRefresh" class="btn btn-success" style="flex: 1;">Refresh</button>
          <button id="btnAirdrop" class="btn btn-primary" style="flex: 1;">+10 VNC</button>
        </div>
        <div id="walletAlert" class="alert"></div>
      </div>

      <div class="card">
        <div class="card-title">Send VanillaCoin</div>
        <div class="form-group">
          <label>Recipient Username</label>
          <input id="txTo" type="text" placeholder="Enter recipient's username"/>
        </div>
        <div class="form-group">
          <label>Amount (VNC)</label>
          <input id="txAmt" type="number" min="0" step="0.00000001" placeholder="0.00000000"/>
        </div>
        <button id="btnSend" class="btn btn-primary" style="width: 100%;">Send Transaction</button>
        <div id="sendAlert" class="alert"></div>
      </div>
    </div>
  </div>

  <!-- Mining View -->
  <div id="miningView" class="view hide">
    <div class="card">
      <div class="card-title">Mining Dashboard</div>
      
      <!-- Mining Stats -->
      <div class="mining-stats">
        <div class="stat-box">
          <div class="stat-label">Status</div>
          <div class="stat-value" id="miningStatusDisplay">Idle</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Blocks Mined</div>
          <div class="stat-value" id="blocksMined">0</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Hash Rate</div>
          <div class="stat-value" id="hashRate">0 H/s</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Earnings</div>
          <div class="stat-value" id="miningEarnings">0 VNC</div>
        </div>
      </div>

      <!-- Progress Bar -->
      <div id="miningProgress" class="hide">
        <div class="progress-bar">
          <div class="progress-fill" id="progressFill" style="width: 0%"></div>
        </div>
        <div style="text-align: center; color: #6b7280; font-size: 0.9rem;">
          Mining in progress... <span id="progressPercent">0%</span>
        </div>
      </div>

      <!-- Mining Controls -->
      <div class="form-group">
        <label>Mining Duration (seconds)</label>
        <input id="mineSeconds" type="number" min="1" max="300" step="1" value="10" placeholder="10"/>
      </div>
      
      <div style="display: flex; gap: 12px;">
        <button id="btnStartMine" class="btn btn-success" style="flex: 1;">Start Mining</button>
        <button id="btnStopMine" class="btn btn-secondary" style="flex: 1;" disabled>Stop Mining</button>
        <button id="btnClearLogs" class="btn btn-secondary">Clear Logs</button>
      </div>

      <div id="mineAlert" class="alert"></div>

      <!-- Mining Terminal -->
      <div class="mining-terminal" id="miningTerminal">
        <div class="terminal-line info">VanillaCoin Mining Terminal v2.0</div>
        <div class="terminal-line">Ready to start mining...</div>
        <div class="terminal-line">Tip: Each block rewards 100 VNC</div>
      </div>
    </div>
  </div>

  <!-- History View -->
  <div id="historyView" class="view hide">
    <div class="card">
      <div class="card-title">Transaction History</div>
      <button id="btnHistory" class="btn btn-primary" style="margin-bottom: 16px;">Load History</button>
      <div id="historyLoading" class="hide" style="text-align: center; padding: 20px;">
        <div class="loading-spinner" style="margin: 0 auto;"></div>
        <p style="margin-top: 10px; color: #6b7280;">Loading transactions...</p>
      </div>
      <div id="hist"></div>
    </div>
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
let connected = false;
let currentUser = localStorage.getItem('vanillacoin_user') || null;
let miningActive = false;
let miningInterval = null;
let blocksMined = 0;
let totalEarnings = 0;

// HWID helpers
function getHWID() {
  let id = localStorage.getItem('vanillacoin_hwid');
  if (!id) {
    id = (crypto && crypto.randomUUID) ? crypto.randomUUID() : 'hw-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
    localStorage.setItem('vanillacoin_hwid', id);
  }
  return id;
}

function getHardwareInfo() {
  return { hwid: getHWID(), ua: navigator.userAgent || '', plat: navigator.platform || '', lang: navigator.language || '' };
}

// UI helpers
function show(el) { el.classList.remove('hide'); }
function hide(el) { el.classList.add('hide'); }

function showAlert(elementId, message, type = 'info') {
  const alert = $(elementId);
  alert.className = `alert alert-${type}`;
  alert.textContent = message;
  alert.style.display = 'block';
  setTimeout(() => { alert.style.display = 'none'; }, 5000);
}

function addTerminalLog(message, type = 'info') {
  const terminal = $('miningTerminal');
  const line = document.createElement('div');
  line.className = `terminal-line ${type}`;
  line.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
  terminal.appendChild(line);
  terminal.scrollTop = terminal.scrollHeight;
  
  // Keep only last 100 lines
  while (terminal.children.length > 100) {
    terminal.removeChild(terminal.firstChild);
  }
}

function setUserUI() {
  if (currentUser) {
    $('userBadge').textContent = currentUser;
    $('userBadge').className = 'badge badge-success';
    hide($('authCard'));
    show($('appTabs'));
    show($('btnLogout'));
  } else {
    $('userBadge').textContent = 'Not signed in';
    $('userBadge').className = 'badge badge-info';
    show($('authCard'));
    hide($('appTabs'));
    hide($('btnLogout'));
  }
}

function switchView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.add('hide'));
  $(name).classList.remove('hide');
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const tab = document.querySelector(`.tab[data-view="${name}"]`);
  if (tab) tab.classList.add('active');
}

// Tab switching
document.addEventListener('click', (e) => {
  const t = e.target.closest('.tab');
  if (t) switchView(t.dataset.view);
});

// Connect to server
async function connectServer() {
  try {
    const r = await fetch('/api/connect', { method: 'POST' });
    const j = await r.json();
    connected = !!j.success;
    if (connected) {
      $('connBadge').textContent = 'Connected';
      $('connBadge').className = 'badge badge-success';
    } else {
      $('connBadge').textContent = 'Disconnected';
      $('connBadge').className = 'badge badge-error';
    }
  } catch {
    connected = false;
    $('connBadge').textContent = 'Disconnected';
    $('connBadge').className = 'badge badge-error';
  }
  return connected;
}

// Balance
async function refreshBalance() {
  if (!connected || !currentUser) return;
  try {
    const r = await fetch(`/api/balance/${encodeURIComponent(currentUser)}`);
    const j = await r.json();
    if (j.success) {
      $('balance').textContent = `${Number(j.balance).toFixed(8)} VNC`;
    }
  } catch (e) {
    console.error('Balance error:', e);
  }
}

// History
async function loadHistory() {
  if (!connected || !currentUser) {
    showAlert('historyLoading', 'Please connect and login first', 'error');
    return;
  }
  
  show($('historyLoading'));
  const hist = $('hist');
  hist.innerHTML = '';
  
  try {
    const r = await fetch(`/api/history/${encodeURIComponent(currentUser)}`);
    const j = await r.json();
    
    hide($('historyLoading'));
    
    if (j.success && Array.isArray(j.history) && j.history.length > 0) {
      j.history.forEach(tx => {
        const div = document.createElement('div');
        const isSent = tx.type === 'sent';
        const isReceived = tx.type === 'received';
        const isMining = tx.type === 'mining' || (!isSent && !isReceived);
        
        let typeClass = isSent ? 'transaction-sent' : isReceived ? 'transaction-received' : 'transaction-mining';
        let sign = isSent ? '-' : '+';
        let amt = Number(tx.amount || 0).toFixed(8);
        let from = tx.from || tx.from_username || '';
        let to = tx.to || tx.to_username || '';
        let status = tx.status || 'confirmed';
        let ts = tx.timestamp || '';
        
        let title = isSent ? `Sent to ${to}` : isReceived ? `Received from ${from}` : 'Mining Reward';
        
        div.className = `transaction-item ${typeClass}`;
        div.innerHTML = `
          <div>
            <div style="font-weight: 700; font-size: 1rem;">${title}</div>
            <div style="color: #6b7280; font-size: 0.85rem; margin-top: 4px;">${status}</div>
            <div style="color: #9ca3af; font-size: 0.8rem; margin-top: 2px;">${ts}</div>
          </div>
          <div class="transaction-amount">${sign}${amt} VNC</div>
        `;
        hist.appendChild(div);
      });
    } else {
      hist.innerHTML = '<div style="text-align: center; color: #6b7280; padding: 40px;">No transactions yet. Start by receiving some coins!</div>';
    }
  } catch (e) {
    hide($('historyLoading'));
    hist.innerHTML = '<div style="text-align: center; color: #ef4444; padding: 40px;">Error loading history. Please try again.</div>';
    console.error('History error:', e);
  }
}

// Auth tabs
$('tabLogin').onclick = () => {
  $('tabLogin').classList.add('active');
  $('tabSignup').classList.remove('active');
  hide($('signupPane'));
  show($('loginPane'));
};

$('tabSignup').onclick = () => {
  $('tabSignup').classList.add('active');
  $('tabLogin').classList.remove('active');
  hide($('loginPane'));
  show($('signupPane'));
};

// Check username
$('btnCheck').onclick = async () => {
  const u = $('suUser').value.trim();
  if (!u) {
    $('checkMsg').textContent = 'Enter username';
    $('checkMsg').className = 'badge badge-warning';
    return;
  }
  
  $('checkMsg').textContent = 'Checking...';
  $('checkMsg').className = 'badge badge-info';
  
  try {
    const r = await fetch(`/api/check_username/${encodeURIComponent(u)}`);
    const j = await r.json();
    if (j.available) {
      $('checkMsg').textContent = 'Available';
      $('checkMsg').className = 'badge badge-success';
    } else {
      $('checkMsg').textContent = 'Taken';
      $('checkMsg').className = 'badge badge-error';
    }
  } catch {
    $('checkMsg').textContent = 'Error checking';
    $('checkMsg').className = 'badge badge-error';
  }
};

// Signup
$('btnSignup').onclick = async () => {
  const u = $('suUser').value.trim();
  const p1 = $('suPass').value;
  const p2 = $('suPass2').value;
  
  if (!u || !p1 || !p2) {
    showAlert('signupAlert', 'All fields are required', 'error');
    return;
  }
  if (p1 !== p2) {
    showAlert('signupAlert', 'Passwords do not match', 'error');
    return;
  }
  if (!connected) {
    showAlert('signupAlert', 'Please connect to server first', 'error');
    return;
  }
  
  try {
    const payload = { username: u, password: p1, word_list: [], hardware_info: getHardwareInfo() };
    const r = await fetch('/api/register', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json();
    
    if (j.success) {
      showAlert('signupAlert', 'Account created! You can now login.', 'success');
      setTimeout(() => {
        $('tabLogin').click();
        $('loginUser').value = u;
      }, 1500);
    } else {
      showAlert('signupAlert', j.message || 'Registration failed', 'error');
    }
  } catch (e) {
    showAlert('signupAlert', 'Registration error: ' + e.message, 'error');
  }
};

// Login
$('btnLogin').onclick = async () => {
  const u = $('loginUser').value.trim();
  const p = $('loginPass').value;
  const remember = $('rememberMe').checked;
  
  if (!u || !p) {
    showAlert('loginAlert', 'Username and password required', 'error');
    return;
  }
  if (!connected) {
    showAlert('loginAlert', 'Please connect to server first', 'error');
    return;
  }
  
  try {
    const payload = { username: u, password: p, word_list: [], hardware_info: getHardwareInfo() };
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const j = await r.json();
    
    if (j.success) {
      localStorage.setItem('vanillacoin_user', u);
      localStorage.setItem('vanillacoin_remember', remember ? '1' : '0');
      currentUser = u;
      showAlert('loginAlert', 'Login successful!', 'success');
      setTimeout(() => {
        setUserUI();
        refreshBalance();
      }, 500);
    } else {
      showAlert('loginAlert', j.message || 'Login failed', 'error');
    }
  } catch (e) {
    showAlert('loginAlert', 'Login error: ' + e.message, 'error');
  }
};

// Logout
$('btnLogout').onclick = () => {
  localStorage.removeItem('vanillacoin_user');
  localStorage.setItem('vanillacoin_remember', '0');
  currentUser = null;
  setUserUI();
  $('tabLogin').click();
};

// Wallet actions
$('btnRefresh').onclick = async () => {
  await refreshBalance();
  showAlert('walletAlert', 'Balance refreshed', 'success');
};

$('btnHistory').onclick = loadHistory;

$('btnSend').onclick = async () => {
  if (!connected) {
    showAlert('sendAlert', 'Please connect to server first', 'error');
    return;
  }
  if (!currentUser) {
    showAlert('sendAlert', 'Please sign in first', 'error');
    return;
  }
  
  const to = $('txTo').value.trim();
  const amt = Number($('txAmt').value);
  
  if (!to || !amt || amt <= 0) {
    showAlert('sendAlert', 'Enter valid recipient and amount', 'error');
    return;
  }
  
  try {
    const r = await fetch('/api/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: currentUser, to, amount: amt })
    });
    const j = await r.json();
    
    if (j.success) {
      showAlert('sendAlert', 'Transaction sent successfully!', 'success');
      $('txTo').value = '';
      $('txAmt').value = '';
      await refreshBalance();
    } else {
      showAlert('sendAlert', j.message || 'Transaction failed', 'error');
    }
  } catch (e) {
    showAlert('sendAlert', 'Error: ' + e.message, 'error');
  }
};

// Airdrop
$('btnAirdrop').onclick = async () => {
  if (!connected) {
    showAlert('walletAlert', 'Please connect to server first', 'error');
    return;
  }
  if (!currentUser) {
    showAlert('walletAlert', 'Please sign in first', 'error');
    return;
  }
  
  try {
    const r = await fetch('/api/airdrop', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to: currentUser, amount: 10 })
    });
    const j = await r.json();
    
    if (j.success) {
      showAlert('walletAlert', 'Received 10 VNC!', 'success');
      await refreshBalance();
    } else {
      showAlert('walletAlert', j.message || 'Airdrop failed', 'error');
    }
  } catch (e) {
    showAlert('walletAlert', 'Error: ' + e.message, 'error');
  }
};

// Mining
let miningStartTime = null;
let simulatedHashRate = 0;
let miningProgressInterval = null;

function updateMiningStats() {
  $('blocksMined').textContent = blocksMined;
  $('miningEarnings').textContent = `${totalEarnings} VNC`;
  
  if (miningActive && miningStartTime) {
    const elapsed = (Date.now() - miningStartTime) / 1000;
    simulatedHashRate = Math.floor(Math.random() * 5000) + 10000;
    $('hashRate').textContent = `${(simulatedHashRate / 1000).toFixed(1)}K H/s`;
  } else {
    $('hashRate').textContent = '0 H/s';
  }
}

$('btnStartMine').onclick = async () => {
  if (!connected) {
    showAlert('mineAlert', 'Please connect to server first', 'error');
    return;
  }
  if (!currentUser) {
    showAlert('mineAlert', 'Please sign in first', 'error');
    return;
  }
  if (miningActive) {
    showAlert('mineAlert', 'Mining already in progress', 'info');
    return;
  }
  
  const seconds = Math.max(1, parseInt($('mineSeconds').value || '10', 10));
  
  miningActive = true;
  miningStartTime = Date.now();
  $('btnStartMine').disabled = true;
  $('btnStopMine').disabled = false;
  $('miningStatusDisplay').textContent = 'Mining';
  $('miningStatusDisplay').style.color = '#10b981';
  
  show($('miningProgress'));
  addTerminalLog('Mining started...', 'success');
  addTerminalLog(`Duration: ${seconds} seconds`, 'info');
  addTerminalLog(`Target: Find valid block hash`, 'info');
  
  // Simulate mining progress
  let progress = 0;
  let attempts = 0;
  miningProgressInterval = setInterval(() => {
    if (!miningActive) {
      clearInterval(miningProgressInterval);
      return;
    }
    
    progress += (100 / (seconds * 10));
    if (progress > 100) progress = 100;
    $('progressFill').style.width = `${progress}%`;
    $('progressPercent').textContent = `${Math.floor(progress)}%`;
    
    // Random mining logs
    attempts++;
    if (attempts % 5 === 0) {
      const randomHash = Math.random().toString(36).substring(2, 15);
      addTerminalLog(`Testing hash: ${randomHash}...`, 'info');
    }
  }, 100);
  
  // Call actual mining API
  try {
    addTerminalLog(`Calling server to mine for ${seconds} seconds...`, 'info');
    const r = await fetch('/api/mine', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: currentUser, seconds })
    });
    const j = await r.json();
    
    // Clean up intervals
    if (miningProgressInterval) {
      clearInterval(miningProgressInterval);
      miningProgressInterval = null;
    }
    
    miningActive = false;
    miningStartTime = null;
    $('btnStartMine').disabled = false;
    $('btnStopMine').disabled = true;
    $('miningStatusDisplay').textContent = 'Idle';
    $('miningStatusDisplay').style.color = '#667eea';
    hide($('miningProgress'));
    
    if (j.success) {
      const found = Number(j.blocks_found || 0);
      blocksMined += found;
      totalEarnings += found * 100;
    
      addTerminalLog(`Mining complete. Blocks found: ${found}`, found > 0 ? 'success' : 'info');
      if (found > 0) addTerminalLog(`Reward: ${found * 100} VNC`, 'success');
    
      showAlert('mineAlert', `Mining finished. Blocks found: ${found}`, found > 0 ? 'success' : 'info');
      await refreshBalance();
    }
    
    updateMiningStats();
  } catch (e) {
    if (miningProgressInterval) {
      clearInterval(miningProgressInterval);
      miningProgressInterval = null;
    }
    miningActive = false;
    miningStartTime = null;
    $('btnStartMine').disabled = false;
    $('btnStopMine').disabled = true;
    hide($('miningProgress'));
    addTerminalLog('Mining error: ' + e.message, 'error');
    showAlert('mineAlert', 'Mining error: ' + e.message, 'error');
  }
};

$('btnStopMine').onclick = () => {
  if (miningActive) {
    miningActive = false;
    miningStartTime = null;
    if (miningProgressInterval) {
      clearInterval(miningProgressInterval);
      miningProgressInterval = null;
    }
    $('btnStartMine').disabled = false;
    $('btnStopMine').disabled = true;
    $('miningStatusDisplay').textContent = 'Stopped';
    hide($('miningProgress'));
    addTerminalLog('Mining stopped by user', 'warning');
  }
};

$('btnClearLogs').onclick = () => {
  $('miningTerminal').innerHTML = '<div class="terminal-line info">Terminal cleared</div>';
};

// Auto update mining stats
setInterval(updateMiningStats, 1000);

// Connect button
$('btnConnect').onclick = async () => {
  await connectServer();
};

// Auto-connect and auto-login
(async () => {
  await connectServer();
  
  const remember = localStorage.getItem('vanillacoin_remember') === '1';
  const storedUser = localStorage.getItem('vanillacoin_user');
  
  if (remember && storedUser) {
    try {
      const payload = { username: storedUser, password: '', word_list: [], hardware_info: { hwid: getHWID() } };
      const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const j = await r.json();
      
      if (j.success) {
        currentUser = storedUser;
        setUserUI();
        await refreshBalance();
      } else {
        localStorage.setItem('vanillacoin_remember', '0');
        currentUser = null;
        setUserUI();
      }
    } catch {
      localStorage.setItem('vanillacoin_remember', '0');
      currentUser = null;
      setUserUI();
    }
  } else {
    currentUser = storedUser || null;
    setUserUI();
    if (currentUser) await refreshBalance();
  }
})();
</script>
</body>
</html>
'''

@app.route("/wallet")
def wallet():
    return HTML_TEMPLATE

if __name__ == "__main__":
    try:
        print("Starting VanillaCoin Web Bridge...")
        print("Open http://127.0.0.1:5000/wallet")
        app.run(debug=True, host="127.0.0.1", port=5000)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        bridge.disconnect()

