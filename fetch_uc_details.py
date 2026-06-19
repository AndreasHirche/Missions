"""
fetch_uc_details.py — Fetches services, products and tags for all UCR missions.

Run:  python fetch_uc_details.py
Needs: .ucr_cookie.json with a valid session cookie (refresh via http://localhost:8080/ucr-login)

Output: uc_details.json  — {ucId: {services:[], products:[], tags:[], lineOfBusiness:[]}}
"""
import json, urllib.request, time, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')

BASE       = os.path.dirname(os.path.abspath(__file__))
UCR_BASE   = "https://ucr.cfapps.eu10-004.hana.ondemand.com"
COOKIE_FILE = os.path.join(BASE, ".ucr_cookie.json")
OUT_FILE    = os.path.join(BASE, "uc_details.json")
RATE_DELAY  = 0.15   # seconds between requests
MAX_RETRIES = 3

def load_cookie():
    try:
        d = json.load(open(COOKIE_FILE))
        age = time.time() - d.get("ts", 0)
        cookie = d.get("cookie", "")
        if not cookie:
            raise ValueError("Cookie is empty")
        if age > 25 * 60:
            print(f"  Warning: cookie is {int(age/60)} minutes old (may be expired)")
        return cookie
    except Exception as e:
        raise RuntimeError(f"Cannot load cookie from {COOKIE_FILE}: {e}\n"
                           "Refresh it at http://localhost:8080/ucr-login")

def fetch_uc_list(cookie):
    """Fetch all UC IDs from the list endpoint."""
    all_ucs = []
    skip = 0
    total = 9999
    print("Fetching UC list...")
    while skip < total:
        url = f"{UCR_BASE}/uc-authbackend/api/v1/use-case/list?top=200&skip={skip}&fields=id,name,status"
        req = urllib.request.Request(url,
            data=b'{"involvement":"ALL_USE_CASES"}',
            headers={"Cookie": cookie, "Content-Type": "application/json", "Accept": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        res = data.get("results", data)
        total = res.get("totalItems", total)
        batch = res.get("useCases", [])
        if not batch:
            break
        all_ucs.extend(batch)
        skip += 200
        print(f"  {len(all_ucs)}/{total}", end="\r", flush=True)
    print(f"\n  Got {len(all_ucs)} UC IDs")
    return all_ucs

def fetch_uc_detail(uc_id, cookie, retry=0):
    """Fetch full detail for one UC including services/products/tags."""
    url = f"{UCR_BASE}/uc-authbackend/api/v1/use-case/{uc_id}"
    req = urllib.request.Request(url,
        headers={"Cookie": cookie, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        return data.get("results", data)
    except urllib.error.HTTPError as e:
        if e.code == 429 and retry < MAX_RETRIES:
            wait = int(e.headers.get("Retry-After", "10"))
            print(f"\n  429 rate limit, waiting {wait}s...")
            time.sleep(wait)
            return fetch_uc_detail(uc_id, cookie, retry + 1)
        if e.code in (401, 403):
            raise RuntimeError(f"Auth error {e.code} — refresh cookie at http://localhost:8080/ucr-login")
        print(f"\n  HTTP {e.code} for {uc_id}")
        return None
    except Exception as e:
        print(f"\n  Error for {uc_id}: {e}")
        return None

def extract_details(uc):
    """Extract services, products, tags, lineOfBusiness from a UC detail object."""
    props = uc.get("useCaseProperties") or {}
    ext   = uc.get("useCaseExternalData") or {}

    def names(lst):
        if not lst: return []
        return [x.get("name") or x.get("title") or str(x) for x in lst if x]

    services       = names(props.get("serviceProducts") or props.get("services") or
                           ext.get("serviceProducts") or ext.get("services") or [])
    products       = names(props.get("product") or props.get("products") or
                           ext.get("product") or ext.get("products") or [])
    tags           = names(props.get("tags") or ext.get("tags") or [])
    lob            = names(props.get("lob") or props.get("lineOfBusiness") or
                           ext.get("lob") or ext.get("lineOfBusiness") or [])
    partner_apps   = names(props.get("partnerSolutions") or props.get("partnerApplications") or
                           ext.get("partnerSolutions") or ext.get("partnerApplications") or [])

    # Also check the rba_rsa_process (E2E processes)
    processes      = [p.get("title","") for p in (props.get("rba_rsa_process") or []) if p]

    return {
        "services":    services,
        "products":    products,
        "tags":        tags,
        "lob":         lob,
        "partnerApps": partner_apps,
        "processes":   processes,
    }

def main():
    cookie = load_cookie()

    # Load existing results to resume interrupted run
    existing = {}
    if os.path.exists(OUT_FILE):
        try:
            existing = json.load(open(OUT_FILE, encoding='utf-8'))
            print(f"Resuming: {len(existing)} already fetched")
        except Exception:
            pass

    # Get all UC IDs
    all_ucs = fetch_uc_list(cookie)
    total   = len(all_ucs)
    done    = 0
    errors  = 0

    print(f"\nFetching details for {total} use cases...")
    print("  (This takes ~3-4 minutes at 0.15s/request)")

    for i, uc in enumerate(all_ucs):
        uc_id = uc.get("id") or uc.get("ucId")
        if not uc_id:
            continue
        if uc_id in existing:
            done += 1
            continue

        detail = fetch_uc_detail(uc_id, cookie)
        time.sleep(RATE_DELAY)

        if detail:
            info = extract_details(detail)
            # Only store if has meaningful data
            if any(info.values()):
                existing[uc_id] = info
                done += 1
            else:
                existing[uc_id] = {}
        else:
            errors += 1

        if (i + 1) % 50 == 0:
            # Save progress
            with open(OUT_FILE, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, separators=(',',':'))
            print(f"  Progress: {i+1}/{total} ({errors} errors) — saved")

    # Final save
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    # Print sample to verify fields found
    with_services = {k: v for k, v in existing.items() if v.get("services")}
    with_products = {k: v for k, v in existing.items() if v.get("products")}
    with_tags     = {k: v for k, v in existing.items() if v.get("tags")}
    with_partner  = {k: v for k, v in existing.items() if v.get("partnerApps")}

    print(f"\n=== Done ===")
    print(f"Total: {len(existing)} | With services: {len(with_services)} | "
          f"Products: {len(with_products)} | Tags: {len(with_tags)} | "
          f"Partner apps: {len(with_partner)} | Errors: {errors}")

    if with_services:
        sample_id = next(iter(with_services))
        print(f"\nSample ({sample_id}):")
        print(json.dumps(with_services[sample_id], indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
