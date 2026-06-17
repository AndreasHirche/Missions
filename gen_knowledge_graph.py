"""
Generate knowledge_graph.html from Mission_Portfolio.xlsx + live UCR API.
Run: python gen_knowledge_graph.py
"""
import openpyxl, json, sys, re, os, urllib.request, urllib.parse, time
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
BASE = os.path.dirname(os.path.abspath(__file__))

JIRA_PAT  = ""  # not used — UCR auth via browser cookie
UCR_BASE  = "https://ucr.cfapps.eu10-004.hana.ondemand.com"

# ── 1. Load Mission Portfolio ────────────────────────────────────────────────
print("Loading Mission_Portfolio.xlsx...")
wb = openpyxl.load_workbook(os.path.join(BASE, "Mission_Portfolio.xlsx"), data_only=True)
ws = wb["Mission Portfolio"]
rows = list(ws.iter_rows(values_only=True))
headers = [str(h).strip() if h else "" for h in rows[0]]

def col(name_fragment):
    for i, h in enumerate(headers):
        if name_fragment.lower() in h.lower():
            return i
    return None

C_ID     = col("Mission ID")
C_NAME   = col("Mission Name")
C_CAT    = col("Category")
C_TAGS   = col("Tags")
C_STATUS = col("STATUS")
C_TYPE   = col("Mission Type")
C_ENV    = col("Environment")
C_BIZ    = col("Business Oriented")
C_LINK   = col("Link to Discovery Center")
C_UCR    = col("Use Case in UCR")
C_EVANG  = col("Disco Center Evangelist")

missions = []
for r in rows[1:]:
    if not r[C_ID] or not r[C_NAME]: continue
    mid = str(r[C_ID]).strip()
    # Parse tags
    raw_tags = str(r[C_TAGS]).strip() if r[C_TAGS] else ""
    tags = [t.strip() for t in re.split(r'[\n,;]', raw_tags) if t.strip() and len(t.strip()) > 1]
    # Normalize category
    cat = str(r[C_CAT]).strip() if r[C_CAT] else "Other"
    # Normalize status
    status = str(r[C_STATUS]).strip().lower() if r[C_STATUS] else ""
    missions.append({
        "id": mid,
        "name": str(r[C_NAME]).strip(),
        "category": cat,
        "tags": tags,
        "status": status,
        "type": str(r[C_TYPE]).strip() if r[C_TYPE] else "",
        "env": str(r[C_ENV]).strip() if r[C_ENV] else "",
        "biz": str(r[C_BIZ]).strip() if r[C_BIZ] else "",
        "evangelist": str(r[C_EVANG]).strip() if r[C_EVANG] else "",
        "ucr": str(r[C_UCR]).strip() if r[C_UCR] else "",
        "url": str(r[C_LINK]).strip() if r[C_LINK] else f"https://discovery-center.cloud.sap/#/missiondetail/{mid}",
    })

print(f"  {len(missions)} missions loaded")

# ── 2. Try live UCR status (optional) ────────────────────────────────────────
cookie_file = os.path.join(BASE, ".ucr_cookie.json")
ucr_status = {}
try:
    d = json.load(open(cookie_file))
    cookie = d.get("cookie","")
    age = time.time() - d.get("ts", 0)
    if cookie and age < 25*60:
        print(f"Fetching live UCR statuses (cookie age {int(age)}s)...")
        url = UCR_BASE + "/uc-authbackend/api/v1/use-case/list?top=200&skip=0&fields=id,name,status"
        # fetch all pages
        skip = 0
        total = 9999
        while skip < total:
            u = url.replace("skip=0", f"skip={skip}")
            req = urllib.request.Request(u,
                data=b'{"involvement":"ALL_USE_CASES"}',
                headers={"Cookie":cookie,"Content-Type":"application/json","Accept":"application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                resp = json.load(r)
            res = resp.get("results", resp)
            total = res.get("totalItems", total)
            batch = res.get("useCases", [])
            for uc in batch:
                ucr_status[uc["id"]] = uc.get("status","")
            skip += 200
            print(f"  fetched {len(ucr_status)}/{total}")
            if not batch: break
        print(f"  Live UCR: {len(ucr_status)} statuses")
    else:
        print("  UCR cookie missing or expired — using Excel status")
except Exception as e:
    print(f"  UCR fetch failed: {e} — using Excel status")

# ── 3. Merge live status ─────────────────────────────────────────────────────
for m in missions:
    ucr_id = m.get("ucr","")
    if ucr_id and ucr_id in ucr_status:
        m["status"] = ucr_status[ucr_id].lower()

# ── 4. Build graph data ──────────────────────────────────────────────────────
print("Building graph...")
nodes = {}
links = []

def node(nid, label, ntype, **extra):
    if nid not in nodes:
        nodes[nid] = {"id": nid, "label": label, "type": ntype, "count": 0, **extra}
    return nodes[nid]

# Category normalization (merge duplicates)
CAT_NORM = {
    "database and data management": "Database & Data Mgmt",
    "application development and automation": "App Dev & Automation",
    "digital process automation": "Digital Process Automation",
    "data and analytics": "Data & Analytics",
    "integration suite": "Integration Suite",
    "inegration suite": "Integration Suite",
    "generative ai": "Generative AI",
    "artificial intelligence": "AI",
    "ai core": "AI",
    "development efficiency": "Dev Efficiency",
    "digital experience": "Digital Experience",
}

def norm_cat(c):
    return CAT_NORM.get(c.lower(), c)

status_order = {"published":1, "in progress":2, "draft":3, "retired":4}

for m in missions:
    mid = f"m:{m['id']}"
    mn = node(mid, m["name"], "mission",
        ucId=m["id"], status=m["status"], url=m["url"],
        evangelist=m["evangelist"], mtype=m["type"])

    # Category
    cat = norm_cat(m["category"])
    if cat:
        cn = node(f"cat:{cat}", cat, "category")
        cn["count"] += 1
        links.append({"source": mid, "target": f"cat:{cat}", "rel": "category"})

    # Tags (only meaningful ones, skip noise)
    for t in m["tags"]:
        if len(t) < 3 or t.startswith("(") or t in ("Yes","No","yes","no"): continue
        tn = node(f"tag:{t.lower()}", t, "tag")
        tn["count"] += 1
        links.append({"source": mid, "target": f"tag:{t.lower()}", "rel": "tag"})

    # Mission type
    if m["type"] and m["type"] not in ("Standard",""):
        tn = node(f"type:{m['type']}", m["type"], "mtype")
        tn["count"] += 1
        links.append({"source": mid, "target": f"type:{m['type']}", "rel": "type"})

    # Evangelist
    if m["evangelist"]:
        en = node(f"evang:{m['evangelist']}", m["evangelist"], "evangelist")
        en["count"] += 1
        links.append({"source": mid, "target": f"evang:{m['evangelist']}", "rel": "evangelist"})

node_list = list(nodes.values())
print(f"  {len(node_list)} nodes, {len(links)} edges")
cats_count = sum(1 for n in node_list if n["type"]=="category")
tags_count = sum(1 for n in node_list if n["type"]=="tag")
evang_count = sum(1 for n in node_list if n["type"]=="evangelist")
print(f"  missions={len(missions)}, categories={cats_count}, tags={tags_count}, evangelists={evang_count}")

GRAPH_JSON = json.dumps({"nodes": node_list, "links": links}, ensure_ascii=False)
STATS = json.dumps({
    "missions": len(missions),
    "categories": cats_count,
    "tags": tags_count,
    "evangelists": evang_count,
    "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
})

# ── 5. Write HTML ─────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SAP Discovery Center – Knowledge Graph</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','72',sans-serif;background:#0a0f1e;color:#e2e8f0;height:100vh;display:flex;flex-direction:column;overflow:hidden}
.top-bar{display:flex;align-items:center;gap:12px;padding:9px 16px;background:#0f172a;border-bottom:1px solid #1e293b;flex-shrink:0;flex-wrap:wrap}
.logo{font-size:0.72rem;font-weight:900;background:linear-gradient(135deg,#0070f2,#00b4d8);color:#fff;padding:5px 10px;border-radius:8px;letter-spacing:0.05em;flex-shrink:0}
.top-bar h1{font-size:0.95rem;font-weight:700;color:#f1f5f9}
.stats-row{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
.stat-chip{font-size:0.68rem;padding:3px 10px;border-radius:12px;font-weight:600;white-space:nowrap}
.s-mission{background:#1e3a5f;color:#60a5fa}
.s-category{background:#1a3a2a;color:#4ade80}
.s-tag{background:#3a2a0a;color:#fbbf24}
.s-evang{background:#2a1a3a;color:#c084fc}
.legend{display:flex;gap:10px;align-items:center;padding:6px 16px;background:#0f172a;border-bottom:1px solid #1e293b;flex-shrink:0;flex-wrap:wrap}
.li{display:flex;align-items:center;gap:5px;font-size:0.7rem;color:#94a3b8;cursor:pointer;padding:2px 7px;border-radius:6px;transition:background .15s;user-select:none}
.li:hover{background:#1e293b}
.li.off{opacity:0.3}
.ld{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.controls{display:flex;gap:8px;align-items:center;padding:6px 16px;background:#0a0f1e;border-bottom:1px solid #1e293b;flex-shrink:0;flex-wrap:wrap}
.sw{position:relative}
.sw input{background:#1e293b;border:1px solid #334155;border-radius:7px;padding:5px 10px 5px 28px;color:#e2e8f0;font-size:0.8rem;width:200px;outline:none}
.sw input:focus{border-color:#0070f2}
.sw .si{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:#475569;font-size:0.85rem;pointer-events:none}
select.ctrl{background:#1e293b;border:1px solid #334155;border-radius:7px;padding:5px 8px;color:#e2e8f0;font-size:0.78rem;outline:none;cursor:pointer}
.btn{padding:5px 12px;border-radius:7px;border:1px solid #334155;background:#1e293b;color:#94a3b8;font-size:0.75rem;cursor:pointer;transition:all .15s}
.btn:hover{background:#334155;color:#f1f5f9}
.btn.on{background:#0070f2;border-color:#0070f2;color:#fff}
#ncount{font-size:0.72rem;color:#475569;margin-left:4px}
.main{flex:1;display:flex;min-height:0}
#gw{flex:1;position:relative;overflow:hidden}
svg{width:100%;height:100%;display:block}
.detail{width:280px;background:#0f172a;border-left:1px solid #1e293b;overflow-y:auto;flex-shrink:0;padding:16px;display:none}
.detail.open{display:block}
.detail h2{font-size:0.88rem;font-weight:700;color:#f1f5f9;line-height:1.4;margin-bottom:8px}
.dtype{font-size:0.62rem;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;padding:2px 8px;border-radius:8px;display:inline-block;margin-bottom:8px}
.df{margin-bottom:9px}
.df label{font-size:0.6rem;text-transform:uppercase;letter-spacing:0.07em;color:#475569;display:block;margin-bottom:2px}
.df p{color:#94a3b8;font-size:0.78rem;line-height:1.45}
.dg h3{font-size:0.62rem;text-transform:uppercase;letter-spacing:0.06em;color:#475569;margin:12px 0 6px}
.dc{font-size:0.75rem;padding:4px 8px;margin-bottom:2px;border-radius:5px;background:#1e293b;color:#94a3b8;cursor:pointer;display:flex;align-items:center;gap:6px;transition:background .12s}
.dc:hover{background:#334155;color:#f1f5f9}
.dd{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.xbtn{float:right;background:none;border:none;color:#475569;font-size:1rem;cursor:pointer;padding:0 0 6px 6px}
.xbtn:hover{color:#f1f5f9}
.ext{display:inline-flex;align-items:center;gap:3px;font-size:0.72rem;color:#0070f2;text-decoration:none;margin-top:5px}
.ext:hover{text-decoration:underline}
.tt{position:absolute;pointer-events:none;background:#0f172a;border:1px solid #334155;border-radius:8px;padding:7px 11px;font-size:0.75rem;color:#e2e8f0;max-width:240px;z-index:100;display:none;line-height:1.5}
</style>
</head>
<body>
<div class="top-bar">
  <div class="logo">DC</div>
  <h1>SAP Discovery Center &ndash; Knowledge Graph</h1>
  <div class="stats-row" id="sr"></div>
</div>
<div class="legend">
  <span style="font-size:0.68rem;color:#475569;font-weight:600">Show:</span>
  <div class="li" data-t="mission"   onclick="toggleT(this)"><div class="ld" style="background:#60a5fa"></div>Mission</div>
  <div class="li" data-t="category"  onclick="toggleT(this)"><div class="ld" style="background:#4ade80"></div>Category</div>
  <div class="li" data-t="tag"       onclick="toggleT(this)"><div class="ld" style="background:#fbbf24"></div>Tag</div>
  <div class="li" data-t="evangelist" onclick="toggleT(this)"><div class="ld" style="background:#c084fc"></div>Evangelist</div>
  <div class="li" data-t="mtype"     onclick="toggleT(this)"><div class="ld" style="background:#f87171"></div>Type</div>
  <div style="width:1px;background:#1e293b;height:14px;margin:0 2px"></div>
  <div class="li" onclick="zoomFit()" style="color:#0070f2">&#8635; Reset</div>
</div>
<div class="controls">
  <div class="sw">
    <span class="si">&#9906;</span>
    <input type="text" id="sq" placeholder="Search nodes&hellip;" oninput="onSearch()">
  </div>
  <select class="ctrl" id="cf" onchange="applyF()">
    <option value="">All Categories</option>
  </select>
  <select class="ctrl" id="sf" onchange="applyF()">
    <option value="">All Statuses</option>
    <option value="published">Published</option>
    <option value="in progress">In Progress</option>
    <option value="draft">Draft</option>
    <option value="retired">Retired</option>
  </select>
  <button class="btn on"  id="b-force"   onclick="setL('force')">Force</button>
  <button class="btn"     id="b-radial"  onclick="setL('radial')">Radial</button>
  <span id="ncount"></span>
</div>
<div class="main">
  <div id="gw">
    <svg id="svg"><g id="root"></g></svg>
    <div class="tt" id="tt"></div>
  </div>
  <div class="detail" id="dp"><div id="dpc"></div></div>
</div>
<script>
const GRAPH=""" + GRAPH_JSON + """;
const STATS=""" + STATS + """;
const TYPE={
  mission:   {color:'#60a5fa', stroke:'#1d4ed8', rBase:5},
  category:  {color:'#4ade80', stroke:'#166534', rBase:18},
  tag:       {color:'#fbbf24', stroke:'#92400e', rBase:9},
  evangelist:{color:'#c084fc', stroke:'#7e22ce', rBase:12},
  mtype:     {color:'#f87171', stroke:'#991b1b', rBase:10},
};
const STATUS_COLOR={published:'#4ade80','in progress':'#60a5fa',draft:'#94a3b8',retired:'#f87171'};

let vis={mission:1,category:1,tag:1,evangelist:1,mtype:1};
let filt={q:'',cat:'',status:''};
let layout='force';
let selNode=null;
let sim, svgSel, zoomBeh, linkSel, nodeSel, labelSel;

// Stats
(function(){
  document.getElementById('sr').innerHTML=
    '<span class="stat-chip s-mission">'+STATS.missions+' Missions</span>'+
    '<span class="stat-chip s-category">'+STATS.categories+' Categories</span>'+
    '<span class="stat-chip s-tag">'+STATS.tags+' Tags</span>'+
    '<span class="stat-chip s-evang">'+STATS.evangelists+' Evangelists</span>'+
    '<span style="font-size:0.65rem;color:#334155;align-self:center">Generated '+STATS.generated+'</span>';
  // Populate category filter
  const sel=document.getElementById('cf');
  const cats=[...new Set(GRAPH.nodes.filter(n=>n.type==='category').map(n=>n.label))].sort();
  cats.forEach(c=>{const o=document.createElement('option');o.value=c;o.textContent=c;sel.appendChild(o);});
})();

function nodeR(n){
  const base=TYPE[n.type]?.rBase||6;
  if(n.type==='mission') return base;
  return Math.min(base+Math.sqrt(n.count||0)*1.8, base*3.5);
}

function filteredData(){
  let nodes=GRAPH.nodes.filter(n=>{
    if(!vis[n.type]) return false;
    if(filt.status && n.type==='mission' && n.status!==filt.status) return false;
    if(filt.q && !n.label.toLowerCase().includes(filt.q)) return false;
    return true;
  });
  if(filt.cat){
    const catId='cat:'+filt.cat;
    const keep=new Set([catId]);
    GRAPH.links.forEach(l=>{
      const s=typeof l.source==='object'?l.source.id:l.source;
      const t=typeof l.target==='object'?l.target.id:l.target;
      if(t===catId) keep.add(s);
    });
    // add connected tags/evangelists of kept missions
    GRAPH.links.forEach(l=>{
      const s=typeof l.source==='object'?l.source.id:l.source;
      const t=typeof l.target==='object'?l.target.id:l.target;
      if(keep.has(s)) keep.add(t);
    });
    nodes=nodes.filter(n=>keep.has(n.id));
  }
  const nids=new Set(nodes.map(n=>n.id));
  const links=GRAPH.links.filter(l=>{
    const s=typeof l.source==='object'?l.source.id:l.source;
    const t=typeof l.target==='object'?l.target.id:l.target;
    return nids.has(s)&&nids.has(t)&&vis[l.rel];
  });
  return{nodes,links};
}

function render(){
  const wrap=document.getElementById('gw');
  const W=wrap.clientWidth, H=wrap.clientHeight;
  svgSel=d3.select('#svg').attr('viewBox',`0 0 ${W} ${H}`);
  const root=d3.select('#root');
  root.selectAll('*').remove();

  zoomBeh=d3.zoom().scaleExtent([0.02,8]).on('zoom',e=>root.attr('transform',e.transform));
  svgSel.call(zoomBeh);
  svgSel.on('click',()=>{selNode=null;closePanel();nodeSel&&nodeSel.attr('stroke-width',1);});

  const {nodes,links}=filteredData();
  window._nd=nodes; window._ld=links;
  document.getElementById('ncount').textContent=nodes.length+' nodes · '+links.length+' edges';

  const lg=root.append('g');
  const ng=root.append('g');

  linkSel=lg.selectAll('line').data(links).join('line')
    .attr('stroke',d=>TYPE[d.rel]?.color||'#334155')
    .attr('stroke-opacity',0.2).attr('stroke-width',0.6);

  nodeSel=ng.selectAll('circle').data(nodes,d=>d.id).join('circle')
    .attr('r',nodeR)
    .attr('fill',d=>d.type==='mission'?(STATUS_COLOR[d.status]||'#60a5fa'):TYPE[d.type].color)
    .attr('stroke',d=>TYPE[d.type]?.stroke||'#334155')
    .attr('stroke-width',1).attr('cursor','pointer')
    .on('mouseover',onHover).on('mouseout',onOut).on('click',onClick)
    .call(d3.drag()
      .on('start',(e,d)=>{if(!e.active)sim.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y;})
      .on('drag', (e,d)=>{d.fx=e.x;d.fy=e.y;})
      .on('end',  (e,d)=>{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null;}));

  labelSel=ng.selectAll('text').data(nodes.filter(n=>n.type!=='mission'),d=>d.id).join('text')
    .text(d=>d.label.length>28?d.label.slice(0,26)+'…':d.label)
    .attr('font-size',d=>d.type==='category'?'11px':d.type==='evangelist'?'9px':'8px')
    .attr('font-weight',d=>d.type==='category'?'700':'400')
    .attr('fill',d=>TYPE[d.type].color)
    .attr('text-anchor','middle')
    .attr('dy',d=>-(nodeR(d)+3))
    .attr('pointer-events','none')
    .style('text-shadow','0 0 5px #0a0f1e,0 0 5px #0a0f1e');

  if(sim) sim.stop();
  sim=d3.forceSimulation(nodes)
    .force('link',d3.forceLink(links).id(d=>d.id)
      .distance(d=>d.rel==='category'?90:d.rel==='evangelist'?60:45)
      .strength(d=>d.rel==='category'?0.5:0.3))
    .force('charge',d3.forceManyBody()
      .strength(d=>d.type==='category'?-300:d.type==='mission'?-20:-100))
    .force('center',d3.forceCenter(W/2,H/2))
    .force('collide',d3.forceCollide().radius(d=>nodeR(d)+2))
    .alphaDecay(0.02)
    .on('tick',tick);

  sim.alpha(1).restart();
  setTimeout(zoomFit, 3000);
}

function tick(){
  linkSel.attr('x1',d=>d.source.x).attr('y1',d=>d.source.y)
         .attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);
  nodeSel.attr('cx',d=>d.x).attr('cy',d=>d.y);
  labelSel.attr('x',d=>d.x).attr('y',d=>d.y);
}

// Interaction
function nbrs(id){
  const s=new Set();
  (window._ld||[]).forEach(l=>{
    const a=typeof l.source==='object'?l.source.id:l.source;
    const b=typeof l.target==='object'?l.target.id:l.target;
    if(a===id)s.add(b); if(b===id)s.add(a);
  });
  return s;
}

function onHover(ev,d){
  const nb=nbrs(d.id);
  nodeSel.attr('opacity',n=>n.id===d.id||nb.has(n.id)?1:0.12);
  linkSel.attr('stroke-opacity',l=>{
    const a=typeof l.source==='object'?l.source.id:l.source;
    const b=typeof l.target==='object'?l.target.id:l.target;
    return a===d.id||b===d.id?0.9:0.03;
  });
  const tt=document.getElementById('tt');
  const tl={mission:'Mission',category:'Category',tag:'Tag',evangelist:'Evangelist',mtype:'Mission Type'}[d.type]||d.type;
  tt.style.display='block';
  tt.innerHTML=`<b style="color:${TYPE[d.type]?.color}">${esc(d.label)}</b><br>
    <span style="color:#64748b;font-size:0.67rem">${tl}${d.status?' · '+d.status:''}${d.count?' · '+d.count+' missions':''}</span>`;
  tt.style.left=(ev.offsetX+14)+'px';
  tt.style.top=(ev.offsetY-10)+'px';
}

function onOut(){
  document.getElementById('tt').style.display='none';
  nodeSel&&nodeSel.attr('opacity',1);
  linkSel&&linkSel.attr('stroke-opacity',0.2);
}

function onClick(ev,d){
  ev.stopPropagation();
  selNode=d;
  nodeSel.attr('stroke-width',n=>n.id===d.id?3:1);
  openPanel(d);
}

function openPanel(d){
  const nb=nbrs(d.id);
  const nbNodes=(window._nd||[]).filter(n=>nb.has(n.id));
  const tl={mission:'Mission',category:'Category',tag:'Tag',evangelist:'Evangelist',mtype:'Mission Type'}[d.type]||d.type;
  const tc={mission:'background:#1e3a5f;color:#60a5fa',category:'background:#1a3a2a;color:#4ade80',
    tag:'background:#3a2a0a;color:#fbbf24',evangelist:'background:#2a1a3a;color:#c084fc',
    mtype:'background:#3a1a1a;color:#f87171'}[d.type]||'';
  const grp={};
  nbNodes.forEach(n=>{if(!grp[n.type])grp[n.type]=[];grp[n.type].push(n);});
  let ch='';
  Object.entries(grp).forEach(([type,ns])=>{
    const tll={mission:'Missions',category:'Categories',tag:'Tags',evangelist:'Evangelists',mtype:'Types'}[type]||type;
    ch+=`<div class="dg"><h3>${tll} (${ns.length})</h3>`;
    ns.slice(0,15).forEach(n=>{
      ch+=`<div class="dc" onclick="focusNode('${n.id}')"><div class="dd" style="background:${TYPE[n.type]?.color}"></div>${esc(n.label)}</div>`;
    });
    if(ns.length>15)ch+=`<div style="font-size:0.67rem;color:#334155;padding:3px 8px">+${ns.length-15} more</div>`;
    ch+='</div>';
  });
  document.getElementById('dpc').innerHTML=
    `<button class="xbtn" onclick="closePanel()">&#10005;</button>`+
    `<div class="dtype" style="${tc}">${tl}</div>`+
    `<h2>${esc(d.label)}</h2>`+
    (d.status?`<div class="df"><label>Status</label><p>${d.status}</p></div>`:'')+
    (d.ucId?`<div class="df"><label>Mission ID</label><p>${d.ucId}</p></div>`+
      `<a class="ext" href="${d.url}" target="_blank" rel="noopener">Open in Discovery Center &#8599;</a>`:'') +
    (d.count?`<div class="df"><label>Missions connected</label><p>${d.count}</p></div>`:'')+
    (d.evangelist?`<div class="df"><label>Evangelist</label><p>${d.evangelist}</p></div>`:'')+
    ch;
  document.getElementById('dp').classList.add('open');
}

function closePanel(){document.getElementById('dp').classList.remove('open');}

function focusNode(id){
  const n=(window._nd||[]).find(x=>x.id===id);
  if(!n)return;
  openPanel(n);
  nodeSel.attr('stroke-width',nd=>nd.id===id?3:1);
  selNode=n;
  const W=document.getElementById('gw').clientWidth;
  const H=document.getElementById('gw').clientHeight;
  svgSel.transition().duration(500).call(zoomBeh.transform,
    d3.zoomIdentity.translate(W/2-n.x,H/2-n.y).scale(1.5));
}

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// Controls
function toggleT(el){
  const t=el.dataset.t;
  vis[t]=!vis[t];
  el.classList.toggle('off',!vis[t]);
  render();
}
function onSearch(){filt.q=document.getElementById('sq').value.toLowerCase().trim();render();}
function applyF(){
  filt.cat=document.getElementById('cf').value;
  filt.status=document.getElementById('sf').value;
  render();
}
function setL(l){
  layout=l;
  document.getElementById('b-force').classList.toggle('on',l==='force');
  document.getElementById('b-radial').classList.toggle('on',l==='radial');
  render();
}
function zoomFit(){
  const nd=window._nd;
  if(!nd||!nd.length)return;
  const wrap=document.getElementById('gw');
  const W=wrap.clientWidth,H=wrap.clientHeight,pad=60;
  const xs=nd.map(d=>d.x).filter(Boolean), ys=nd.map(d=>d.y).filter(Boolean);
  if(!xs.length)return;
  const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
  const sc=Math.min((W-pad*2)/(x1-x0||1),(H-pad*2)/(y1-y0||1),4);
  svgSel.transition().duration(700).call(zoomBeh.transform,
    d3.zoomIdentity.translate(W/2-sc*(x0+x1)/2,H/2-sc*(y0+y1)/2).scale(sc));
}

window.addEventListener('load', render);
window.addEventListener('resize', ()=>{if(window._nd)render();});
</script>
</body>
</html>"""

out = os.path.join(BASE, "knowledge_graph.html")
with open(out, 'w', encoding='utf-8') as f:
    f.write(HTML)
print(f"Written {len(HTML):,} bytes -> {out}")
