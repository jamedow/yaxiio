const { useState, useEffect, useCallback, useMemo } = React;
const { ReactFlow, Handle, useNodesState, useEdgesState, MarkerType, Background, Controls } = window.ReactFlow;

const API = "http://" + window.location.hostname + ":3006/api";

// ── Custom Agent Node ──
function AgentNode({ data }) {
    const statusClass = data.state === "executing" ? "executing" : data.state === "fault" ? "fault" : "idle";
    return (
        <div className={`agent-node ${data.layer || "l4"}`}>
            <Handle type="target" position="top" style={{background:"var(--muted)"}} />
            <div className="node-header">
                <div className={`node-dot ${statusClass}`}></div>
                <div>
                    <div className="node-name">{data.label}</div>
                    <div className="node-role">{data.role || data.layer || ""}</div>
                </div>
            </div>
            {data.progress > 0 && data.progress < 100 && (
                <div className="progress-bar"><div className="progress-fill" style={{width:data.progress+"%"}}></div></div>
            )}
            {data.tools && (
                <div className="node-tools">
                    {data.tools.slice(0,4).map(t => <span key={t} className="tool-tag">{t}</span>)}
                </div>
            )}
            <Handle type="source" position="bottom" style={{background:"var(--muted)"}} />
        </div>
    );
}

// ── Layer Node ──
function LayerNode({ data }) {
    return (
        <div className={`agent-node ${data.layer}`}>
            <Handle type="target" position="left" style={{background:"var(--muted)"}} />
            <div className="node-header">
                <div className={`node-dot ${data.state === "active" ? "executing" : "idle"}`}></div>
                <div>
                    <div className="node-name">{data.label}</div>
                    <div className="node-role">{data.desc}</div>
                </div>
            </div>
            <Handle type="source" position="right" style={{background:"var(--muted)"}} />
        </div>
    );
}

const nodeTypes = { agentNode: AgentNode, layerNode: LayerNode };

// ── Main App ──
function App() {
    const [nodes, setNodes, onNodesChange] = useNodesState([]);
    const [edges, setEdges, onEdgesChange] = useEdgesState([]);
    const [activeTab, setActiveTab] = useState("flow");
    const [sidebar, setSidebar] = useState({ type: "tasks", data: [] });
    const [stats, setStats] = useState({ tasks: 0, agents: 0, score: 0 });

    const onConnect = useCallback((params) => setEdges(eds => [...eds, params]), []);

    // Build flow graph from API data
    const buildGraph = useCallback((data) => {
        const ns = [], es = [];
        const layers = [
            { id: "L1", label: "L1 感知", desc: "Intent Recognition", layer: "l1" },
            { id: "L2", label: "L2 规划", desc: "Task Decomposition", layer: "l2" },
            { id: "L3", label: "L3 协调", desc: "Agent Scheduling", layer: "l3" },
            { id: "L4", label: "L4 执行", desc: "Task Execution", layer: "l4" },
            { id: "L5", label: "L5 进化", desc: "Quality Scoring", layer: "l5" },
        ];
        layers.forEach((l, i) => {
            ns.push({ id: l.id, type: "layerNode", position: { x: 80 + i * 200, y: 80 }, data: { ...l, state: "active" } });
            if (i > 0) es.push({ id: "e-"+i, source: layers[i-1].id, target: l.id, animated: true, style: { stroke: "var(--muted)" }, markerEnd: { type: MarkerType.ArrowClosed } });
        });
        (data.agents || []).forEach((a, i) => {
            const col = i % 5, row = Math.floor(i / 5);
            ns.push({ id: "a-"+i, type: "agentNode", position: { x: 80 + col * 200, y: 250 + row * 140 },
                data: { label: a.name, layer: "l4", state: a.state, progress: a.progress || 0, tools: a.tools || [], role: a.quadrant } });
            es.push({ id: "ea-"+i, source: "L3", target: "a-"+i, style: { stroke: "var(--yellow)", strokeDasharray: "5,5" }, animated: true });
            es.push({ id: "eb-"+i, source: "a-"+i, target: "L4", style: { stroke: "var(--red)", strokeDasharray: "5,5" } });
        });
        setNodes(ns); setEdges(es);
    }, []);

    // Poll APIs
    useEffect(() => {
        const poll = async () => {
            try {
                const [wRes, aRes, sRes] = await Promise.all([
                    fetch(API + "/workflow"), fetch(API + "/agents"), fetch(API + "/scores")
                ]);
                const w = await wRes.json(), a = await aRes.json(), s = await sRes.json();
                setStats({ tasks: (w.tasks||[]).length, agents: a.length, score: s.length ? s[0].ai_score : 0 });
                if (activeTab === "flow") buildGraph(w.current_flow || {});
                if (activeTab === "agents") setSidebar({ type: "agents", data: a });
                if (activeTab === "scores") setSidebar({ type: "scores", data: s });
            } catch(e) {}
        };
        poll();
        const interval = setInterval(poll, 8000);
        return () => clearInterval(interval);
    }, [activeTab, buildGraph]);

    return (
        <div className="app">
            <header>
                <h1>⚡ Yaxiio Dashboard</h1>
                <div className="stats">
                    <span><span className="stat-label">Tasks</span><span className="stat-value">{stats.tasks}</span></span>
                    <span><span className="stat-label">Agents</span><span className="stat-value">{stats.agents}</span></span>
                    <span><span className="stat-label">Latest Score</span><span className="stat-value">{stats.score}</span></span>
                </div>
                <div className="tabs">
                    {["flow","agents","scores"].map(t => (
                        <button key={t} className={`tab ${activeTab === t ? "active" : ""}`} onClick={() => setActiveTab(t)}>
                            {t === "flow" ? "工作流" : t === "agents" ? "Agent管理" : "评分"}
                        </button>
                    ))}
                </div>
            </header>
            <main>
                <div className="flow-panel">
                    {activeTab === "flow" && (
                        <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
                            onConnect={onConnect} nodeTypes={nodeTypes} fitView style={{background:"var(--bg)"}}>
                            <Background color="var(--border)" gap={20} />
                            <Controls />
                        </ReactFlow>
                    )}
                    {activeTab === "agents" && (
                        <div style={{padding:20}}>
                            {sidebar.data.map(a => (
                                <div key={a.name} className="agent-list-item">
                                    <span><span className={`node-dot ${a.running?"executing":"idle"}`} style={{display:"inline-block",marginRight:8}}></span>{a.name}</span>
                                    <span style={{fontSize:11,color:"var(--muted)"}}>{a.quadrant} · {a.skills?.[0]||""}</span>
                                </div>
                            ))}
                        </div>
                    )}
                    {activeTab === "scores" && (
                        <div style={{padding:20}}>
                            {sidebar.data.map(s => (
                                <div key={s.task_id} style={{marginBottom:12}}>
                                    <div style={{fontSize:13,marginBottom:4,fontFamily:"monospace"}}>{s.task_id}</div>
                                    <div className="score-bar">
                                        <span className="score-label">AI</span>
                                        <div className="score-track"><div className="score-fill" style={{width:(s.ai_score||0)*10+"%",background:"var(--yellow)"}}></div></div>
                                        <span style={{fontSize:12}}>{s.ai_score||"-"}</span>
                                    </div>
                                    {s.human_score && (
                                        <div className="score-bar">
                                            <span className="score-label">Human</span>
                                            <div className="score-track"><div className="score-fill" style={{width:s.human_score*10+"%",background:"var(--green)"}}></div></div>
                                            <span style={{fontSize:12}}>{s.human_score}</span>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
                {activeTab === "flow" && (
                    <div className="sidebar">
                        <div className="card"><h3>📋 最近任务</h3>
                            {(sidebar.data||[]).slice(0,8).map(t => (
                                <div key={t.id} className="agent-list-item">
                                    <span style={{fontFamily:"monospace",fontSize:12}}>{t.id}</span>
                                    <span style={{fontSize:11,color:"var(--muted)"}}>{t.subtasks} subtasks</span>
                                </div>
                            ))}
                        </div>
                        <div className="card"><h3>🔧 快速操作</h3>
                            <button className="btn btn-primary" style={{width:"100%",marginBottom:8}} onClick={() => fetch(API.replace("3006","3005")+"/api/review",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({task_id:prompt("Task ID:"),reviewer_id:"jamedow",scores:{accuracy:7,completeness:7},overall:7,comment:""})}).then(r=>r.json()).then(d=>alert("Submitted: "+d.status))}>
                                提交评分
                            </button>
                            <button className="btn" style={{width:"100%"}} onClick={() => alert("Coming soon")}>
                                创建 Agent
                            </button>
                        </div>
                    </div>
                )}
            </main>
        </div>
    );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
