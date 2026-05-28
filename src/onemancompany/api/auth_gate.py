"""Optional login gate for the AutoResearch web UI.

Disabled by default: with ``AUTH_ENABLED`` unset (or != "1") this module is a
complete no-op, so local development and `localhost:8000` testing behave exactly
as before — no login required.

When ``AUTH_ENABLED=1`` it gates the whole site behind a verification-code login
backed by an external auth service (Yangtze-Cloud style API):

  - GET  /login            -> login page (verification code)
  - POST /auth/code/send   -> proxied to <backend>/api/user/code/send
  - POST /auth/login       -> proxied to <backend>/api/user/login; on success the
                              JWT is stored in an HttpOnly, same-origin cookie
  - GET  /auth/logout      -> clears the cookie, back to /login
  - GET  /auth/status      -> {"enabled": true} (lets the UI show a logout button)

The backend client credentials are injected server-side from env, so the secret
never reaches the browser. Wired in by main.py via ``install_auth_gate(app)``.

Env:
  AUTH_ENABLED=1
  AUTH_CLIENT_ID=...        # a registered OAuth2 client id
  AUTH_CLIENT_SECRET=...    # its secret (kept server-side)
  AUTH_BACKEND=https://auth.example.com   # auth service base URL
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request

from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

COOKIE = "mmt_at"
ALLOW_PREFIXES = ("/login", "/auth/")


def _enabled() -> bool:
    return os.environ.get("AUTH_ENABLED", "0") == "1"


def _backend() -> str:
    return os.environ.get("AUTH_BACKEND", "").rstrip("/")


def _client_headers() -> dict:
    return {
        "X-Client-Id": os.environ.get("AUTH_CLIENT_ID", ""),
        "X-Client-Secret": os.environ.get("AUTH_CLIENT_SECRET", ""),
    }


def _forward(method: str, path: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(_backend() + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        if v:
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        return 502, json.dumps({"success": False, "message": f"auth proxy error: {e}"}).encode()


def current_user_id(request) -> str:
    """Best-effort decode of the logged-in user's id from the auth cookie.

    Returns "" when there is no cookie / it cannot be decoded. Used to attribute
    a launched pipeline to its owner for per-user LLM dispatch (see core.user_llm).
    """
    token = request.cookies.get(COOKIE, "")
    if not token:
        return ""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return str(claims.get("userId") or claims.get("uid") or claims.get("sub") or "")
    except Exception as e:
        logger.debug("[auth_gate] could not decode user id from cookie: {}", e)
        return ""


def _token_valid(token: str) -> bool:
    """Presence + expiry check on the JWT (no signature verification)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims.get("exp", 0)) > time.time()
    except Exception:
        # Malformed / undecodable token → treat as invalid; caller redirects to login.
        return False


async def _login_page(request):
    return HTMLResponse(LOGIN_HTML)


async def _status(request):
    return JSONResponse({"enabled": True})


async def _send_code(request):
    status, data = _forward("POST", "/api/user/code/send", _client_headers(), await request.body())
    return Response(data, status_code=status, media_type="application/json")


async def _do_login(request):
    status, data = _forward("POST", "/api/user/login", _client_headers(), await request.body())
    resp = Response(data, status_code=status, media_type="application/json")
    try:
        j = json.loads(data)
        token = (j.get("data") or {}).get("accessToken")
        if j.get("success") and token:
            resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                            max_age=604800, path="/")
    except Exception as e:
        # Backend returned non-JSON / unexpected shape → forward the raw response
        # as-is without setting a cookie; the client surfaces the error message.
        logger.debug("[auth_gate] login response not JSON-decodable: {}", e)
    return resp


async def _do_logout(request):
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE, path="/")
    return resp


class _AuthGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if path.startswith(ALLOW_PREFIXES) or path == "/favicon.ico":
            return await call_next(request)
        # Optional localhost bypass (OMC_TRUST_LOCALHOST=1): requests originating
        # on the server itself (127.0.0.1/::1) skip the login gate. Lets
        # server-side automation (e.g. headless full-auto pipeline runs) hit the
        # API without a login cookie; external clients are unaffected.
        if os.environ.get("OMC_TRUST_LOCALHOST") == "1":
            client_host = request.client.host if request.client else ""
            if client_host in ("127.0.0.1", "::1", "localhost"):
                return await call_next(request)
        token = request.cookies.get(COOKIE, "")
        if token and _token_valid(token):
            return await call_next(request)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=302)
        return JSONResponse({"detail": "unauthorized"}, status_code=401)


def install_auth_gate(app) -> None:
    """Install the login gate on ``app`` — no-op unless AUTH_ENABLED=1.

    Routes are prepended so they win over the catch-all static mount at "/".
    """
    if not _enabled():
        return
    routes = [
        Route("/login", _login_page, methods=["GET"]),
        Route("/auth/status", _status, methods=["GET"]),
        Route("/auth/code/send", _send_code, methods=["POST"]),
        Route("/auth/login", _do_login, methods=["POST"]),
        Route("/auth/logout", _do_logout, methods=["GET", "POST"]),
    ]
    for r in reversed(routes):
        app.router.routes.insert(0, r)
    app.add_middleware(_AuthGateMiddleware)
    print(f"[auth_gate] enabled — backend={_backend() or '(AUTH_BACKEND unset!)'} "
          f"client={os.environ.get('AUTH_CLIENT_ID') or '(AUTH_CLIENT_ID unset!)'}")


LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>登录 · AutoResearch</title><style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:linear-gradient(135deg,#1e3a8a,#0f172a);color:#e5e7eb}
.card{width:360px;background:#111827;border:1px solid #1f2937;border-radius:16px;padding:32px;box-shadow:0 20px 60px rgba(0,0,0,.4)}
h1{margin:0 0 4px;font-size:20px}.sub{margin:0 0 24px;font-size:13px;color:#9ca3af}
label{display:block;font-size:12px;color:#9ca3af;margin:14px 0 6px}
input{width:100%;padding:11px 12px;border-radius:9px;border:1px solid #374151;background:#0b1220;color:#e5e7eb;font-size:14px}
input:focus{outline:none;border-color:#3b82f6}.row{display:flex;gap:8px}.row input{flex:1}
button{border:none;border-radius:9px;cursor:pointer;font-size:14px;font-weight:600}
.btn-code{padding:0 14px;background:#1f2937;color:#93c5fd;white-space:nowrap}.btn-code:disabled{opacity:.5;cursor:not-allowed}
.btn-login{width:100%;padding:12px;margin-top:22px;background:#3b82f6;color:#fff}.btn-login:hover{background:#2563eb}
.msg{margin-top:14px;font-size:13px;min-height:18px}.msg.ok{color:#34d399}.msg.err{color:#f87171}
</style></head><body><div class="card">
<h1>登录 / 注册</h1><p class="sub">验证码登录 · AutoResearch</p>
<label>账号（手机号或邮箱）</label>
<input id="account" type="text" placeholder="手机号 或 邮箱" autocomplete="username">
<label>验证码</label>
<div class="row"><input id="code" type="text" placeholder="6 位验证码" autocomplete="one-time-code">
<button id="sendBtn" class="btn-code" onclick="sendCode()">发送验证码</button></div>
<button class="btn-login" onclick="login()">登 录</button>
<div id="msg" class="msg"></div></div>
<script>
let cd=0;const $=i=>document.getElementById(i);
function setMsg(t,ok){const m=$('msg');m.textContent=t;m.className='msg '+(ok?'ok':'err');}
async function post(p,b){const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});return r.json();}
async function sendCode(){const a=$('account').value.trim();if(!a)return setMsg('请先填写账号',false);
$('sendBtn').disabled=true;try{const r=await post('/auth/code/send',{account:a});
if(r.success){setMsg('验证码已发送，请查收',true);cd=60;tick();}else{setMsg(r.message||'发送失败',false);$('sendBtn').disabled=false;}}
catch(e){setMsg('网络错误：'+e.message,false);$('sendBtn').disabled=false;}}
function tick(){if(cd<=0){$('sendBtn').disabled=false;$('sendBtn').textContent='发送验证码';return;}$('sendBtn').textContent=cd--+'s';setTimeout(tick,1000);}
async function login(){const a=$('account').value.trim(),c=$('code').value.trim();
if(!a||!c)return setMsg('请填写账号和验证码',false);
try{const r=await post('/auth/login',{account:a,code:c});
if(r.success&&r.data){setMsg('登录成功！正在进入 AutoResearch…',true);setTimeout(()=>location.href='/',600);}
else setMsg(r.message||'登录失败',false);}catch(e){setMsg('网络错误：'+e.message,false);}}
</script></body></html>"""
