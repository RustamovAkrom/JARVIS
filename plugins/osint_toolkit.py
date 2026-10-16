"""osint_toolkit.py — basic open-source OSINT toolkit for JARVIS.

Uses modern maintained tools when available (Sherlock, Holehe, PhoneInfoga,
phonenumbers). Falls back to public endpoints so it always works without
API keys or logins.

Settings: plugin config namespace `osint_toolkit` (same pattern as telegram_remote).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

try:
    from memory.config_manager import get_plugin_config, save_plugin_config
    _HAS_CONFIG = True
except ImportError:
    _HAS_CONFIG = False

    def get_plugin_config(namespace):  # type: ignore
        return {}

    def save_plugin_config(namespace, values):  # type: ignore
        raise RuntimeError("no plugin settings store")


_NS = "osint_toolkit"
_UA = "JARVIS-OSINT/1.1 (+https://github.com/RustamovAkrom/JARVIS)"
_TIMEOUT = 10

_DEFAULTS = {
    "timeout_sec": 10,
    "sherlock_timeout": 45,
    "holehe_timeout": 60,
    "phoneinfoga_timeout": 40,
    "max_username_fallback": 16,
    "use_sherlock": True,
    "use_holehe": True,
    "use_phoneinfoga": True,
    "use_phonenumbers": True,
    "enable_username": True,
    "enable_email": True,
    "enable_phone": True,
    "enable_ip": True,
    "enable_domain": True,
    "enable_github": True,
    "user_agent": _UA,
    "default_region": "UZ",  # for local phone numbers without +
}


def _cfg() -> dict:
    raw = get_plugin_config(_NS) if _HAS_CONFIG else {}
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        out.update({k: raw[k] for k in _DEFAULTS if k in raw})
    return out


PLUGIN = {
    "name": "osint_toolkit",
    "description": (
        "Open-source intelligence (OSINT) toolkit. "
        "Investigate a username, email, phone number, IP, domain or GitHub account "
        "using public sources and optional local tools (Sherlock, Holehe, PhoneInfoga). "
        "No API keys or logins required. "
        "Prefer action=investigate for a full automatic report."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": [
                    "investigate",
                    "username",
                    "email",
                    "phone",
                    "ip",
                    "domain",
                    "dns",
                    "github",
                    "status",
                ],
                "description": (
                    "investigate = auto multi-tool report. "
                    "username / email / phone / ip / domain / dns / github = single tool. "
                    "status = settings and available engines."
                ),
            },
            "target": {
                "type": "STRING",
                "description": (
                    "Username, email, phone (+998...), IP, domain or GitHub login. "
                    "Required except for status."
                ),
            },
        },
        "required": ["action"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "SILENT",
}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _get(url: str, timeout: float = _TIMEOUT) -> tuple[int, str, dict]:
    h = {"User-Agent": _cfg().get("user_agent") or _UA, "Accept": "*/*"}
    try:
        req = urllib.request.Request(url, headers=h, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, dict(e.headers) if e.headers else {}
    except Exception:
        return 0, "", {}


def _json_get(url: str, timeout: float = _TIMEOUT) -> Optional[dict]:
    code, body, _ = _get(url, timeout=timeout)
    if code != 200 or not body:
        return None
    try:
        return json.loads(body)
    except Exception:
        return None


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _run_cmd(args: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            creationflags=0x08000000 if __import__("sys").platform == "win32" else 0,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -2, "", "not found"
    except Exception as e:
        return -3, "", str(e)


# ── Username (Sherlock → fallback) ───────────────────────────────────────────

_FALLBACK_PLATFORMS = [
    ("GitHub", "https://github.com/{}"),
    ("GitLab", "https://gitlab.com/{}"),
    ("Reddit", "https://www.reddit.com/user/{}"),
    ("Telegram", "https://t.me/{}"),
    ("Steam", "https://steamcommunity.com/id/{}"),
    ("Twitch", "https://www.twitch.tv/{}"),
    ("Pinterest", "https://www.pinterest.com/{}"),
    ("TikTok", "https://www.tiktok.com/@{}"),
    ("Medium", "https://medium.com/@{}"),
    ("Dev.to", "https://dev.to/{}"),
    ("HackerNews", "https://news.ycombinator.com/user?id={}"),
    ("Keybase", "https://keybase.io/{}"),
    ("About.me", "https://about.me/{}"),
    ("Linktree", "https://linktr.ee/{}"),
    ("Chess.com", "https://www.chess.com/member/{}"),
    ("Duolingo", "https://www.duolingo.com/profile/{}"),
]


def tool_username(target: str, cfg: dict) -> dict:
    user = re.sub(r"[^a-zA-Z0-9_\-\.]", "", target.strip())[:40]
    if len(user) < 2:
        return {"error": "Invalid username", "target": target}

    # Prefer Sherlock
    if cfg.get("use_sherlock") and _which("sherlock"):
        code, out, err = _run_cmd(
            [
                "sherlock", user,
                "--print-found",
                "--no-color",
                "--timeout", str(min(15, int(cfg.get("timeout_sec") or 10))),
            ],
            timeout=float(cfg.get("sherlock_timeout") or 45),
        )
        found = []
        for line in (out or "").splitlines():
            line = line.strip()
            if line.startswith("[+]") or "http" in line.lower():
                # formats vary: [+] GitHub: https://...
                m = re.search(r"(https?://\S+)", line)
                if m:
                    url = m.group(1).rstrip(".,)")
                    name = line.split(":")[0].replace("[+]", "").strip() or "site"
                    found.append({"platform": name, "url": url})
        if found or code == 0:
            return {
                "target": user,
                "engine": "sherlock",
                "found": found,
                "found_count": len(found),
                "note": "Sherlock (local). Install: pip install sherlock-project",
            }

    # Lightweight fallback
    max_n = int(cfg.get("max_username_fallback") or 16)
    platforms = _FALLBACK_PLATFORMS[:max_n]
    found, missing = [], []

    def check(item):
        name, tmpl = item
        url = tmpl.format(user)
        code, body, _ = _get(url, timeout=float(cfg.get("timeout_sec") or 8))
        if code == 200:
            low = body.lower()
            if any(x in low for x in ("not found", "doesn't exist", "page not found", "404")):
                return name, url, False
            return name, url, True
        return name, url, False if code else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        for fut in as_completed({ex.submit(check, p): p for p in platforms}):
            name, url, ok = fut.result()
            if ok is True:
                found.append({"platform": name, "url": url})
            elif ok is False:
                missing.append(name)

    return {
        "target": user,
        "engine": "fallback",
        "found": found,
        "found_count": len(found),
        "checked": len(platforms),
        "not_found_sample": missing[:12],
        "note": "Install Sherlock for 400+ sites: pip install sherlock-project",
    }


# ── Email (Holehe → MX) ──────────────────────────────────────────────────────

def tool_email(target: str, cfg: dict) -> dict:
    email = target.strip().lower()
    if not re.match(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", email):
        return {"error": "Invalid email format", "target": target}

    local, domain = email.rsplit("@", 1)
    result: dict[str, Any] = {
        "email": email,
        "local_part": local,
        "domain": domain,
        "valid_format": True,
    }

    # Holehe
    if cfg.get("use_holehe") and _which("holehe"):
        code, out, err = _run_cmd(
            ["holehe", email, "--only-used", "--no-color"],
            timeout=float(cfg.get("holehe_timeout") or 60),
        )
        sites = []
        for line in (out or "").splitlines():
            line = line.strip()
            # typical: [+] github or "github: true"
            if line.startswith("[+]") or ("used" in line.lower() and ":" in line):
                name = re.sub(r"^\[\+\]\s*", "", line).split(":")[0].strip()
                if name and len(name) < 40:
                    sites.append(name)
        result["engine"] = "holehe"
        result["registered_on"] = sites
        result["registered_count"] = len(sites)
        result["note"] = "Holehe checks registration via public endpoints (no alert to target)."
    else:
        result["engine"] = "mx"
        result["note"] = "Install Holehe for site registration check: pip install holehe"

    # MX always
    dns = tool_dns(domain, cfg)
    mx = (dns.get("records") or {}).get("MX") or []
    result["mx_records"] = mx
    result["has_mx"] = bool(mx)
    return result


# ── Phone (phonenumbers + PhoneInfoga) ───────────────────────────────────────

def tool_phone(target: str, cfg: dict) -> dict:
    raw = target.strip()
    # keep digits and leading +
    cleaned = re.sub(r"[^\d+]", "", raw)
    if cleaned.count("+") > 1:
        cleaned = cleaned.replace("+", "")
        cleaned = "+" + cleaned
    if not cleaned or len(re.sub(r"\D", "", cleaned)) < 7:
        return {"error": "Invalid phone number", "target": target}

    result: dict[str, Any] = {"input": raw, "normalized": cleaned}

    # Offline: phonenumbers
    if cfg.get("use_phonenumbers"):
        try:
            import phonenumbers
            from phonenumbers import carrier, geocoder, timezone
            from phonenumbers import PhoneNumberFormat, NumberParseException

            region = cfg.get("default_region") or "UZ"
            try:
                num = phonenumbers.parse(cleaned, None if cleaned.startswith("+") else region)
            except NumberParseException:
                num = phonenumbers.parse(cleaned, region)

            result["engine"] = "phonenumbers"
            result["valid"] = phonenumbers.is_valid_number(num)
            result["possible"] = phonenumbers.is_possible_number(num)
            result["e164"] = phonenumbers.format_number(num, PhoneNumberFormat.E164)
            result["international"] = phonenumbers.format_number(num, PhoneNumberFormat.INTERNATIONAL)
            result["national"] = phonenumbers.format_number(num, PhoneNumberFormat.NATIONAL)
            result["country_code"] = num.country_code
            result["region"] = phonenumbers.region_code_for_number(num)
            result["location"] = geocoder.description_for_number(num, "en") or None
            result["carrier"] = carrier.name_for_number(num, "en") or None
            try:
                result["timezones"] = list(timezone.time_zones_for_number(num) or [])
            except Exception:
                result["timezones"] = []
            ntype = phonenumbers.number_type(num)
            type_map = {
                0: "fixed_line",
                1: "mobile",
                2: "fixed_or_mobile",
                3: "toll_free",
                4: "premium_rate",
                5: "shared_cost",
                6: "voip",
                7: "personal",
                8: "pager",
                9: "uan",
                10: "voicemail",
            }
            result["line_type"] = type_map.get(ntype, str(ntype))
        except ImportError:
            result["phonenumbers"] = "not installed (pip install phonenumbers)"
        except Exception as e:
            result["phonenumbers_error"] = str(e)

    # Optional PhoneInfoga (local scanner works without keys)
    if cfg.get("use_phoneinfoga") and _which("phoneinfoga"):
        number = result.get("e164") or cleaned
        code, out, err = _run_cmd(
            ["phoneinfoga", "scan", "-n", number],
            timeout=float(cfg.get("phoneinfoga_timeout") or 40),
        )
        if out:
            result["phoneinfoga_raw"] = out.strip()[:2000]
            result["phoneinfoga"] = "ok" if code == 0 else f"exit {code}"

    # Free Google dork links (no request from us)
    q = urllib.parse.quote(result.get("e164") or cleaned)
    result["osint_links"] = {
        "google": f"https://www.google.com/search?q=%22{q}%22",
        "google_social": f"https://www.google.com/search?q=site:facebook.com+OR+site:linkedin.com+OR+site:twitter.com+%22{q}%22",
        "truecaller_web": "https://www.truecaller.com/",  # manual
    }
    result["note"] = (
        "Phone data is offline (libphonenumber) + optional PhoneInfoga. "
        "No paid APIs. Carrier reflects original allocation; number portability may differ."
    )
    return result


# ── IP / DNS / Domain / GitHub ───────────────────────────────────────────────

def tool_ip(target: str, cfg: dict) -> dict:
    ip = target.strip()
    if not re.match(r"^[\da-fA-F:\.]+$", ip):
        return {"error": "Invalid IP", "target": target}
    data = _json_get(
        f"http://ip-api.com/json/{urllib.parse.quote(ip)}"
        f"?fields=status,message,country,countryCode,regionName,city,zip,"
        f"lat,lon,timezone,isp,org,as,query,mobile,proxy,hosting",
        timeout=float(cfg.get("timeout_sec") or 10),
    )
    if not data or data.get("status") != "success":
        return {"error": (data or {}).get("message") or "Lookup failed", "target": ip}
    return {
        "ip": data.get("query") or ip,
        "country": data.get("country"),
        "region": data.get("regionName"),
        "city": data.get("city"),
        "zip": data.get("zip"),
        "lat": data.get("lat"),
        "lon": data.get("lon"),
        "timezone": data.get("timezone"),
        "isp": data.get("isp"),
        "org": data.get("org"),
        "as": data.get("as"),
        "mobile": data.get("mobile"),
        "proxy": data.get("proxy"),
        "hosting": data.get("hosting"),
        "engine": "ip-api.com",
    }


def tool_dns(target: str, cfg: dict) -> dict:
    host = re.sub(r"^https?://", "", target.strip().lower()).split("/")[0].rstrip(".")
    if not host or " " in host:
        return {"error": "Invalid hostname", "target": target}
    records = {}
    for rtype in ("A", "AAAA", "MX", "NS", "TXT", "CNAME"):
        data = _json_get(
            f"https://dns.google/resolve?name={urllib.parse.quote(host)}&type={rtype}",
            timeout=float(cfg.get("timeout_sec") or 10),
        )
        if not data:
            continue
        vals = [a.get("data") for a in (data.get("Answer") or []) if a.get("data")]
        if vals:
            records[rtype] = vals
    return {"host": host, "records": records, "engine": "dns.google"}


def tool_domain(target: str, cfg: dict) -> dict:
    host = re.sub(r"^https?://", "", target.strip().lower()).split("/")[0].rstrip(".")
    if not host:
        return {"error": "Invalid domain", "target": target}
    result: dict[str, Any] = {"domain": host, "dns": tool_dns(host, cfg).get("records") or {}}

    rdap = _json_get(f"https://rdap.org/domain/{urllib.parse.quote(host)}", timeout=12)
    if rdap:
        result["rdap"] = {
            "handle": rdap.get("handle"),
            "ldhName": rdap.get("ldhName"),
            "status": rdap.get("status"),
            "nameservers": [
                ns.get("ldhName") or ns.get("unicodeName")
                for ns in (rdap.get("nameservers") or [])
                if isinstance(ns, dict)
            ],
            "events": [
                {"action": e.get("eventAction"), "date": e.get("eventDate")}
                for e in (rdap.get("events") or [])
                if isinstance(e, dict) and e.get("eventAction")
            ][:6],
        }

    # crt.sh subdomains (limited)
    crt = _json_get(
        f"https://crt.sh/?q=%25.{urllib.parse.quote(host)}&output=json",
        timeout=12,
    )
    if isinstance(crt, list):
        names = set()
        for row in crt[:80]:
            name = (row.get("name_value") or "").strip().lower()
            for part in name.split("\n"):
                part = part.strip().lstrip("*.")
                if part.endswith(host) and " " not in part:
                    names.add(part)
        if names:
            result["subdomains_sample"] = sorted(names)[:25]

    for scheme in ("https", "http"):
        code, _, hdrs = _get(f"{scheme}://{host}/", timeout=6)
        if code:
            result["http"] = {
                "scheme": scheme,
                "status": code,
                "server": hdrs.get("Server") or hdrs.get("server"),
            }
            break
    return result


def tool_github(target: str, cfg: dict) -> dict:
    user = re.sub(r"[^a-zA-Z0-9\-]", "", target.strip())[:39]
    if not user:
        return {"error": "Invalid GitHub username", "target": target}
    data = _json_get(f"https://api.github.com/users/{urllib.parse.quote(user)}", timeout=10)
    if not data or "message" in data:
        return {"error": (data or {}).get("message") or "Not found", "target": user}
    return {
        "login": data.get("login"),
        "name": data.get("name"),
        "type": data.get("type"),
        "bio": data.get("bio"),
        "company": data.get("company"),
        "blog": data.get("blog"),
        "location": data.get("location"),
        "email": data.get("email"),
        "twitter": data.get("twitter_username"),
        "public_repos": data.get("public_repos"),
        "followers": data.get("followers"),
        "following": data.get("following"),
        "created_at": data.get("created_at"),
        "html_url": data.get("html_url"),
        "engine": "api.github.com",
    }


# ── Auto investigate ─────────────────────────────────────────────────────────

def _detect_type(target: str) -> str:
    t = target.strip()
    if re.match(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$", t, re.I):
        return "email"
    # phone: mostly digits, optional +, length 8–15
    digits = re.sub(r"\D", "", t)
    if (t.startswith("+") or digits.startswith("998") or digits.startswith("7")) and 8 <= len(digits) <= 15:
        if re.match(r"^[\d\s\-\+\(\)]+$", t):
            return "phone"
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", t) or (":" in t and re.match(r"^[\da-fA-F:]+$", t)):
        return "ip"
    if "." in t and " " not in t and re.match(r"^[a-z0-9.\-]+\.[a-z]{2,}$", t, re.I):
        return "domain"
    return "username"


def tool_investigate(target: str, cfg: dict) -> dict:
    kind = _detect_type(target)
    report: dict[str, Any] = {
        "target": target.strip(),
        "detected_type": kind,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "tools_run": [],
    }

    def add(name, enabled, fn, *args):
        if not cfg.get(enabled, True):
            return
        report["tools_run"].append(name)
        report[name] = fn(*args)

    if kind == "email":
        add("email", "enable_email", tool_email, target, cfg)
        domain = target.split("@")[-1]
        add("domain", "enable_domain", tool_domain, domain, cfg)
        local = target.split("@")[0]
        add("username", "enable_username", tool_username, local, cfg)
        add("github", "enable_github", tool_github, local, cfg)
    elif kind == "phone":
        add("phone", "enable_phone", tool_phone, target, cfg)
    elif kind == "ip":
        add("ip", "enable_ip", tool_ip, target, cfg)
    elif kind == "domain":
        add("domain", "enable_domain", tool_domain, target, cfg)
    else:
        add("username", "enable_username", tool_username, target, cfg)
        add("github", "enable_github", tool_github, target, cfg)
    return report


# ── Format for voice / chat ──────────────────────────────────────────────────

def _fmt(report: dict) -> str:
    if report.get("error") and len(report) <= 3:
        return f"Error: {report['error']}"

    lines: list[str] = []
    if "detected_type" in report:
        lines.append(f"OSINT «{report.get('target')}» ({report.get('detected_type')})")
        lines.append(f"Tools: {', '.join(report.get('tools_run') or [])}")
        lines.append("")

    def block_username(b: dict):
        if not isinstance(b, dict) or "found" not in b:
            return
        lines.append(f"Username «{b.get('target')}» [{b.get('engine')}] — {b.get('found_count', 0)} hits")
        for f in (b.get("found") or [])[:20]:
            lines.append(f"  • {f.get('platform')}: {f.get('url')}")
        lines.append("")

    def block_email(b: dict):
        if not isinstance(b, dict) or not b.get("email"):
            return
        lines.append(f"Email {b['email']} [{b.get('engine')}]")
        lines.append(f"  MX: {'yes' if b.get('has_mx') else 'no'}")
        if b.get("registered_on"):
            lines.append(f"  Registered on ({b.get('registered_count')}): {', '.join(b['registered_on'][:15])}")
        lines.append("")

    def block_phone(b: dict):
        if not isinstance(b, dict):
            return
        if b.get("error"):
            lines.append(f"Phone error: {b['error']}")
            return
        lines.append(f"Phone {b.get('e164') or b.get('normalized') or b.get('input')}")
        if b.get("valid") is not None:
            lines.append(f"  Valid: {b.get('valid')}  ·  Type: {b.get('line_type') or '—'}")
        if b.get("location") or b.get("region"):
            lines.append(f"  Location: {b.get('location') or b.get('region')}")
        if b.get("carrier"):
            lines.append(f"  Carrier: {b.get('carrier')}")
        if b.get("timezones"):
            lines.append(f"  TZ: {', '.join(b['timezones'][:3])}")
        if b.get("international"):
            lines.append(f"  Format: {b.get('international')}")
        links = b.get("osint_links") or {}
        if links.get("google"):
            lines.append(f"  Google dork: {links['google']}")
        lines.append("")

    def block_ip(b: dict):
        if not isinstance(b, dict) or not b.get("ip"):
            return
        lines.append(f"IP {b['ip']}")
        lines.append(f"  {b.get('city')}, {b.get('region')}, {b.get('country')}")
        lines.append(f"  ISP: {b.get('isp')}  ·  Org: {b.get('org')}")
        flags = [k for k in ("proxy", "hosting", "mobile") if b.get(k)]
        if flags:
            lines.append(f"  Flags: {', '.join(flags)}")
        lines.append("")

    def block_domain(b: dict):
        if not isinstance(b, dict) or not b.get("domain"):
            return
        lines.append(f"Domain {b['domain']}")
        rdap = b.get("rdap") or {}
        if rdap.get("status"):
            st = rdap["status"]
            lines.append(f"  Status: {', '.join(st) if isinstance(st, list) else st}")
        if rdap.get("nameservers"):
            lines.append(f"  NS: {', '.join(rdap['nameservers'][:4])}")
        for ev in (rdap.get("events") or [])[:3]:
            lines.append(f"  {ev.get('action')}: {ev.get('date')}")
        if b.get("subdomains_sample"):
            lines.append(f"  Subdomains (sample): {', '.join(b['subdomains_sample'][:8])}")
        http = b.get("http")
        if http:
            lines.append(f"  HTTP: {http.get('scheme')} → {http.get('status')} ({http.get('server') or '—'})")
        lines.append("")

    def block_github(b: dict):
        if not isinstance(b, dict) or not b.get("login"):
            return
        lines.append(f"GitHub @{b['login']} ({b.get('type')})")
        if b.get("name"):
            lines.append(f"  Name: {b['name']}")
        if b.get("bio"):
            lines.append(f"  Bio: {str(b['bio'])[:100]}")
        lines.append(f"  Repos: {b.get('public_repos')}  ·  Followers: {b.get('followers')}")
        if b.get("html_url"):
            lines.append(f"  {b['html_url']}")
        lines.append("")

    # nested (investigate) or top-level
    for key, fn in (
        ("username", block_username),
        ("email", block_email),
        ("phone", block_phone),
        ("ip", block_ip),
        ("domain", block_domain),
        ("github", block_github),
    ):
        if key in report:
            fn(report[key])
        elif key == "username" and "found" in report:
            fn(report)
        elif key == "phone" and ("e164" in report or "normalized" in report):
            fn(report)
        elif key == "ip" and report.get("ip") and "detected_type" not in report:
            fn(report)
        elif key == "github" and report.get("login") and "detected_type" not in report:
            fn(report)
        elif key == "email" and report.get("email") and "detected_type" not in report:
            fn(report)
        elif key == "domain" and report.get("domain") and "detected_type" not in report:
            fn(report)

    if "records" in report and report.get("records"):
        lines.append(f"DNS {report.get('host')}")
        for rtype, vals in report["records"].items():
            lines.append(f"  {rtype}: {', '.join(vals[:5])}")
        lines.append("")

    if not lines:
        return json.dumps(report, ensure_ascii=False, indent=2)[:2500]
    text = "\n".join(lines).strip()
    return text[:3000] + ("\n… (truncated)" if len(text) > 3000 else "")


# ── Entry ────────────────────────────────────────────────────────────────────

def run(parameters: dict, player=None, session_memory=None) -> str:
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "investigate").strip().lower()
    target = str(params.get("target") or "").strip()
    cfg = _cfg()

    if action == "status":
        engines = []
        if _which("sherlock"):
            engines.append("sherlock")
        if _which("holehe"):
            engines.append("holehe")
        if _which("phoneinfoga"):
            engines.append("phoneinfoga")
        try:
            import phonenumbers  # noqa: F401
            engines.append("phonenumbers")
        except ImportError:
            pass
        return (
            f"OSINT toolkit ready. "
            f"Local engines: {', '.join(engines) or 'none (using public fallbacks)'}. "
            f"Enabled: username={cfg.get('enable_username')}, email={cfg.get('enable_email')}, "
            f"phone={cfg.get('enable_phone')}, ip={cfg.get('enable_ip')}, domain={cfg.get('enable_domain')}. "
            f"Optional install: pip install sherlock-project holehe phonenumbers"
        )

    if not target:
        return "Provide a target: username, email, phone (+998…), IP, domain or GitHub login."

    try:
        if action == "investigate":
            report = tool_investigate(target, cfg)
        elif action == "username":
            if not cfg.get("enable_username"):
                return "Username checks disabled in settings."
            report = tool_username(target, cfg)
        elif action == "email":
            if not cfg.get("enable_email"):
                return "Email checks disabled in settings."
            report = tool_email(target, cfg)
        elif action == "phone":
            if not cfg.get("enable_phone"):
                return "Phone checks disabled in settings."
            report = tool_phone(target, cfg)
        elif action == "ip":
            if not cfg.get("enable_ip"):
                return "IP lookups disabled in settings."
            report = tool_ip(target, cfg)
        elif action == "domain":
            if not cfg.get("enable_domain"):
                return "Domain lookups disabled in settings."
            report = tool_domain(target, cfg)
        elif action == "dns":
            report = tool_dns(target, cfg)
        elif action == "github":
            if not cfg.get("enable_github"):
                return "GitHub lookups disabled in settings."
            report = tool_github(target, cfg)
        else:
            return "Unknown action. Use: investigate, username, email, phone, ip, domain, dns, github, status."

        return _fmt(report)
    except Exception as exc:
        return f"OSINT failed: {type(exc).__name__}: {exc}"
