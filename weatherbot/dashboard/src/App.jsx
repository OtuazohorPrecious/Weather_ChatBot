import { useState, useEffect, useCallback } from "react";
import { LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

const API     = (window.__ENV__ && window.__ENV__.ANOMALY_API_URL)  || "http://localhost:8002";
const LOG_API = (window.__ENV__ && window.__ENV__.LOGGING_API_URL) || "http://localhost:8004";
const POLL_MS = 8000;

const fmt     = (ts) => { if (!ts) return "—"; return new Date(ts).toLocaleTimeString("en-GB",{hour:"2-digit",minute:"2-digit",second:"2-digit"}); };
const fmtFull = (ts) => { if (!ts) return "—"; return new Date(ts).toLocaleString("en-GB"); };
const SEV_COLOR = { high:"#c0392b", medium:"#d35400", low:"#d4ac0d" };
const SEV_BG    = { high:"#fdf2f0", medium:"#fef5ec", low:"#fefde7" };
const ACT_COLOR = { restart_container:"#c0392b", rate_limit:"#d35400", monitor:"#7f8c8d", circuit_open:"#6c3483" };
const ACT_LABEL = { restart_container:"Restart", rate_limit:"Rate Limit", monitor:"Monitor", circuit_open:"Circuit Open" };
const TYPE_LABEL= { service_error:"Service Error", high_latency:"High Latency", statistical_anomaly:"Statistical" };
const CB_STATE  = { closed:{color:"#1a7a4a",bg:"#eafaf1",label:"CLOSED"}, open:{color:"#c0392b",bg:"#fdf2f0",label:"OPEN"}, half_open:{color:"#d35400",bg:"#fef5ec",label:"HALF-OPEN"} };
const SVC_COLORS= ["#1a5fa8","#1a7a4a","#d35400","#6c3483","#7f8c8d"];

async function fetchJSON(url) {
  const r = await fetch(url, { signal: AbortSignal.timeout(6000) });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}

// ── Base components ────────────────────────────────────────────────

function Section({ title, accent, children, badge }) {
  return (
    <div style={{ background:"#fff", border:"1px solid #e8ecf0", borderRadius:8, overflow:"hidden", marginBottom:20 }}>
      <div style={{ background:accent||"#1a5fa8", padding:"10px 20px", display:"flex", justifyContent:"space-between", alignItems:"center" }}>
        <span style={{ color:"#fff", fontWeight:700, fontSize:12, letterSpacing:"0.06em", textTransform:"uppercase", fontFamily:"'DM Mono',monospace" }}>{title}</span>
        {badge !== undefined && <span style={{ background:"rgba(255,255,255,0.25)", color:"#fff", fontSize:11, padding:"2px 10px", borderRadius:12, fontFamily:"'DM Mono',monospace" }}>{badge}</span>}
      </div>
      <div style={{ padding:"16px 20px" }}>{children}</div>
    </div>
  );
}

function StatCard({ label, value, color, sub }) {
  return (
    <div style={{ background:"#fff", border:"1px solid #e8ecf0", borderRadius:8, padding:"18px 22px", borderTop:"3px solid "+(color||"#1a5fa8") }}>
      <div style={{ fontSize:28, fontWeight:800, color:color||"#1a3a5c", fontFamily:"'DM Mono',monospace", lineHeight:1 }}>{value ?? "—"}</div>
      <div style={{ fontSize:10, color:"#6b7280", marginTop:6, letterSpacing:"0.1em", textTransform:"uppercase" }}>{label}</div>
      {sub && <div style={{ fontSize:11, color:"#9ca3af", marginTop:4 }}>{sub}</div>}
    </div>
  );
}

function SevBadge({ sev }) {
  return <span style={{ fontSize:10, fontWeight:700, color:SEV_COLOR[sev]||"#555", background:SEV_BG[sev]||"#f5f5f5", padding:"2px 8px", borderRadius:4, letterSpacing:"0.05em", fontFamily:"'DM Mono',monospace" }}>{(sev||"").toUpperCase()}</span>;
}

function ActBadge({ act }) {
  const c = ACT_COLOR[act]||"#7f8c8d";
  return <span style={{ fontSize:10, fontWeight:700, color:c, background:c+"18", padding:"2px 8px", borderRadius:4, letterSpacing:"0.05em", border:"1px solid "+c+"40", fontFamily:"'DM Mono',monospace" }}>{ACT_LABEL[act]||act}</span>;
}

// ── Anomaly Feed ───────────────────────────────────────────────────

function Feed({ anomalies, total, sel, onSel }) {
  return (
    <Section title="Anomaly Detection Feed" accent="#c0392b" badge={`${total} total`}>
      {anomalies.length === 0
        ? <p style={{ color:"#9ca3af", fontSize:13, fontStyle:"italic", margin:0 }}>No anomalies detected. System is healthy.</p>
        : <>
            <div style={{ display:"grid", gridTemplateColumns:"auto 100px 160px 1fr auto", gap:"0 16px", padding:"0 12px 8px", borderBottom:"1px solid #f0f0f0", marginBottom:6 }}>
              {["Severity","Service","Type","Description","Time"].map(h=><span key={h} style={{ fontSize:10, color:"#9ca3af", fontWeight:700, letterSpacing:"0.08em", textTransform:"uppercase" }}>{h}</span>)}
            </div>
            <div style={{ maxHeight:340, overflowY:"auto" }}>
              {anomalies.map((a)=>(
                <div key={a.id} onClick={()=>onSel(sel&&sel.id===a.id?null:a)}
                  style={{ cursor:"pointer", display:"grid", gridTemplateColumns:"auto 100px 160px 1fr auto", gap:"0 16px", padding:"9px 12px", borderRadius:6, marginBottom:2, alignItems:"center",
                    background:sel&&sel.id===a.id?"#eef3fc":a.severity==="high"?"#fff8f8":"#fafafa",
                    borderLeft:"3px solid "+(SEV_COLOR[a.severity]||"#ccc"), transition:"background 0.15s" }}>
                  <SevBadge sev={a.severity}/>
                  <span style={{ fontSize:12, color:"#374151", fontFamily:"'DM Mono',monospace" }}>{a.service}</span>
                  <span style={{ fontSize:12, color:"#374151" }}>{TYPE_LABEL[a.anomaly_type]||a.anomaly_type}</span>
                  <span style={{ fontSize:12, color:"#4b5563" }}>{a.description}</span>
                  <span style={{ fontSize:11, color:"#9ca3af", whiteSpace:"nowrap", fontFamily:"'DM Mono',monospace" }}>{fmt(a.timestamp)}</span>
                </div>
              ))}
            </div>
            <p style={{ fontSize:11, color:"#9ca3af", margin:"8px 0 0", fontStyle:"italic" }}>Click a row to view the XAI explanation</p>
          </>
      }
      {sel && sel.explanation && (
        <div style={{ marginTop:16, padding:"16px 18px", background:"#f0f4ff", border:"1px solid #c7d5f7", borderRadius:8, borderLeft:"4px solid #1a5fa8" }}>
          <div style={{ fontSize:11, fontWeight:700, color:"#1a5fa8", letterSpacing:"0.08em", textTransform:"uppercase", marginBottom:12, fontFamily:"'DM Mono',monospace" }}>
            XAI Explanation — {sel.service} — {TYPE_LABEL[sel.anomaly_type]||sel.anomaly_type} — {fmtFull(sel.timestamp)}
          </div>
          <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr 1fr", gap:16 }}>
            <div>
              <div style={{ fontSize:10, fontWeight:700, color:"#6b7280", letterSpacing:"0.08em", textTransform:"uppercase", marginBottom:6 }}>Detection Reasons</div>
              {(sel.explanation.reasons||[]).map((r,i)=>(
                <div key={i} style={{ fontSize:12, color:"#374151", marginBottom:5, paddingLeft:10, borderLeft:"2px solid #1a5fa8", lineHeight:1.5 }}>{r}</div>
              ))}
            </div>
            <div>
              <div style={{ fontSize:10, fontWeight:700, color:"#6b7280", letterSpacing:"0.08em", textTransform:"uppercase", marginBottom:6 }}>Feature Values</div>
              {Object.entries(sel.explanation.feature_values||{}).map(([k,v])=>(
                <div key={k} style={{ display:"flex", justifyContent:"space-between", fontSize:12, color:"#374151", marginBottom:4, fontFamily:"'DM Mono',monospace" }}>
                  <span style={{ color:"#6b7280" }}>{k}</span>
                  <span style={{ fontWeight:600 }}>{typeof v==="number"?v.toFixed(2):v}</span>
                </div>
              ))}
            </div>
            <div>
              <div style={{ fontSize:10, fontWeight:700, color:"#6b7280", letterSpacing:"0.08em", textTransform:"uppercase", marginBottom:6 }}>Baseline Comparison</div>
              {sel.explanation.baseline_stats && (
                <>
                  <div style={{ fontSize:12, color:"#6b7280", marginBottom:4 }}>Trained on {sel.explanation.baseline_stats.n_samples} samples</div>
                  {["mean_latency","std_latency","error_rate"].map(k=>(
                    <div key={k} style={{ display:"flex", justifyContent:"space-between", fontSize:12, fontFamily:"'DM Mono',monospace", marginBottom:4 }}>
                      <span style={{ color:"#6b7280" }}>{k.replace(/_/g," ")}</span>
                      <span style={{ color:"#374151", fontWeight:600 }}>{sel.explanation.baseline_stats[k]!==undefined?Number(sel.explanation.baseline_stats[k]).toFixed(2):"—"}</span>
                    </div>
                  ))}
                  <div style={{ marginTop:8, fontSize:12, fontFamily:"'DM Mono',monospace", color:SEV_COLOR[sel.severity] }}>
                    Isolation score: <strong>{sel.score?sel.score.toFixed(4):"—"}</strong>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}

// ── Healing History ────────────────────────────────────────────────

function Healing({ actions, stats }) {
  return (
    <Section title="Healing Actions History" accent="#d35400" badge={`${actions.length} actions`}>
      {actions.length===0
        ? <p style={{ color:"#9ca3af", fontSize:13, fontStyle:"italic", margin:0 }}>No healing actions yet.</p>
        : <>
            <div style={{ display:"grid", gridTemplateColumns:"auto 160px 80px 1fr auto", gap:"0 16px", padding:"0 12px 8px", borderBottom:"1px solid #f0f0f0", marginBottom:6 }}>
              {["Action","Service","Severity","Reason","Time"].map(h=><span key={h} style={{ fontSize:10, color:"#9ca3af", fontWeight:700, letterSpacing:"0.08em", textTransform:"uppercase" }}>{h}</span>)}
            </div>
            <div style={{ maxHeight:320, overflowY:"auto" }}>
              {actions.map((a)=>(
                <div key={a.id} style={{ display:"grid", gridTemplateColumns:"auto 160px 80px 1fr auto", gap:"0 16px", padding:"9px 12px", borderRadius:6, marginBottom:2, alignItems:"center",
                  background:a.success?"#f8fffe":"#fff8f8", borderLeft:"3px solid "+(a.success?"#1a7a4a":"#c0392b") }}>
                  <ActBadge act={a.action}/>
                  <span style={{ fontSize:12, color:"#374151", fontFamily:"'DM Mono',monospace" }}>{a.service}</span>
                  <SevBadge sev={a.severity}/>
                  <span style={{ fontSize:12, color:"#4b5563" }}>{a.reason||"—"}</span>
                  <div style={{ textAlign:"right" }}>
                    <div style={{ fontSize:10, color:a.success?"#1a7a4a":"#c0392b", fontWeight:700, fontFamily:"'DM Mono',monospace" }}>{a.success?"SUCCESS":"FAILED"}</div>
                    <div style={{ fontSize:11, color:"#9ca3af", fontFamily:"'DM Mono',monospace", whiteSpace:"nowrap" }}>{fmt(a.executed_at)}</div>
                  </div>
                </div>
              ))}
            </div>
          </>
      }
      {stats && (stats.healing_by_action||[]).length>0 && (
        <div style={{ marginTop:16, display:"flex", gap:12, flexWrap:"wrap" }}>
          {(stats.healing_by_action||[]).map(a=>(
            <div key={a.action} style={{ padding:"10px 16px", background:"#fdf6f0", border:"1px solid #f0d9c0", borderRadius:8, minWidth:130 }}>
              <ActBadge act={a.action}/>
              <div style={{ fontSize:22, fontWeight:800, color:"#d35400", marginTop:6, fontFamily:"'DM Mono',monospace" }}>{a.count}</div>
              <div style={{ fontSize:11, color:"#9ca3af" }}>{a.successful} successful</div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}

// ── Infrastructure ─────────────────────────────────────────────────

function Infra({ health }) {
  const cbs = (health&&health.circuit_breakers)||{};
  const rls = (health&&health.rate_limiters)||{};
  const mdl = (health&&health.models)||{};
  return (
    <div>
      <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:16, marginBottom:16 }}>
        <Section title="Circuit Breakers" accent="#6c3483">
          {Object.keys(cbs).length===0
            ? <p style={{ color:"#9ca3af", fontSize:13, fontStyle:"italic", margin:0 }}>No circuit breakers triggered yet. All services operational.</p>
            : Object.entries(cbs).map(([svc,info])=>{
                const st = CB_STATE[info.state]||{color:"#555",bg:"#f5f5f5",label:(info.state||"").toUpperCase()};
                return (
                  <div key={svc} style={{ display:"flex", justifyContent:"space-between", alignItems:"center", padding:"10px 14px", marginBottom:6, background:st.bg, borderRadius:6, border:"1px solid "+st.color+"30", borderLeft:"3px solid "+st.color }}>
                    <div>
                      <div style={{ fontSize:13, fontWeight:600, color:"#1a1a1a", fontFamily:"'DM Mono',monospace" }}>{svc}</div>
                      <div style={{ fontSize:11, color:"#6b7280", marginTop:2 }}>Failures recorded: {info.failure_count}</div>
                    </div>
                    <span style={{ fontSize:11, fontWeight:700, color:st.color, background:"#fff", border:"1px solid "+st.color, padding:"4px 10px", borderRadius:4, fontFamily:"'DM Mono',monospace" }}>{st.label}</span>
                  </div>
                );
              })
          }
        </Section>
        <Section title="Rate Limiters" accent="#1a7a4a">
          {Object.keys(rls).length===0
            ? <p style={{ color:"#9ca3af", fontSize:13, fontStyle:"italic", margin:0 }}>No services throttled. Normal request rates.</p>
            : Object.entries(rls).map(([svc,info])=>{
                const pct = Math.min(100,(info.tokens/20)*100);
                const c = info.throttled?"#d35400":"#1a7a4a";
                return (
                  <div key={svc} style={{ marginBottom:14 }}>
                    <div style={{ display:"flex", justifyContent:"space-between", marginBottom:5 }}>
                      <span style={{ fontSize:13, fontWeight:600, color:"#1a1a1a", fontFamily:"'DM Mono',monospace" }}>{svc}</span>
                      <span style={{ fontSize:11, fontWeight:600, color:c }}>{info.throttled?"Throttled · "+info.current_rate+" req/s":"Normal · "+info.current_rate+" req/s"}</span>
                    </div>
                    <div style={{ background:"#e5e7eb", borderRadius:4, height:7, overflow:"hidden" }}>
                      <div style={{ height:"100%", width:pct+"%", background:c, borderRadius:4, transition:"width 0.5s" }}/>
                    </div>
                    <div style={{ fontSize:10, color:"#9ca3af", marginTop:3, fontFamily:"'DM Mono',monospace" }}>{info.tokens!==undefined?Number(info.tokens).toFixed(1):"—"} / 20 tokens available</div>
                  </div>
                );
              })
          }
        </Section>
      </div>
      {Object.keys(mdl).length>0 && (
        <Section title="ML Model Status — Per-Service Isolation Forest Baselines" accent="#1a5fa8">
          <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fill,minmax(240px,1fr))", gap:12 }}>
            {Object.entries(mdl).map(([svc,info])=>(
              <div key={svc} style={{ padding:"12px 14px", background:"#f0f7ff", border:"1px solid #c7d5f7", borderRadius:6, borderLeft:"3px solid #1a5fa8" }}>
                <div style={{ fontSize:12, fontWeight:700, color:"#1a5fa8", marginBottom:4, fontFamily:"'DM Mono',monospace" }}>{svc}</div>
                <div style={{ fontSize:11, color:"#6b7280", marginBottom:6 }}>Last trained: {fmtFull(info.last_trained)}</div>
                {info.baseline_stats && (
                  <div style={{ display:"flex", gap:14 }}>
                    <span style={{ fontSize:11, fontFamily:"'DM Mono',monospace", color:"#374151" }}>μ={Number(info.baseline_stats.mean_latency||0).toFixed(0)} ms</span>
                    <span style={{ fontSize:11, fontFamily:"'DM Mono',monospace", color:"#374151" }}>n={info.baseline_stats.n_samples}</span>
                    <span style={{ fontSize:11, fontFamily:"'DM Mono',monospace", color:"#374151" }}>err={(Number(info.baseline_stats.error_rate||0)*100).toFixed(1)}%</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

// ── Telemetry Charts ───────────────────────────────────────────────

function Charts({ logs, stats }) {
  const latData = (() => {
    if (!logs||logs.length===0) return [];
    const b={};
    [...logs].reverse().slice(0,80).forEach(l=>{ const t=fmt(l.timestamp); if(!b[t]) b[t]={time:t}; b[t][l.service]=l.latency_ms; });
    return Object.values(b).slice(-20);
  })();
  const svcData = stats?(stats.anomalies_by_service||[]):[];
  const SVCS = ["weather_service","nlu_service","responder_service","frontend"];
  return (
    <div style={{ display:"grid", gridTemplateColumns:"2fr 1fr", gap:16 }}>
      <Section title="Per-Service Request Latency (ms)" accent="#1a5fa8">
        {latData.length===0
          ? <p style={{ color:"#9ca3af", fontSize:13 }}>No log data yet.</p>
          : <ResponsiveContainer width="100%" height={220}>
              <LineChart data={latData} margin={{top:4,right:10,left:-20,bottom:0}}>
                <XAxis dataKey="time" tick={{fill:"#9ca3af",fontSize:10}} />
                <YAxis tick={{fill:"#9ca3af",fontSize:10}} />
                <Tooltip contentStyle={{background:"#fff",border:"1px solid #e8ecf0",borderRadius:8,fontSize:12}} />
                {SVCS.map((s,i)=><Line key={s} type="monotone" dataKey={s} stroke={SVC_COLORS[i]} strokeWidth={2} dot={false} connectNulls />)}
              </LineChart>
            </ResponsiveContainer>
        }
        <div style={{ display:"flex", gap:16, marginTop:8, flexWrap:"wrap" }}>
          {SVCS.map((s,i)=><span key={s} style={{ fontSize:11, color:SVC_COLORS[i], display:"flex", alignItems:"center", gap:5 }}><span style={{ width:16, height:2, background:SVC_COLORS[i], display:"inline-block", borderRadius:2 }}/>{s.replace("_service","")}</span>)}
        </div>
      </Section>
      <Section title="Anomalies by Service" accent="#c0392b">
        {svcData.length===0
          ? <p style={{ color:"#9ca3af", fontSize:13 }}>No anomaly data yet.</p>
          : <ResponsiveContainer width="100%" height={220}>
              <BarChart data={svcData} margin={{top:4,right:10,left:-20,bottom:40}}>
                <XAxis dataKey="service" tick={{fill:"#9ca3af",fontSize:9}} angle={-25} textAnchor="end" />
                <YAxis tick={{fill:"#9ca3af",fontSize:10}} />
                <Tooltip contentStyle={{background:"#fff",border:"1px solid #e8ecf0",borderRadius:8,fontSize:12}} />
                <Bar dataKey="total" radius={[4,4,0,0]}>
                  {svcData.map((_,i)=><Cell key={i} fill={SVC_COLORS[i%SVC_COLORS.length]} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
        }
      </Section>
    </div>
  );
}

// ── Root App ───────────────────────────────────────────────────────

export default function App() {
  const [health,    setHealth]    = useState(null);
  const [anomalies, setAnomalies] = useState([]);
  const [healing,   setHealing]   = useState([]);
  const [stats,     setStats]     = useState(null);
  const [logs,      setLogs]      = useState([]);
  const [sel,       setSel]       = useState(null);
  const [updated,   setUpdated]   = useState(null);
  const [online,    setOnline]    = useState(true);
  const [tab,       setTab]       = useState("anomalies");

  const poll = useCallback(async () => {
    try {
      const [h,a,he,s,l] = await Promise.allSettled([
        fetchJSON(API+"/health"),
        fetchJSON(API+"/anomalies/recent?limit=100"),
        fetchJSON(API+"/healing/history?limit=50"),
        fetchJSON(API+"/stats"),
        fetchJSON(LOG_API+"/logs?limit=200"),
      ]);
      if (h.status==="fulfilled")  setHealth(h.value);
      if (a.status==="fulfilled")  setAnomalies(a.value.anomalies||[]);
      if (he.status==="fulfilled") setHealing(he.value.actions||[]);
      if (s.status==="fulfilled")  setStats(s.value);
      if (l.status==="fulfilled")  setLogs(l.value||[]);
      setOnline(true); setUpdated(new Date());
    } catch(_) { setOnline(false); }
  },[]);

  useEffect(()=>{ poll(); const t=setInterval(poll,POLL_MS); return ()=>clearInterval(t); },[poll]);

  const total    = stats
    ? (stats.anomalies_by_service||[]).reduce((s,r)=>s+Number(r.total||0),0)
    : anomalies.length;
  const actTotal  = stats
    ? (stats.healing_by_action||[]).reduce((s,r)=>s+Number(r.count||0),0)
    : healing.length;
  const restarts  = stats
    ? Number(((stats.healing_by_action||[]).find(r=>r.action==="restart_container")||{}).count||0)
    : healing.filter(h=>h.action==="restart_container").length;
  const avgScore  = anomalies.length>0
    ? (anomalies.reduce((s,a)=>s+(a.score||0),0)/anomalies.length).toFixed(3)
    : "—";
  const models   = health===null ? "—" : Object.keys(health.models||{}).length;
  const cbOpen   = health?Object.values(health.circuit_breakers||{}).filter(cb=>cb.state!=="closed").length:0;

  const TABS=[["anomalies","Anomaly Feed"],["healing","Healing History"],["infra","Infrastructure"],["charts","Telemetry"]];

  return (
    <div style={{ minHeight:"100vh", background:"#f4f6f8", color:"#1a1a1a", fontFamily:"'DM Sans','Segoe UI',system-ui,sans-serif" }}>
      {/* Header */}
      <div style={{ background:"#1a3a5c", borderBottom:"1px solid #0f2540", padding:"0 32px", position:"sticky", top:0, zIndex:100 }}>
        <div style={{ display:"flex", justifyContent:"space-between", alignItems:"center", height:52 }}>
          <div style={{ display:"flex", alignItems:"center", gap:20 }}>
            <div>
              <div style={{ fontSize:14, fontWeight:700, color:"#fff", letterSpacing:"0.04em", fontFamily:"'DM Mono',monospace" }}>WEATHERBOT  ·  SELF-HEALING MONITOR</div>
              <div style={{ fontSize:10, color:"#7098c0", marginTop:1 }}>ETU-LETI  ·  Otuazohor P.O.  ·  2026</div>
            </div>
            <div style={{ width:1, height:28, background:"rgba(255,255,255,0.15)" }}/>
            <div style={{ display:"flex", gap:2 }}>
              {TABS.map(([k,l])=>(
                <button key={k} onClick={()=>setTab(k)} style={{ background:tab===k?"rgba(255,255,255,0.15)":"transparent", border:"none", borderBottom:tab===k?"2px solid #5da0e0":"2px solid transparent", color:tab===k?"#fff":"#7098c0", fontSize:12, fontWeight:tab===k?600:400, padding:"14px 16px", cursor:"pointer", letterSpacing:"0.02em", transition:"all 0.15s" }}>
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div style={{ display:"flex", alignItems:"center", gap:16 }}>
            {cbOpen>0 && <span style={{ fontSize:11, background:"#c0392b", color:"#fff", padding:"3px 10px", borderRadius:4, fontWeight:700, fontFamily:"'DM Mono',monospace" }}>{cbOpen} CB OPEN</span>}
            <span style={{ fontSize:11, color:"#7098c0", fontFamily:"'DM Mono',monospace" }}>{updated?"updated "+fmt(updated):"connecting..."}</span>
            <div style={{ display:"flex", alignItems:"center", gap:6, fontSize:12 }}>
              <span style={{ width:7, height:7, borderRadius:"50%", background:online?"#2ecc71":"#c0392b", display:"inline-block" }}/>
              <span style={{ color:online?"#2ecc71":"#c0392b", fontWeight:600 }}>{online?"LIVE":"OFFLINE"}</span>
            </div>
          </div>
        </div>
      </div>

      <div style={{ padding:"24px 32px" }}>
        {/* KPI row */}
        <div style={{ display:"grid", gridTemplateColumns:"repeat(5,1fr)", gap:12, marginBottom:24 }}>
          <StatCard label="Anomalies Detected"  value={total}    color="#1a5fa8" sub="across all services" />
          <StatCard label="Healing Actions"     value={actTotal}  color="#d35400" sub={restarts+" restarts · "+(actTotal-restarts)+" other"} />
          <StatCard label="Avg Isolation Score" value={avgScore} color="#c0392b" sub="lower = more anomalous" />
          <StatCard label="ML Models Active"    value={models}   color="#6c3483" sub={models===0?"Training in progress...":"Isolation Forest per-service"} />
          <StatCard label="Circuit Breakers"    value={cbOpen===0?"All Closed":cbOpen+" Open"} color={cbOpen>0?"#c0392b":"#1a7a4a"} />
        </div>

        {tab==="anomalies" && <Feed     anomalies={anomalies} total={total} sel={sel} onSel={setSel} />}
        {tab==="healing"   && <Healing  actions={healing} stats={stats} />}
        {tab==="infra"     && <Infra    health={health} />}
        {tab==="charts"    && <Charts   logs={logs} stats={stats} />}
      </div>
    </div>
  );
}
