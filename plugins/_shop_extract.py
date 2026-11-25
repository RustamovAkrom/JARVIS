"""
Reading a shop page, and building a link that puts the product in a cart.

No network here and no knowledge of any one country's shops: everything is read
from what the page itself declares.

WHERE THE PRICE COMES FROM
    Shops publish their products for search engines as schema.org data —
    JSON-LD `Product` / `Offer` blocks, microdata `itemprop="price"`, or
    OpenGraph `product:price:amount`. That markup is the same in every country
    and every language, which is what makes it the right thing to read: a
    scraper written for one shop's HTML breaks on the next shop and on the next
    redesign, a schema.org reader does not care.

    Measured 2026-09-18 on real search results: Trendyol, MediaMarkt TR,
    Turkcell, epey, Logitech, billiger.de and Anker all declared name, price
    and currency this way. Price-comparison sites (akakce, cimri, idealo,
    geizhals) and eBay answered 403 to any non-browser request; Amazon and
    Walmart answered but declare no price markup — those fall through to
    reading the page text with a model, in the plugin.

WHAT "ADD TO CART" CAN MEAN WITHOUT CLICKING
    Some shop PLATFORMS take a product into the cart from a plain address, in
    the user's own browser and session:
        Shopify       /cart/add?id=<variant>&quantity=<n>
        WooCommerce   /?add-to-cart=<product id>&quantity=<n>
        Amazon        /gp/aws/cart/add.html?ASIN.1=<asin>&Quantity.1=<n>&…
                      (every Amazon country; Amazon asks for one confirming click)
    These are recognised from the page, not from a list of shops, so any of the
    millions of shops on those platforms works. Everything else needs a person
    — or the screen agent — to press the shop's own button.

    A Shopify "cart permalink" (/cart/<id>:<n>) is deliberately NOT used: it
    goes straight to checkout, and nothing in this plugin ever goes there.
"""
from __future__ import annotations

import html as _html
import json
import re
import urllib.parse
from typing import Any, Optional

_LD = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
                 re.S | re.I)
_META = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", re.S)


# ─────────────────────────────────────────────────────────────────────────────
# Prices
# ─────────────────────────────────────────────────────────────────────────────

def parse_price(value: Any) -> Optional[float]:
    """A price as a number, from any of the ways shops write one.

        56900.00  ·  "1.299,00"  ·  "1,299.00"  ·  "57 238,06"  ·  "₺4.659"  ·  12
    With both separators present the last one is the decimal point. With only
    one, three digits after it mean thousands ("1.299" is one thousand two
    hundred ninety-nine), anything else means decimals ("12,5").
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    text = re.sub(r"[^\d.,]", "", str(value))
    if not text or not re.search(r"\d", text):
        return None
    if "." in text and "," in text:
        dec = "." if text.rfind(".") > text.rfind(",") else ","
        text = text.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in text or "." in text:
        sep = "," if "," in text else "."
        head, _, tail = text.rpartition(sep)
        if len(tail) == 3 and (text.count(sep) > 1 or len(head.replace(sep, "")) <= 3):
            text = text.replace(sep, "")
        else:
            text = head.replace(sep, "") + "." + tail
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def _availability(value: Any) -> Optional[bool]:
    text = str(value or "").lower()
    if not text:
        return None
    if any(k in text for k in ("instock", "in_stock", "limitedavailability", "onlineonly",
                               "preorder", "presale", "backorder")):
        return True
    if any(k in text for k in ("outofstock", "out_of_stock", "soldout", "discontinued")):
        return False
    return None


def condition(value: Any) -> str:
    """schema.org itemCondition -> new | refurbished | used | damaged | ''."""
    text = str(value or "").lower()
    for key, name in (("refurbished", "refurbished"), ("used", "used"),
                      ("damaged", "damaged"), ("new", "new")):
        if key in text:
            return name
    return ""


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _text(value: Any, limit: int = 200) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("@id") or ""
    value = _first(value)
    if value is None:
        return ""
    return re.sub(r"\s+", " ", _html.unescape(str(value))).strip()[:limit]


def _image(value: Any) -> str:
    value = _first(value)
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl")
    return str(value or "")[:500]


# ─────────────────────────────────────────────────────────────────────────────
# schema.org JSON-LD
# ─────────────────────────────────────────────────────────────────────────────

def _load_ld(block: str) -> Any:
    raw = block.strip()
    for attempt in (raw, _html.unescape(raw),
                    re.sub(r",\s*([}\]])", r"\1", re.sub(r"[\x00-\x1f]", " ", raw))):
        try:
            return json.loads(attempt)
        except Exception:
            continue
    return None


def _types(node: dict) -> set:
    t = node.get("@type")
    t = t if isinstance(t, list) else [t]
    return {str(x).split("/")[-1] for x in t if x}


def _offers_of(product: dict, page_url: str) -> list[dict]:
    """Every priced offer a Product node declares."""
    out = []
    raw = product.get("offers")
    queue = raw if isinstance(raw, list) else [raw]
    while queue:
        offer = queue.pop(0)
        if not isinstance(offer, dict):
            continue
        types = _types(offer)
        nested = offer.get("offers")
        if "AggregateOffer" in types:
            if isinstance(nested, (list, dict)):
                queue.extend(nested if isinstance(nested, list) else [nested])
            low = parse_price(offer.get("lowPrice") or offer.get("price"))
            if low:
                out.append({"price": low, "currency": _text(offer.get("priceCurrency"), 8).upper(),
                            "condition": condition(offer.get("itemCondition")),
                            "in_stock": _availability(offer.get("availability")),
                            "seller": "", "aggregate": True,
                            "offer_count": int(parse_price(offer.get("offerCount")) or 0),
                            "url": page_url, "shipping": None})
            continue
        spec = offer.get("priceSpecification")
        spec = _first(spec) if isinstance(spec, (list, dict)) else None
        price = parse_price(offer.get("price") if offer.get("price") not in (None, "")
                            else (spec or {}).get("price"))
        if not price:
            continue
        currency = _text(offer.get("priceCurrency") or (spec or {}).get("priceCurrency"), 8).upper()
        shipping = None
        details = _first(offer.get("shippingDetails"))
        if isinstance(details, dict):
            rate = details.get("shippingRate")
            if isinstance(rate, dict):
                shipping = parse_price(rate.get("value"))
                if shipping is None and str(rate.get("value")).strip() in ("0", "0.0", "0.00"):
                    shipping = 0.0
        url = offer.get("url") if isinstance(offer.get("url"), str) else ""
        out.append({"price": price, "currency": currency,
                    "condition": condition(offer.get("itemCondition") or product.get("itemCondition")),
                    "tax_included": _tax_included(offer, spec),
                    "in_stock": _availability(offer.get("availability")),
                    "seller": _text(offer.get("seller"), 80), "aggregate": False,
                    "offer_count": 0,
                    "url": urllib.parse.urljoin(page_url, url) if url else page_url,
                    "shipping": shipping})
    return out


def _walk(node: Any, found: list) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(item, found)
        return
    if not isinstance(node, dict):
        return
    types = _types(node)
    if types & {"Product", "ProductGroup", "IndividualProduct", "ProductModel", "Book",
                "SomeProducts", "Vehicle"}:
        found.append(node)
        for variant in node.get("hasVariant") or []:
            if isinstance(variant, dict):
                variant.setdefault("brand", node.get("brand"))
                found.append(variant)
    for key, value in node.items():
        if key in ("hasVariant",):
            continue
        if isinstance(value, (dict, list)):
            _walk(value, found)


def extract_offers(page: str, page_url: str) -> list[dict]:
    """[{name, price, currency, in_stock, seller, url, brand, sku, gtin, image,
    aggregate, offer_count, shipping, source}] — every priced product the page
    declares, from JSON-LD first, microdata and OpenGraph second."""
    results: list[dict] = []
    products: list = []
    for block in _LD.findall(page or ""):
        data = _load_ld(block)
        if data is not None:
            _walk(data, products)
    for product in products:
        name = _text(product.get("name"), 200)
        base = {"name": name, "brand": _text(product.get("brand"), 80),
                "sku": _text(product.get("sku"), 60),
                "gtin": _text(product.get("gtin13") or product.get("gtin") or product.get("gtin12")
                              or product.get("gtin14") or product.get("gtin8"), 20),
                "mpn": _text(product.get("mpn"), 60),
                "image": _image(product.get("image")), "source": "json-ld"}
        for offer in _offers_of(product, page_url):
            results.append({**base, **offer})

    if not results:
        meta = _meta_tags(page)
        price = parse_price(meta.get("product:price:amount") or meta.get("og:price:amount")
                            or _itemprop(page, "price"))
        if price:
            results.append({
                "name": _text(meta.get("og:title") or _title(page), 200),
                "price": price,
                "currency": _text(meta.get("product:price:currency") or meta.get("og:price:currency")
                                  or _itemprop(page, "priceCurrency"), 8).upper(),
                "in_stock": _availability(meta.get("product:availability")
                                          or meta.get("og:availability")
                                          or _itemprop(page, "availability")),
                "seller": "", "url": page_url, "brand": _text(meta.get("product:brand"), 80),
                "sku": "", "gtin": "", "mpn": "", "image": _image(meta.get("og:image")),
                "aggregate": False, "offer_count": 0, "shipping": None,
                "condition": condition(meta.get("product:condition") or meta.get("og:condition")
                                       or _itemprop(page, "itemCondition")),
                "source": "meta"})

    # Duplicates: the same name and price declared twice (ProductGroup + variant).
    seen, unique = set(), []
    for r in results:
        key = (r["name"].casefold(), round(r["price"], 2), r["currency"], r["url"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _meta_tags(page: str) -> dict:
    out = {}
    for tag in _META.findall(page or "")[:400]:
        attrs = {k.lower(): _html.unescape(v) for k, _q, v in _ATTR.findall(tag)}
        key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
        if key and "content" in attrs:
            out.setdefault(key.lower(), attrs["content"])
    return out


def _itemprop(page: str, prop: str) -> str:
    m = re.search(r"itemprop=[\"']" + re.escape(prop) + r"[\"'][^>]*?content=[\"']([^\"']+)",
                  page or "", re.I)
    if not m:
        m = re.search(r"content=[\"']([^\"']+)[\"'][^>]*?itemprop=[\"']" + re.escape(prop) + r"[\"']",
                      page or "", re.I)
    return m.group(1) if m else ""


def _title(page: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", page or "", re.S | re.I)
    return m.group(1) if m else ""


def clean_url(url: str) -> str:
    """The address without tracking and affiliate parameters — the page is the same,
    and a comparison site's referral tag is not the user's business to carry."""
    parts = urllib.parse.urlparse(url or "")
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if not re.match(r"(?i)^(utm_|gclid|fbclid|yclid|msclkid|_ga|mc_|srsltid|tag$|"
                            r"linkcode$|ascsubtag$|aff_|affiliate|partner|clickid)", k)]
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(keep)))


def slug(url: str, limit: int = 100) -> str:
    """The readable part of a product address - where shops often write the
    condition, the capacity or the colour in their own language."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url or "").path or "")
    parts = [p for p in path.split("/") if p]
    text = max(parts, key=len) if parts else ""
    return re.sub(r"[-_+]+", " ", re.sub(r"[.][A-Za-z]{2,5}$", "", text))[:limit]


def _tax_included(offer: dict, spec: Optional[dict]) -> Optional[bool]:
    """schema.org valueAddedTaxIncluded, where a shop declares it at all."""
    for node in (spec or {}, offer):
        value = node.get("valueAddedTaxIncluded") if isinstance(node, dict) else None
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
    return None


def page_text(page: str, limit: int = 6000) -> str:
    """Visible text, roughly — for a model to read when a page declares nothing."""
    body = re.sub(r"(?is)<(script|style|noscript|svg)\b[^>]*>.*?</\1>", " ", page or "")
    body = re.sub(r"(?is)<head\b[^>]*>.*?</head>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", _html.unescape(body)).strip()
    return body[:limit]


def _price_spellings(price: float) -> list[str]:
    """How a page may print this number: 508.53 → 508,53 · 508.53 · 1.299,00 · 1,299.00 · 1 299."""
    whole = int(price)
    cents = int(round((price - whole) * 100))
    grouped_dot = f"{whole:,}".replace(",", ".")
    grouped_comma = f"{whole:,}"
    out = [f"{grouped_dot},{cents:02d}", f"{grouped_comma}.{cents:02d}", f"{whole},{cents:02d}",
           f"{whole}.{cents:02d}", f"{whole:,}".replace(",", " ")]
    if whole >= 1000:
        out.append(grouped_dot)
        out.append(grouped_comma)
    out.append(str(whole))
    return list(dict.fromkeys(out))


def evidence(page: str, price: float, name: str, limit: int = 2600,
             text: Optional[str] = None) -> str:
    """The parts of a page that say what the markup does not: whether the price
    includes tax, whether it is in stock, what shipping costs.

    Not the start of the page: measured on direnc.net, the "508,53 TL + KDV …
    610,23 TL KDV Dahil" block sits past character 20 000, behind the menus, and
    a reader of the first few thousand characters never sees it. So: the text
    around every place the price is printed and around the product's name, plus
    the page's first and last few hundred characters, where shops put their
    shipping banner and footer. No word of any language is looked for.
    """
    text = text if text is not None else page_text(page, 400_000)
    spans: list[tuple[int, int]] = []
    for spelling in _price_spellings(price):
        for m in re.finditer(r"(?<![\d.,])" + re.escape(spelling) + r"(?![\d])", text):
            spans.append((max(0, m.start() - 300), m.end() + 300))
            if len(spans) > 6:
                break
        if len(spans) > 6:
            break
    key = re.sub(r"\s+", " ", name or "").strip()[:28]
    if key:
        for m in list(re.finditer(re.escape(key), text, re.I))[:2]:
            spans.append((max(0, m.start() - 150), m.end() + 450))
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    parts = [text[:350]] + [text[a:b] for a, b in merged] + [text[-500:]]
    joined = " … ".join(p.strip() for p in parts if p.strip())
    return joined[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Searching inside one shop
# ─────────────────────────────────────────────────────────────────────────────

_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
_INPUT = re.compile(r"<input\b[^>]*>", re.I)


def _attr(tag: str, name: str) -> str:
    m = re.search(r"\b" + name + r"\s*=\s*([\"'])(.*?)\1", tag, re.S | re.I)
    return _html.unescape(m.group(2)) if m else ""


def search_url(page: str, base_url: str, query: str) -> str:
    """The shop's own search address for `query`, read from its search form.

    A shop's search box is an HTML form — GET, one visible text or search field,
    maybe some hidden fields — whatever the shop's language or platform. Seen on
    five Turkish electronics shops: /arama?word=, /arama?q=, ?q= on the page
    itself. Forms that take a password or an email are never used.
    """
    best, best_score = "", -1
    for attrs, body in _FORM.findall(page or ""):
        method = (_attr("<f " + attrs, "method") or "get").lower()
        if method != "get":
            continue
        inputs = _INPUT.findall(body)
        types = [(_attr(i, "type") or "text").lower() for i in inputs]
        if any(t in ("password", "email", "tel", "file") for t in types):
            continue
        visible = [(i, t) for i, t in zip(inputs, types) if t in ("text", "search")]
        if len(visible) != 1 or not _attr(visible[0][0], "name"):
            continue
        score = (2 if visible[0][1] == "search" else 0) + (1 if _attr("<f " + attrs, "role") == "search" else 0)
        if "search" in (attrs + visible[0][0]).lower():
            score += 1
        if score > best_score:
            params = []
            for i, t in zip(inputs, types):
                n = _attr(i, "name")
                if not n:
                    continue
                if t == "hidden":
                    params.append((n, _attr(i, "value")))
                elif (i, t) == visible[0]:
                    params.append((n, query))
            action = urllib.parse.urljoin(base_url, _attr("<f " + attrs, "action") or base_url)
            sep = "&" if urllib.parse.urlparse(action).query else "?"
            best, best_score = action + sep + urllib.parse.urlencode(params), score
    return best


def _tokens(text: str) -> set:
    import unicodedata
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return {t for t in re.split(r"[^\w]+", folded) if len(t) >= 2}


def product_links(page: str, base_url: str, query: str, limit: int = 4) -> list[str]:
    """Links on a shop's search-results page that look like the product asked for.

    Scored by the words and model codes shared between the query and the link's
    text and address — "esp32 s3 n16r8" against "ESP32-S3 N16R8 Geliştirme Kartı"
    shares every token, a footer link shares none. Only the shop's own pages.
    """
    host = (urllib.parse.urlparse(base_url).hostname or "").lower().removeprefix("www.")
    want = _tokens(query)
    if not want:
        return []
    scored: dict[str, float] = {}
    for m in re.finditer(r"<a\b([^>]*)>(.*?)</a>", page or "", re.S | re.I):
        href = _attr("<a " + m.group(1), "href")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urllib.parse.urljoin(base_url, href).split("#")[0]
        link_host = (urllib.parse.urlparse(url).hostname or "").lower().removeprefix("www.")
        if link_host != host or url.rstrip("/") == base_url.rstrip("/"):
            continue
        label = re.sub(r"<[^>]+>", " ", m.group(2)) + " " + _attr("<a " + m.group(1), "title")
        have = _tokens(label) | _tokens(urllib.parse.unquote(urllib.parse.urlparse(url).path))
        score = len(want & have) / len(want)
        if score >= 0.5:
            scored[url] = max(scored.get(url, 0.0), score)
    return [u for u, _s in sorted(scored.items(), key=lambda kv: -kv[1])][:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Platforms and cart links
# ─────────────────────────────────────────────────────────────────────────────

_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/asin)/([A-Z0-9]{10})(?:[/?]|$)")


def is_amazon(host: str) -> bool:
    return bool(re.search(r"(^|\.)amazon\.[a-z.]{2,8}$", host or ""))


def detect_platform(page: str, url: str) -> dict:
    """{"platform": shopify | woocommerce | amazon | "", plus the ids a cart link needs}."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if is_amazon(host):
        m = _ASIN.search(urllib.parse.urlparse(url).path + "/")
        if m:
            return {"platform": "amazon", "asin": m.group(1)}
    text = page or ""
    shopify_cdn = re.search(r"https?://cdn\.shopify\.com(?:/|\b)", text, re.IGNORECASE)
    if shopify_cdn or "Shopify.shop" in text or "shopify-section" in text:
        variant = ""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if q.get("variant"):
            variant = q["variant"][0]
        if not variant:
            m = re.search(r"[\"']?(?:selected_or_first_available_variant|current_variant)"
                          r"[\"']?\s*[:=]\s*\{[^{}]*?[\"']?id[\"']?\s*:\s*(\d{6,})", text)
            m = m or re.search(r"name=[\"']id[\"'][^>]*value=[\"'](\d{6,})", text)
            m = m or re.search(r"value=[\"'](\d{6,})[\"'][^>]*name=[\"']id[\"']", text)
            variant = m.group(1) if m else ""
        return {"platform": "shopify", "variant": variant}
    if "woocommerce" in text.lower():
        m = (re.search(r"name=[\"']add-to-cart[\"'][^>]*value=[\"'](\d+)", text)
             or re.search(r"value=[\"'](\d+)[\"'][^>]*name=[\"']add-to-cart[\"']", text)
             or re.search(r"data-product_id=[\"'](\d+)", text)
             or re.search(r"\bpostid-(\d+)\b", text))
        if m:
            return {"platform": "woocommerce", "product_id": m.group(1)}
    return {"platform": ""}


def cart_links(items: list[dict]) -> list[dict]:
    """[{url, items: [indexes], platform}] for the items a platform can add by link.

    `items` — [{url, qty, platform: {...detect_platform...}}]. Amazon items of
    one country share one link; Shopify and WooCommerce get one link each.
    Items no platform can take are left out: the caller knows which they are.
    """
    links: list[dict] = []
    amazon: dict[str, list] = {}
    for i, item in enumerate(items):
        plat = item.get("platform") or {}
        qty = max(1, int(item.get("qty") or 1))
        parts = urllib.parse.urlparse(item.get("url") or "")
        root = f"{parts.scheme or 'https'}://{parts.netloc}"
        if not parts.netloc:
            continue
        if plat.get("platform") == "amazon" and plat.get("asin"):
            amazon.setdefault(root, []).append((i, plat["asin"], qty))
        elif plat.get("platform") == "shopify" and plat.get("variant"):
            links.append({"url": f"{root}/cart/add?id={plat['variant']}&quantity={qty}",
                          "items": [i], "platform": "shopify"})
        elif plat.get("platform") == "woocommerce" and plat.get("product_id"):
            links.append({"url": f"{root}/?add-to-cart={plat['product_id']}&quantity={qty}",
                          "items": [i], "platform": "woocommerce"})
    for root, entries in amazon.items():
        query = []
        for n, (_i, asin, qty) in enumerate(entries, 1):
            query += [(f"ASIN.{n}", asin), (f"Quantity.{n}", str(qty))]
        links.append({"url": f"{root}/gp/aws/cart/add.html?" + urllib.parse.urlencode(query),
                      "items": [e[0] for e in entries], "platform": "amazon"})
    return links


_CHECKOUT = re.compile(r"(?i)(checkout|/pay(ment)?\b|/order/|/buy-now|one-?click|/cart/\d+:\d+)")


def is_checkout(url: str) -> bool:
    """True for an address that would start paying. Nothing here opens one."""
    return bool(_CHECKOUT.search(urllib.parse.urlparse(url or "").path or ""))

