import os
import shutil
import socket
import qrcode
import uvicorn
import json
import asyncio
import sys
import io
import base64
import subprocess
import platform
import time
from fastapi import FastAPI, File, UploadFile, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from typing import List

app = FastAPI(title="Arocna3 Meshroom Bridge")

# Global Config & State
BASE_DIR = Path("./meshroom_projects")
BASE_DIR.mkdir(exist_ok=True)

# Application State variables
current_project_id = ""
PROJECT_DIR = None
INPUT_DIR = None
captured_sectors = set()
total_images = 0
last_focus = 0.0
capture_history = [] 
calibrated_lenses = set()
last_phone_heartbeat = 0.0 # Track phone presence

def initialize_project(project_id: str):
    global current_project_id, PROJECT_DIR, INPUT_DIR, total_images, capture_history, captured_sectors, calibrated_lenses
    current_project_id = project_id
    PROJECT_DIR = BASE_DIR / project_id
    INPUT_DIR = PROJECT_DIR / "input"
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Reset/Recover state from disk
    captured_sectors = set()
    calibrated_lenses = set()
    capture_history = []
    
    # Scan existing images to sync state
    existing_images = sorted(list(INPUT_DIR.glob("*.jpg")))
    total_images = len(existing_images)
    
    # In a production environment, we could read EXIF here to rebuild the 3D cone map
    # This total_images value will be sent to the phone upon the next handshake
    print(f"Project Switched: {project_id} ({total_images} frames found on disk)")

def get_latest_project():
    """Finds the most recently modified project folder, or returns a new timestamped ID if none exist."""
    projects = [d for d in BASE_DIR.iterdir() if d.is_dir()]
    if not projects:
        return f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    # Sort projects by modification time of the directory
    latest = max(projects, key=lambda p: p.stat().st_mtime)
    return latest.name

# --- PERSISTENT INITIALIZATION ---
# Resumes the last project found on disk to avoid creating duplicates on restart
initialize_project(get_latest_project())

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        msg = json.dumps(message)
        for connection in self.active_connections:
            try: await connection.send_text(msg)
            except Exception: pass

manager = ConnectionManager()

# Background task to monitor phone status
@app.on_event("startup")
async def start_monitor():
    async def monitor_heartbeat():
        global last_phone_heartbeat
        while True:
            await asyncio.sleep(5)
            # If we haven't heard from the phone in 12 seconds, tell the dashboard
            if last_phone_heartbeat > 0 and (time.time() - last_phone_heartbeat) > 12:
                last_phone_heartbeat = 0
                await manager.broadcast({"type": "phone_status", "paired": False})
                
    asyncio.create_task(monitor_heartbeat())

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = '127.0.0.1'
    finally:
        s.close()
    return local_ip

# --- DASHBOARD UI ---
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Arocna3 Meshroom Bridge</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
    <style>
        body { background-color: #080808; color: #e5e5e5; font-family: ui-monospace, monospace; overflow-x: hidden; }
        .cyan-glow { box-shadow: 0 0 40px rgba(6, 182, 212, 0.1); }
        .status-pulse { animation: pulse 2s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
        canvas { outline: none; }
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #222; border-radius: 10px; }
        #qr-modal { transition: opacity 0.3s ease, visibility 0.3s ease; }
        #qr-modal.hidden { opacity: 0; visibility: hidden; display: none; }
        .project-btn.active { border-color: rgb(6, 182, 212); background-color: rgba(6, 182, 212, 0.15); color: white; }
        .project-row:hover .action-btns { opacity: 1; }
    </style>
</head>
<body class="p-4 lg:p-8">
    <div id="qr-modal" class="hidden fixed inset-0 z-50 flex items-center justify-center bg-black/90 backdrop-blur-sm">
        <div class="bg-neutral-900 border border-white/10 p-8 rounded-[2.5rem] flex flex-col items-center max-w-sm w-full cyan-glow">
            <h2 class="text-xs font-black tracking-[0.3em] uppercase text-cyan-500 mb-6">Scan to Pair</h2>
            <div class="bg-white p-4 rounded-3xl mb-6 shadow-2xl">
                <img id="qr-image" src="" alt="Pairing QR Code" class="w-64 h-64">
            </div>
            <p class="text-[10px] text-neutral-500 text-center mb-8 uppercase font-bold leading-relaxed tracking-widest">
                Link your Pixel 7 Pro telemetry<br>to the workstation bridge.
            </p>
            <button onclick="toggleQR()" class="w-full py-4 bg-neutral-800 hover:bg-neutral-700 rounded-2xl text-xs font-bold uppercase tracking-widest transition-all">
                Close
            </button>
        </div>
    </div>

    <div class="max-w-[1600px] mx-auto">
        <div class="flex flex-col md:flex-row justify-between items-start md:items-end mb-8 border-b border-white/5 pb-6">
            <div>
                <h1 class="text-4xl font-black tracking-tighter text-white uppercase italic">AROCNA3 <span class="text-cyan-500 font-normal">BRIDGE</span></h1>
                <p class="text-[10px] text-neutral-500 mt-2 uppercase tracking-[0.4em] font-bold">Field Handshake Station v1.6</p>
            </div>
            <div class="flex flex-row items-center gap-4 mt-4 md:mt-0">
                <button onclick="toggleQR()" class="bg-cyan-600/10 border border-cyan-500/30 hover:bg-cyan-500 hover:text-black px-5 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all">
                    Pairing
                </button>
                <div id="connection-status" class="flex items-center gap-3 bg-white/5 border border-white/5 px-4 py-2 rounded-xl text-xs font-bold text-red-500 uppercase tracking-widest">
                    <div class="w-2 h-2 rounded-full bg-red-500 status-pulse"></div> OFFLINE
                </div>
            </div>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-12 gap-8">
            <div class="lg:col-span-3 space-y-6">
                <div class="bg-neutral-900/40 rounded-3xl border border-white/5 p-6 backdrop-blur-xl">
                    <div class="flex justify-between items-center mb-6">
                        <h2 class="text-[10px] font-bold text-neutral-500 uppercase tracking-widest">Project Browser</h2>
                        <button onclick="newProject()" class="text-[10px] bg-cyan-500 text-black px-3 py-1 rounded-lg font-black uppercase tracking-tighter hover:scale-105 transition-all">New</button>
                    </div>
                    <div id="project-list" class="space-y-2 max-h-64 overflow-y-auto custom-scrollbar pr-2"></div>
                </div>

                <div class="bg-neutral-900/40 rounded-3xl border border-white/5 p-6 backdrop-blur-xl">
                    <h2 class="text-[10px] font-bold text-neutral-500 uppercase tracking-widest mb-6">Lens Profiles</h2>
                    <div class="grid grid-cols-3 gap-4">
                        <div id="lens-0" class="bg-black/40 border border-white/5 p-4 rounded-2xl text-center"><p class="text-[11px] font-bold text-neutral-600 uppercase">UW</p></div>
                        <div id="lens-1" class="bg-black/40 border border-white/5 p-4 rounded-2xl text-center"><p class="text-[11px] font-bold text-neutral-600 uppercase">W</p></div>
                        <div id="lens-2" class="bg-black/40 border border-white/5 p-4 rounded-2xl text-center"><p class="text-[11px] font-bold text-neutral-600 uppercase">T</p></div>
                    </div>
                </div>

                <div class="bg-neutral-900/40 rounded-3xl border border-white/5 p-6 backdrop-blur-xl">
                    <h2 class="text-[10px] font-bold text-neutral-500 uppercase tracking-widest mb-6">Session Metrics</h2>
                    <div class="space-y-4">
                        <div class="bg-black/40 p-4 rounded-2xl border border-white/5 flex justify-between items-center">
                            <span class="text-[10px] text-neutral-500 font-bold uppercase">Frame Count</span>
                            <span id="stat-photos" class="text-2xl font-black text-white">0</span>
                        </div>
                        <div class="bg-black/40 p-4 rounded-2xl border border-white/5 flex justify-between items-center">
                            <span class="text-[10px] text-neutral-500 font-bold uppercase">Sphere Cover</span>
                            <span id="coverage-percent" class="text-2xl font-black text-cyan-500 italic">0%</span>
                        </div>
                    </div>
                </div>
            </div>

            <div class="lg:col-span-9 relative bg-neutral-900/20 rounded-[2.5rem] border border-white/5 overflow-hidden cyan-glow min-h-[600px]" id="three-container">
                <div class="absolute top-8 left-8 z-10 pointer-events-none">
                    <h2 class="text-xs font-bold text-neutral-400 uppercase tracking-[0.3em]">AliceVision Field Monitor</h2>
                    <p id="project-id-display" class="text-[10px] text-cyan-500 mt-1 uppercase font-black"></p>
                </div>
            </div>
        </div>
    </div>

    <script>
        let ws;
        const container = document.getElementById('three-container');
        const qrModal = document.getElementById('qr-modal');
        const qrImage = document.getElementById('qr-image');
        const statusEl = document.getElementById('connection-status');
        let scene, camera, renderer, controls, subject;
        let cones = [];
        let photoData = [];
        let activeProject = "";

        async function toggleQR() {
            if (qrModal.classList.contains('hidden')) {
                const resp = await fetch('/qr');
                const data = await resp.json();
                qrImage.src = `data:image/png;base64,${data.image}`;
                qrModal.classList.remove('hidden');
            } else { qrModal.classList.add('hidden'); }
        }

        async function refreshProjects() {
            const resp = await fetch('/projects');
            const data = await resp.json();
            const list = document.getElementById('project-list');
            list.innerHTML = "";
            data.projects.forEach(p => {
                const row = document.createElement('div');
                row.className = "project-row group flex items-center gap-1 w-full";
                const btn = document.createElement('button');
                btn.className = `project-btn flex-1 text-left p-3 rounded-xl border border-white/5 bg-black/20 text-[10px] font-bold uppercase tracking-tighter truncate transition-all ${p === activeProject ? 'active' : 'hover:bg-white/5'}`;
                btn.innerText = p;
                btn.onclick = () => selectProject(p);
                const actionContainer = document.createElement('div');
                actionContainer.className = "action-btns opacity-0 group-hover:opacity-100 flex gap-1 transition-opacity";
                const editBtn = document.createElement('button');
                editBtn.className = "p-2 hover:text-cyan-500 text-neutral-600";
                editBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 1 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>';
                editBtn.onclick = (e) => { e.stopPropagation(); renameProject(p); };
                const deleteBtn = document.createElement('button');
                deleteBtn.className = "p-2 hover:text-red-500 text-neutral-600";
                deleteBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>';
                deleteBtn.onclick = (e) => { e.stopPropagation(); deleteProject(p); };
                actionContainer.appendChild(editBtn); actionContainer.appendChild(deleteBtn);
                row.appendChild(btn); row.appendChild(actionContainer); list.appendChild(row);
            });
        }

        async function selectProject(id) { await fetch(`/select_project/${id}`, {method: 'POST'}); location.reload(); }
        async function newProject() {
            const name = prompt("Project Name:", "project_" + new Date().getTime());
            if (name) { await fetch('/new_project', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: name})}); location.reload(); }
        }
        async function renameProject(id) {
            const newName = prompt("New name:", id);
            if (newName && newName !== id) { await fetch('/rename_project', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({old_id: id, new_id: newName})}); location.reload(); }
        }
        async function deleteProject(id) {
            if (confirm(`Delete permanent?`)) { await fetch(`/delete_project/${id}`, { method: 'DELETE' }); location.reload(); }
        }

        function setStatus(paired) {
            if (paired) {
                statusEl.innerHTML = '<div class="w-2 h-2 rounded-full bg-green-500"></div> PAIRED';
                statusEl.className = "flex items-center gap-3 bg-green-500/10 border border-green-500/20 px-4 py-2 rounded-xl text-xs font-bold text-green-500 uppercase tracking-widest";
            } else {
                statusEl.innerHTML = '<div class="w-2 h-2 rounded-full bg-red-500 status-pulse"></div> OFFLINE';
                statusEl.className = "flex items-center gap-3 bg-white/5 border border-white/5 px-4 py-2 rounded-xl text-xs font-bold text-red-500 uppercase tracking-widest";
            }
        }

        function init3D() {
            scene = new THREE.Scene();
            camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 0.1, 1000);
            camera.position.set(5, 5, 8);
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
            renderer.setSize(container.clientWidth, container.clientHeight);
            renderer.setPixelRatio(window.devicePixelRatio);
            container.appendChild(renderer.domElement);
            controls = new THREE.OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            const geometry = new THREE.SphereGeometry(1.2, 32, 32);
            const material = new THREE.MeshBasicMaterial({ color: 0x222222, wireframe: true, transparent: true, opacity: 0.2 });
            subject = new THREE.Mesh(geometry, material);
            scene.add(subject);
            const gridHelper = new THREE.GridHelper(10, 20, 0x333333, 0x111111);
            gridHelper.position.y = -2;
            scene.add(gridHelper);
            animate();
        }

        function animate() { requestAnimationFrame(animate); controls.update(); renderer.render(scene, camera); }

        function updateCones() {
            cones.forEach(c => scene.remove(c)); cones = [];
            photoData.forEach((photo) => {
                const az = (parseFloat(photo.azimuth)) * Math.PI / 180;
                const alt = (parseFloat(photo.altitude) - 50) * 0.02;
                const dist = 4;
                const x = dist * Math.cos(az), z = dist * Math.sin(az), y = alt * 2;
                const cone = new THREE.Mesh(new THREE.ConeGeometry(0.2, 0.5, 4), new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.5 }));
                cone.position.set(x, y, z); cone.lookAt(0, 0, 0); cone.rotateX(Math.PI / 2);
                scene.add(cone); cones.push(cone);
            });
        }

        function connect() {
            ws = new WebSocket(`ws://${window.location.host}/ws`);
            ws.onopen = () => { /* Server connected, but phone might be offline */ };
            ws.onmessage = (e) => {
                const data = JSON.parse(e.data);
                if (data.type === 'pair') { qrModal.classList.add('hidden'); setStatus(true); }
                if (data.type === 'phone_status') { setStatus(data.paired); }
                if (data.type === 'init') {
                    activeProject = data.project;
                    document.getElementById('project-id-display').innerText = data.project;
                    photoData = data.history || [];
                    document.getElementById('stat-photos').innerText = data.total || 0;
                    document.getElementById('coverage-percent').innerText = Math.round((new Set(data.sectors).size / 36) * 100) + '%';
                    refreshProjects();
                }
                if(data.type === 'upload') {
                    setStatus(true); // Activity means paired
                    photoData.push(data); document.getElementById('stat-photos').innerText = data.total_count;
                    updateCones();
                }
            };
            ws.onclose = () => { setTimeout(connect, 2000); };
        }

        window.addEventListener('resize', () => {
            camera.aspect = container.clientWidth / container.clientHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(container.clientWidth, container.clientHeight);
        });

        init3D(); connect();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def dashboard(): return DASHBOARD_HTML

@app.get("/qr")
async def get_qr():
    ip = get_local_ip()
    url = f"mbridge://local={ip}:8080"
    qr = qrcode.make(url)
    buffered = io.BytesIO()
    qr.save(buffered, format="PNG")
    return JSONResponse({"image": base64.b64encode(buffered.getvalue()).decode()})

@app.get("/ping")
async def phone_ping(request: Request):
    global last_phone_heartbeat
    last_phone_heartbeat = time.time()
    # Broadcase paired status and current project total count for double confirmation
    await manager.broadcast({"type": "phone_status", "paired": True, "total_count": total_images})
    return {"status": "ok", "total_count": total_images}

@app.get("/projects")
async def list_projects():
    projects = [d.name for d in BASE_DIR.iterdir() if d.is_dir()]
    return JSONResponse({"projects": sorted(projects, reverse=True)})

@app.post("/new_project")
async def create_project(req: Request):
    data = await req.json()
    project_id = data.get("id", f"project_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    initialize_project(project_id)
    return {"status": "created", "id": project_id}

@app.post("/select_project/{project_id}")
async def select_project(project_id: str):
    initialize_project(project_id)
    return {"status": "selected"}

@app.post("/rename_project")
async def rename_project(req: Request):
    data = await req.json()
    old_id, new_id = data.get("old_id"), data.get("new_id")
    if old_id and new_id and (BASE_DIR / old_id).exists():
        os.rename(BASE_DIR / old_id, BASE_DIR / new_id)
        if old_id == current_project_id: initialize_project(new_id)
        return {"status": "renamed"}
    return JSONResponse({"error": "Failed"}, status_code=400)

@app.delete("/delete_project/{project_id}")
async def delete_project(project_id: str):
    target = BASE_DIR / project_id
    if target.exists():
        shutil.rmtree(target)
        if project_id == current_project_id: 
            initialize_project(get_latest_project())
        return {"status": "deleted"}
    return JSONResponse({"error": "Not found"}, status_code=404)

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # SYNC UPON PAIRING: Send project name and existing disk frame count to the phone
        await websocket.send_text(json.dumps({
            "type": "init", "project": current_project_id, 
            "sectors": list(captured_sectors), "history": capture_history, 
            "total": total_images, # This serves as the start count for the mobile app
            "focus": last_focus,
            "lenses": list(calibrated_lenses)
        }))
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect(websocket)

@app.post("/pair")
async def pair(request: Request):
    global last_phone_heartbeat
    last_phone_heartbeat = time.time()
    # Include total count in the pair broadcast so the phone can sync immediately
    await manager.broadcast({"type": "pair", "host": request.client.host, "total_count": total_images})
    return {"status": "paired", "total_count": total_images}

@app.post("/upload")
async def upload(
    image: UploadFile = File(...), azimuth: str = Form(...), 
    diopter: str = Form(...), altitude: str = Form("50"),
    lens_idx: int = Form(1), is_calibrated: str = Form("false"),
    client_count: int = Form(0) # Phone reports its internal count
):
    global total_images, last_focus, last_phone_heartbeat
    last_phone_heartbeat = time.time()
    
    # 1. Save file to disk
    with open(INPUT_DIR / image.filename, "wb") as b: b.write(await image.read())
    
    try:
        # 2. Update local server state
        d_val = float(diopter); dist = round(1.0/d_val if d_val > 0.01 else 0.0, 3); last_focus = dist
        angle = float(azimuth); sector = int(angle // 10); captured_sectors.add(sector)
        
        # 3. Use Disk Count as final Source of Truth for Dashboard update
        total_images = len(list(INPUT_DIR.glob("*.jpg")))
        is_cal = is_calibrated.lower() == "true"
        if is_cal: calibrated_lenses.add(lens_idx)
        
        meta = {
            "type": "upload", "filename": image.filename, "azimuth": azimuth, 
            "altitude": altitude, "focus": dist, "sector": sector, 
            "lens_idx": lens_idx, "lens_calibrated": is_cal, 
            "total_count": total_images,
            "client_reported_count": client_count
        }
        capture_history.append(meta)
        
        # 4. Update Dashboard ONLY after file is saved and count is verified
        await manager.broadcast(meta)
        
    except Exception as e: 
        print(f"Error processing upload: {e}")
        
    return {"status": "success", "server_total": total_images}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="warning")