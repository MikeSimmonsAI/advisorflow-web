import React, { useState } from "react";

/**
 * SCI voicemail operations prototype.
 * Independent frontend workstream; mock data only, no backend calls.
 * Integrate with Claude's eventual voice endpoints after contracts are finalized.
 */
const initial = [
  { id: "VM-1001", name: "Patricia R.", phone: "(205) 555-0142", location: "Alabama Memorial", status: "Needs callback", duration: "0:42", greeting: "Alabama Memorial greeting", time: "10:42 AM" },
  { id: "VM-1002", name: "Marcus T.", phone: "(850) 555-0198", location: "Pensacola Gardens", status: "Needs callback", duration: "1:13", greeting: "Pensacola Gardens greeting", time: "10:18 AM" },
  { id: "VM-1003", name: "Unknown caller", phone: "Unknown", location: "Unassigned", status: "Review", duration: "0:25", greeting: "General greeting", time: "9:57 AM" },
];
export default function SciVoicemailPreview() {
  const [items, setItems] = useState(initial);
  const [selected, setSelected] = useState(initial[0].id);
  const [filter, setFilter] = useState("All");
  const active = items.find(x => x.id === selected) || items[0];
  const shown = items.filter(x => filter === "All" || x.status === filter);
  const updateStatus = (status) => setItems(prev => prev.map(x => x.id === selected ? { ...x, status } : x));
  return (
    <main style={{fontFamily:"Inter,system-ui,sans-serif",padding:28,maxWidth:1100,margin:"auto",color:"#17243b"}}>
      <div style={{fontSize:12,color:"#687b97",letterSpacing:2,fontWeight:700}}>EVOSYS PRO / SCI OPERATIONS</div>
      <h1 style={{margin:"8px 0"}}>Voicemail & Callback Center</h1>
      <p style={{color:"#687b97"}}>Prototype with sample data — not connected to live Twilio or SCI contacts.</p>
      <div style={{display:"flex",gap:12,flexWrap:"wrap",margin:"20px 0"}}>
        {[[ "Voicemails",items.length ],[ "Need callbacks",items.filter(x=>x.status==="Needs callback").length ],[ "Unassigned",items.filter(x=>x.location==="Unassigned").length ]].map(([label,value])=><div key={label} style={{border:"1px solid #dce5f0",borderRadius:12,padding:18,minWidth:170,background:"#f7faff"}}><div style={{fontSize:13,color:"#62718b"}}>{label}</div><strong style={{fontSize:30}}>{value}</strong></div>)}
      </div>
      <div style={{display:"flex",gap:10,marginBottom:14}}>
        {["All","Needs callback","Review","Completed"].map(x=><button key={x} onClick={()=>setFilter(x)} style={{border:"1px solid #cad6e7",borderRadius:20,padding:"8px 14px",background:filter===x?"#183b77":"white",color:filter===x?"white":"#183b77",cursor:"pointer"}}>{x}</button>)}
      </div>
      <div style={{display:"grid",gridTemplateColumns:"minmax(260px,1fr) minmax(320px,1.2fr)",gap:16}}>
        <section style={{border:"1px solid #dce5f0",borderRadius:14,overflow:"hidden"}}>
          {shown.map(x=><button key={x.id} onClick={()=>setSelected(x.id)} style={{display:"block",width:"100%",textAlign:"left",padding:16,border:"none",borderBottom:"1px solid #e7edf5",background:active.id===x.id?"#eaf2ff":"white",cursor:"pointer"}}>
            <strong>{x.name}</strong><span style={{float:"right",fontSize:12,color:"#64748b"}}>{x.time}</span><div style={{fontSize:13,color:"#5b6b85",marginTop:5}}>{x.location} · {x.duration}</div><div style={{fontSize:12,marginTop:6,color:"#1b5b9e"}}>{x.status}</div>
          </button>)}
          {!shown.length && <p style={{padding:18}}>No voicemails in this filter.</p>}
        </section>
        <section style={{border:"1px solid #dce5f0",borderRadius:14,padding:22}}>
          <div style={{fontSize:12,color:"#64748b"}}>SELECTED MESSAGE · {active.id}</div>
          <h2>{active.name}</h2>
          <p>{active.phone}</p>
          <p><strong>Cemetery:</strong> {active.location}</p>
          <p><strong>Greeting used:</strong> {active.greeting}</p>
          <p><strong>Recording:</strong> {active.duration} · Audio playback pending backend integration</p>
          <p><strong>Callback:</strong> {active.status}</p>
          <div style={{display:"flex",gap:10,flexWrap:"wrap",marginTop:24}}>
            <button onClick={()=>updateStatus("Completed")} style={{padding:"10px 14px",border:0,borderRadius:8,color:"white",background:"#17744d",cursor:"pointer"}}>Mark callback complete</button>
            <button onClick={()=>updateStatus("Needs callback")} style={{padding:"10px 14px",border:"1px solid #b9c9df",borderRadius:8,background:"white",cursor:"pointer"}}>Reopen callback</button>
          </div>
        </section>
      </div>
      <p style={{fontSize:12,color:"#64748b",marginTop:20}}>All names and phone numbers are fictional demo examples. No calls or messages are sent.</p>
    </main>
  );
}
