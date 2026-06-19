#!/usr/bin/env python3
"""
Missions local server — serves static files + reverse proxies + UCR auto-login.

  POST /ucr-proxy?path=...&qs=...    → UCR
  GET  /jira-proxy?path=...&qs=...   → Jira (PAT via X-Jira-Pat or cookie)
  GET  /ucr-login                    → Opens browser → captures UCR session cookie
  GET  /ucr-cookie                   → Returns last captured UCR cookie as JSON
"""
import http.server, urllib.request, urllib.parse, json, os, threading, webbrowser, time

PORT       = 8080
UCR_BASE   = "https://ucr.cfapps.eu10-004.hana.ondemand.com"
JIRA_BASE  = "https://jira.tools.sap"
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(STATIC_DIR, ".ucr_cookie.json")

# ── UCR auto-login via system browser ────────────────────────────────────────
_pending_cookie = {"value": "", "ts": 0}

def _save_cookie(cookie_str: str):
    _pending_cookie["value"] = cookie_str
    _pending_cookie["ts"] = time.time()
    with open(COOKIE_FILE, "w") as f:
        json.dump(_pending_cookie, f)

def _load_cookie() -> str:
    """Return last saved cookie if < 25 minutes old."""
    try:
        data = json.load(open(COOKIE_FILE))
        if time.time() - data.get("ts", 0) < 25 * 60:
            return data.get("value", "")
    except Exception:
        pass
    return ""

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=STATIC_DIR, **kw)

    def log_message(self, fmt, *args):
        if args and ('-proxy' in str(args[0])):
            print(f"[proxy] {args[0]}")

    def add_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type,X-UCR-Cookie,X-Jira-Cookie,X-Jira-Pat,Cookie")

    def do_OPTIONS(self):
        self.send_response(200)
        self.add_cors()
        self.end_headers()

    def _proxy(self, method):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path.startswith("/ucr-proxy"):
            upstream_base   = UCR_BASE
            cookie_header   = "X-UCR-Cookie"
        elif parsed.path.startswith("/jira-proxy"):
            upstream_base   = JIRA_BASE
            cookie_header   = "X-Jira-Cookie"
        else:
            return False

        # read query string for upstream path + query
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        upstream_path = qs.get("path", ["/uc-authbackend/api/v1/use-case/list"])[0]
        upstream_qs   = qs.get("qs",   [""])[0]

        # get auth: PAT preferred over cookie
        if upstream_base == JIRA_BASE:
            pat = self.headers.get("X-Jira-Pat", "")
            cookie = self.headers.get("X-Jira-Cookie", "") or self.headers.get("Cookie", "")
            req_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if pat:
                req_headers["Authorization"] = f"Bearer {pat}"
            elif cookie:
                req_headers["Cookie"] = cookie
        else:
            cookie = self.headers.get("X-UCR-Cookie", "") or self.headers.get("Cookie", "")
            # fall back to saved cookie from /ucr-capture
            if not cookie:
                cookie = _load_cookie()
            req_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if cookie:
                req_headers["Cookie"] = cookie

        # read request body
        body = b""
        clen = int(self.headers.get("Content-Length", 0))
        if clen:
            body = self.rfile.read(clen)

        try:
            url = upstream_base + upstream_path + (("?" + upstream_qs) if upstream_qs else "")
            req = urllib.request.Request(url, data=body if body else None,
                                         headers=req_headers, method=method)
            # retry up to 3 times on 429
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = resp.read()
                        ct   = resp.headers.get("Content-Type", "application/json")
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:
                        retry_after = int(e.headers.get("Retry-After", "5"))
                        time.sleep(min(retry_after, 10))
                        continue
                    raise
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.add_cors()
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            body_err = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.add_cors()
            self.end_headers()
            self.wfile.write(body_err)
        except Exception as ex:
            msg = json.dumps({"error": str(ex)}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.add_cors()
            self.end_headers()
            self.wfile.write(msg)
        return True

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # ── UCR cookie capture endpoint ───────────────────────────────────
        if parsed.path == "/ucr-cookie":
            # Return last saved cookie (from /ucr-capture or manual paste)
            cookie = _load_cookie()
            resp = json.dumps({"cookie": cookie, "age": int(time.time() - _pending_cookie["ts"]) if _pending_cookie["ts"] else -1}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.add_cors()
            self.end_headers()
            self.wfile.write(resp)
            return

        # ── UCR login: open browser → user logs in → capture callback ────
        if parsed.path == "/ucr-login":
            # Redirect to UCR; user logs in normally; they then hit /ucr-capture with the cookie
            instructions = """<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>UCR Login Helper</title>
<style>body{font-family:system-ui;background:#0a0f1e;color:#e2e8f0;padding:32px;max-width:560px;margin:0 auto}
h2{color:#22d3ee}code{background:#1e293b;padding:2px 7px;border-radius:4px;color:#a78bfa}
.btn{display:inline-block;margin-top:16px;padding:10px 24px;background:#0070f2;color:#fff;
  text-decoration:none;border-radius:8px;font-weight:600}
.step{margin:12px 0;padding:12px 16px;background:#1e293b;border-radius:8px;border-left:3px solid #22d3ee}
</style></head><body>
<h2>⚡ UCR Session Cookie Capture</h2>
<div class="step"><b>Step 1:</b> <a href="https://ucr.cfapps.eu10-004.hana.ondemand.com/uc-authbackend/api/v1/use-case/list" target="_blank" class="btn">Open UCR API directly ↗</a><br>
This will redirect you to SAP SSO login. Log in with your SAP credentials.</div>
<div class="step"><b>Step 2:</b> After login you see JSON in the browser. Now open DevTools (F12) →
<b>Application</b> tab → <b>Cookies</b> → click <code>ucr.cfapps.eu10-004.hana.ondemand.com</code> →
copy the values of <code>JSESSIONID</code> and <code>__VCAP_ID__</code>, then paste them below as:<br><br>
<code>JSESSIONID=&lt;value&gt;; __VCAP_ID__=&lt;value&gt;</code></div>
<div class="step"><b>Step 3:</b> Paste the cookie below and click Save:
<br><br>
<textarea id="c" style="width:100%;height:80px;background:#0f172a;color:#94a3b8;border:1px solid #334155;
  border-radius:6px;padding:8px;font-family:monospace;font-size:.75rem" placeholder="Paste cookie here..."></textarea>
<br><button onclick="save()" style="margin-top:8px;padding:8px 20px;background:#22c55e;color:#fff;
  border:none;border-radius:6px;font-weight:600;cursor:pointer;font-size:.85rem">Save Cookie ✓</button>
<span id="msg" style="margin-left:10px;color:#4ade80"></span>
</div>
<script>
function save(){
  var c=document.getElementById('c').value.trim();
  if(!c){document.getElementById('msg').textContent='⚠ Empty';return;}
  fetch('/ucr-capture',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({cookie:c})})
  .then(r=>r.json()).then(d=>{
    document.getElementById('msg').textContent='✅ Saved! You can close this tab.';
  }).catch(()=>document.getElementById('msg').textContent='❌ Error');
}
</script>
</body></html>"""
            data = instructions.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.add_cors()
            self.end_headers()
            self.wfile.write(data)
            return

        if not self._proxy("GET"):
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)

        # ── UCR cookie save from login helper page ────────────────────────
        if parsed.path == "/ucr-capture":
            clen = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(clen)) if clen else {}
            cookie = body.get("cookie", "").strip()
            if cookie:
                _save_cookie(cookie)
                print(f"[ucr-capture] Cookie saved ({len(cookie)} chars)")
            resp = json.dumps({"ok": bool(cookie)}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.add_cors()
            self.end_headers()
            self.wfile.write(resp)
            return

        if not self._proxy("POST"):
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    os.chdir(STATIC_DIR)
    server = http.server.HTTPServer(("", PORT), Handler)
    print(f"Missions server running on http://localhost:{PORT}")
    print(f"UCR proxy at http://localhost:{PORT}/ucr-proxy")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
