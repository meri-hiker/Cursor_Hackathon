"""A single static HTML dashboard: open it in a browser, no server needed."""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from same_person.screener.pipeline import HitResult

TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screening dashboard</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2330;--muted:#667085;--line:#e3e6eb;
--same:#b42318;--same-bg:#fde8e7;--rev:#b54708;--rev-bg:#fef0c7;--diff:#067647;--diff-bg:#dcfae6;
--hi:#b42318;--med:#b54708;--info:#475467}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--ink:#e6e9ef;--muted:#98a2b3;
--line:#2b313c;--same:#ff8a80;--same-bg:#3b1a18;--rev:#fdb022;--rev-bg:#3a2a0c;--diff:#6ce9a6;
--diff-bg:#0f2e1f;--hi:#ff8a80;--med:#fdb022;--info:#98a2b3}}
*{box-sizing:border-box}body{margin:0;overflow-x:hidden;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 24px 8px}h1{margin:0 0 4px;font-size:20px}.sub{color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;padding:12px 24px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:22px}.tile span{color:var(--muted);font-size:12px}
.controls{display:flex;flex-wrap:wrap;gap:8px;padding:8px 24px}
.controls input,.controls select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;
background:var(--card);color:var(--ink)}.controls input{flex:1;min-width:220px}
table{width:calc(100% - 48px);margin:8px 24px 40px;border-collapse:collapse;background:var(--card);
border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{overflow-wrap:anywhere;padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{font-size:12px;color:var(--muted);cursor:pointer;user-select:none;white-space:nowrap}
tr.row{cursor:pointer}tr.row:hover{background:color-mix(in srgb,var(--line) 40%,transparent)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.SAME_ENTITY{color:var(--same);background:var(--same-bg)}.REVIEW{color:var(--rev);background:var(--rev-bg)}
.DIFFERENT_ENTITY{color:var(--diff);background:var(--diff-bg)}
.bar{height:6px;border-radius:3px;background:var(--line);width:80px;margin-top:4px}
.bar i{display:block;height:6px;border-radius:3px}
.flag{font-size:12px;margin:2px 0}.flag.high{color:var(--hi)}.flag.medium{color:var(--med)}
.flag.info{color:var(--info)}.cnt{font-weight:600}.cnt.h{color:var(--hi)}
tr.detail td{background:color-mix(in srgb,var(--bg) 60%,var(--card))}
.detail h4{margin:8px 0 4px;font-size:12px;color:var(--muted);text-transform:uppercase}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
td.mono{white-space:nowrap;overflow-wrap:normal}.ok{color:var(--diff)}.bad{color:var(--same)}
@media (max-width:760px){.hide-sm{display:none}table{width:calc(100% - 32px);margin:8px 16px}
header,.tiles,.controls{padding-left:16px;padding-right:16px}}
</style></head><body>
<header><h1>Screening dashboard</h1>
<div class="sub">Generated __GENERATED__ &middot; __N__ hits on __A__ alerts &middot;
click a row for the pipeline trail and red flags</div></header>
<div class="tiles" id="tiles"></div>
<div class="controls">
<input id="q" placeholder="Search name, alert, hit, customer record, flag...">
<select id="fd"><option value="">All decisions</option><option>SAME_ENTITY</option>
<option>REVIEW</option><option>DIFFERENT_ENTITY</option></select>
<select id="ff"><option value="">Any red flags</option><option value="high">Has high flags</option>
<option value="none">No red flags</option></select>
<select id="fl"><option value="">All hits</option><option value="lab">Practice-labelled</option>
<option value="wrong">Labelled and wrong</option></select>
</div>
<table><thead><tr>
<th data-k="alert_id">Alert</th><th data-k="customer_name">Customer (screened)</th>
<th data-k="hit_name" class="hide-sm">Hit</th><th data-k="decision">Decision</th>
<th data-k="rating">Rating</th><th data-k="high">Red flags</th>
<th data-k="customer_record_id" class="hide-sm">Record</th><th data-k="label" class="hide-sm">Label</th>
</tr></thead><tbody id="tb"></tbody></table>
<script>
const DATA=__DATA__;const SCORE=__SCORE__;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let sortKey='rating',sortDir=-1,open=new Set();
function tiles(){const c={SAME_ENTITY:0,REVIEW:0,DIFFERENT_ENTITY:0};DATA.forEach(r=>c[r.decision]++);
const alerts=new Set(DATA.map(r=>r.alert_id));
const cleared=[...alerts].filter(a=>DATA.filter(r=>r.alert_id===a).every(r=>r.decision==='DIFFERENT_ENTITY')).length;
const hf=DATA.filter(r=>r.high>0).length;
let t=[['SAME_ENTITY',c.SAME_ENTITY,'hits confirmed as the customer'],['REVIEW',c.REVIEW,'hits for a person to look at'],
['DIFFERENT_ENTITY',c.DIFFERENT_ENTITY,'hits cleared'],[null,cleared+' / '+alerts.size,'alerts fully cleared'],
[null,hf,'hits with high red flags']];
if(SCORE.labelled){t.push([null,SCORE.correct+' / '+SCORE.labelled+' ('+Math.round(SCORE.accuracy*100)+'%)','practice labels correct']);
t.push([null,SCORE.wrongly_cleared.length,'labelled hits wrongly cleared']);}
document.getElementById('tiles').innerHTML=t.map(([k,v,l])=>`<div class="tile"><b ${k?`class="${k}" style="background:none"`:''}>${esc(v)}</b><span>${esc(l)}</span></div>`).join('');}
function rows(){const q=document.getElementById('q').value.toLowerCase(),fd=document.getElementById('fd').value,
ff=document.getElementById('ff').value,fl=document.getElementById('fl').value;
let r=DATA.filter(x=>(!fd||x.decision===fd)&&(!ff||(ff==='high'?x.high>0:x.flags.filter(f=>f.severity!=='info').length===0))
&&(!fl||(fl==='lab'?x.label:x.label&&x.label!==x.decision))&&(!q||x.search.includes(q)));
r.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];if(typeof x==='string')return sortDir*x.localeCompare(y);return sortDir*((x??-1)-(y??-1));});
const color=d=>d==='SAME_ENTITY'?'var(--same)':d==='REVIEW'?'var(--rev)':'var(--diff)';
document.getElementById('tb').innerHTML=r.map(x=>{const fl=x.flags.filter(f=>f.severity!=='info');
const lab=x.label?`<span class="${x.label===x.decision?'ok':'bad'}">${x.label===x.decision?'&#10003;':'&#10007;'} ${esc(x.label)}</span>`:'';
let h=`<tr class="row" data-id="${x.hit_id}"><td class="mono">${esc(x.alert_id)}<br>${esc(x.hit_id)}</td>
<td>${esc(x.customer_name)}<br><span class="sub">${esc(x.entity_type)} &middot; ${esc(x.link)}</span></td>
<td class="hide-sm">${esc(x.hit_name)}<br><span class="sub">${esc(x.hit_category)}</span></td>
<td><span class="pill ${x.decision}">${esc(x.decision.replace('_ENTITY',''))}</span><br><span class="sub mono">${esc(x.reason_codes.join(', '))}</span></td>
<td><b>${x.rating}</b> <span class="sub">${esc(x.rating_band)}</span><div class="bar"><i style="width:${x.rating}%;background:${color(x.decision)}"></i></div>
<span class="sub">step 2: ${x.intrinsic_score??'-'}</span></td>
<td><span class="cnt ${x.high?'h':''}">${x.high} high</span> <span class="sub">/ ${fl.length}</span>${fl.slice(0,2).map(f=>`<div class="flag ${f.severity}">${esc(f.code)}</div>`).join('')}</td>
<td class="hide-sm mono">${esc(x.customer_record_id||'none')}</td><td class="hide-sm">${lab}</td></tr>`;
if(open.has(x.hit_id)){h+=`<tr class="detail"><td colspan="8"><h4>Why</h4>${esc(x.explanation)}
<h4>Pipeline trail</h4><div class="mono">${x.path.map(esc).join('<br>')}</div>
<h4>Evidence cited</h4><div class="mono">${esc(x.evidence.join(', '))||'-'}</div>
<h4>Red flags by step</h4>${x.flags.map(f=>`<div class="flag ${f.severity}"><b>${esc(f.severity)}</b> [${esc(f.step)}] ${esc(f.code)}: ${esc(f.detail)}</div>`).join('')||'none'}
${x.sop_decision!==x.decision?`<h4>Strict SOP decision</h4>${esc(x.sop_decision)}`:''}
${x.label?`<h4>Practice label</h4>${esc(x.label)} (${esc(x.label_reasons)})`:''}</td></tr>`;}
return h;}).join('');
document.querySelectorAll('tr.row').forEach(tr=>tr.onclick=()=>{const id=tr.dataset.id;open.has(id)?open.delete(id):open.add(id);rows();});}
document.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;sortDir=sortKey===k?-sortDir:-1;sortKey=k;rows();});
['q','fd','ff','fl'].forEach(id=>document.getElementById(id).addEventListener('input',rows));
tiles();rows();
</script></body></html>
"""


def write_dashboard(results: list[HitResult], score: dict, path: Path) -> None:
    data = []
    for r in results:
        flags = [
            {"step": f.step, "severity": f.severity, "code": f.code, "detail": f.detail}
            for f in r.flags
        ]
        search = " ".join(
            [
                r.hit_id,
                r.alert_id,
                r.customer_name,
                r.hit_name,
                r.customer_record_id,
                r.decision,
                " ".join(r.reason_codes),
            ]
            + [f["code"] + " " + f["detail"] for f in flags]
        ).lower()
        data.append(
            {
                "hit_id": r.hit_id,
                "alert_id": r.alert_id,
                "customer_name": r.customer_name,
                "hit_name": r.hit_name,
                "entity_type": r.entity_type,
                "hit_category": r.hit_category,
                "link": r.link,
                "decision": r.decision,
                "sop_decision": r.sop_decision,
                "reason_codes": r.reason_codes,
                "customer_record_id": r.customer_record_id,
                "rating": r.rating,
                "rating_band": r.rating_band,
                "intrinsic_score": r.intrinsic_score,
                "high": r.high_flags,
                "flags": flags,
                "explanation": r.explanation,
                "evidence": r.evidence,
                "path": r.path,
                "label": r.label_decision,
                "label_reasons": r.label_reasons,
                "search": search,
            }
        )
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = (
        TEMPLATE.replace("__DATA__", payload)
        .replace("__SCORE__", json.dumps(score).replace("</", "<\\/"))
        .replace("__GENERATED__", html.escape(datetime.now().strftime("%Y-%m-%d %H:%M")))
        .replace("__N__", str(len(results)))
        .replace("__A__", str(len({r.alert_id for r in results})))
    )
    path.write_text(page, encoding="utf-8")
