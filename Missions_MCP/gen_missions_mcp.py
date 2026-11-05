"""
gen_missions_mcp.py — Baked-JSON-Generator fuer die Disco-MCP-Fork-Variante.

Datenquelle: SAP Discovery Center MCP (statt Excel/CSV).
  - mcp_raw.json      : Katalog-Dump (von Claude via mcp__dc-prod__list_mission_catalog)
  - mcp_details.json  : get_mission_detail fuer die MCP-only Missions (UCR ID + Services)
Hybrid-Felder (im MCP nicht vorhanden): Author, Coach name(s), Expiry Date
  -> people_sidecar.json (aus der Excel-abgeleiteten missions_baked.json geseedet)

Ausgabe: missions_mcp.json  (Schema identisch zu ../missions_baked.json)

Lauf:  python gen_missions_mcp.py
"""
import json, os, sys

sys.stdout.reconfigure(encoding="utf-8")

HERE   = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(HERE)

RAW      = os.path.join(HERE, "mcp_raw.json")
DETAILS  = os.path.join(HERE, "mcp_details.json")
SIDECAR  = os.path.join(HERE, "people_sidecar.json")
OUT      = os.path.join(HERE, "missions_mcp.json")
BAKED    = os.path.join(PARENT, "missions_baked.json")
UCSERV   = os.path.join(PARENT, "uc_services.json")

# ── Code -> Klartext-Maps (einmalig aus missions_baked.json x Katalog abgeleitet) ──
CAT_MAP = {
    "appdev":        "Application Development and Automation",
    "intgn":         "Integration",
    "aicatg":        "Artificial Intelligence",
    "dataanalytics": "Data and Analytics",
    "planning":      "Analytics and Planning",
    "onboard":       "Onboarding",
    "es":            "Enterprise Services",
    "ps":            "Professional Services",
    "partneronly":   "Partner Solutions",
}

# FocusTags-Codes -> Klartext (Korrelation ueber 166 gemeinsame Missions, nur sichere)
FOCUS_MAP = {
    "1":  "Artificial Intelligence",
    "3":  "Digital Transformation",
    "4":  "Extensibility",
    "6":  "Industry 4.0",
    "7":  "Integration",
    "9":  "User Experience",
    "11": "RISE with SAP",
    "13": "Sustainability",
    "18": "Business Transformation",
    "22": "Localization",
    "23": "Machine Learning",
    "24": "New Business Models",
    "26": "Two-tier ERP",
    "28": "GROW with SAP",
    "30": "Business Suite",
    "31": "Clean Core",
    "32": "Business AI",
    "34": "Industry Solution",
    "35": "Business Data Cloud",
    # unaufgeloeste/unsichere Codes bewusst weggelassen: 2,16,17,27,29,33,37,51,54,57
}

MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
def fmt_date(iso):
    """2026-10-21T07:46:57Z -> 'Oct 21, 2026'"""
    if not iso: return ""
    try:
        d = iso[:10].split("-")
        return f"{MONTHS[int(d[1])-1]} {int(d[2])}, {d[0]}"
    except Exception:
        return ""

def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def build_sidecar(baked_list):
    """Seed people_sidecar.json aus der Excel-abgeleiteten missions_baked.json (Key = UCR ID).
    Kann spaeter aus Mission_Portfolio.xlsx / Use Cases.csv neu erzeugt werden."""
    side = {}
    for m in baked_list:
        ucr = (m.get("UCR ID") or "").strip()
        if not ucr:
            continue
        side[ucr] = {
            "Author":        m.get("Author", ""),
            "Coach name(s)": m.get("Coach name(s)", ""),
            "Expiry Date":   m.get("Expiry Date", ""),
            "Featured Mission?": m.get("Featured Mission?", ""),
        }
    return side

def main():
    cat = load(RAW, None)
    if not cat:
        sys.exit(f"FEHLER: {RAW} fehlt oder leer. Erst Katalog-Dump erzeugen.")
    details   = load(DETAILS, {})
    baked_list = load(BAKED, {"missions": []})
    if isinstance(baked_list, dict):
        baked_list = baked_list.get("missions", [])
    baked = {str(m["_dcId"]): m for m in baked_list if m.get("_dcId")}
    uc_services = load(UCSERV, {})

    # people_sidecar: vorhandenes nutzen, sonst aus baked seeden + schreiben
    sidecar = load(SIDECAR, None)
    if sidecar is None:
        sidecar = build_sidecar(baked_list)
        with open(SIDECAR, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=1)
        print(f"people_sidecar.json geseedet: {len(sidecar)} Eintraege")

    missions = []
    n_detail = n_bakedjoin = n_newnoucr = 0

    for c in cat:
        dcId = str(c["Id"])
        b    = baked.get(dcId)
        det  = details.get(dcId)

        # UCR ID + Services + Status + LastModified + Industrien
        if b:
            n_bakedjoin += 1
            ucr        = (b.get("UCR ID") or "").strip()
            services   = b.get("_services") or []
            industries = b.get("_industries") or []
            status     = b.get("STATUS") or "Published"
            lastmod    = b.get("Last Modified On") or ""
            channels   = b.get("Published Channels") or "Discovery Center"
            company    = (b.get("Authoring Company") or c.get("PartnerCompany") or "").strip()
            link       = b.get("Link to Discovery Center") or ""
        else:
            ucr        = (det.get("ucrId") if det else "") or ""
            services   = (det.get("services") if det else None) or uc_services.get(dcId) or []
            industries = []
            status     = "Published"
            lastmod    = fmt_date(det.get("lastModified")) if det else ""
            channels   = "Discovery Center"
            company    = (c.get("PartnerCompany") or "").strip()
            link       = ""
            if det:
                n_detail += 1
        if not ucr:
            ucr = "DC" + dcId          # Fallback-Key fuer die Tabelle/KG
            n_newnoucr += 1
        if not services and dcId in uc_services:
            services = uc_services[dcId]
        if not link:
            link = f"https://discovery-center.cloud.sap/missiondetail/{dcId}/{dcId}/"

        # Category (Codes -> Klartext, mehrere mit ', ')
        cat_codes = [x for x in (c.get("Category") or "").split(",") if x]
        category  = ", ".join(CAT_MAP.get(x, x.title()) for x in cat_codes)

        # Focus (Codes -> Klartext, unsichere weggelassen)
        focus_codes = [x for x in (c.get("FocusTags") or "").split(",") if x]
        focus_topics = [FOCUS_MAP[x] for x in focus_codes if x in FOCUS_MAP]
        focus_csv    = ", ".join(focus_topics)

        portfolio_cat = "SAP" if company.strip().upper() == "SAP" else "Partner"

        # Hybrid-Felder aus Sidecar (per UCR ID)
        sc = sidecar.get(ucr, {})

        missions.append({
            "UCR ID":                  ucr,
            "Mission Name":            c.get("Name", ""),
            "STATUS":                  status,
            "Category":                category,
            "Author":                  sc.get("Author", ""),
            "Authoring Company":       company,
            "Coach name(s)":           sc.get("Coach name(s)", ""),
            "Published Channels":      channels,
            "_portfolio_cat":          portfolio_cat,
            "_dcId":                   dcId,
            "Link to Discovery Center": link,
            "_services":               services,
            "_focus_csv":              focus_csv,
            "_focusTopics":            focus_topics,
            "_industries":             industries,
            "Featured Mission?":       sc.get("Featured Mission?", ""),
            "Expiry Date":             sc.get("Expiry Date", ""),
            "Last Modified On":        lastmod,
        })

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"missions": missions}, f, ensure_ascii=False, separators=(",", ":"))

    with_serv = sum(1 for m in missions if m["_services"])
    with_ucr  = sum(1 for m in missions if not m["UCR ID"].startswith("DC"))
    print(f"Geschrieben: {len(missions)} Missions -> {OUT}")
    print(f"  aus baked gejoint: {n_bakedjoin} | via MCP-Detail: {n_detail} | ohne echte UCR ID: {n_newnoucr}")
    print(f"  mit Services: {with_serv} | mit echter UCR ID: {with_ucr}")

if __name__ == "__main__":
    main()
