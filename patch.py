import sys

html_code = """
        <section class="panel" style="margin-top: 16px;" id="bot-management-section">
            <div class="panel-header">
                <span class="panel-title">🤖 Docker Bot Management</span>
                <button id="add-bot-btn" class="log-btn" style="margin-left:auto;">+ Add Bot</button>
            </div>
            <div class="panel-body">
                <div id="bot-add-form" style="display:none; margin-bottom: 16px; padding: 16px; background: var(--bg-surface-2); border-radius: 6px;">
                    <h4 style="margin-top:0; margin-bottom:12px;">Add New Bot Configuration</h4>
                    <div style="display:grid; gap: 12px; grid-template-columns: 1fr 1fr;">
                        <div><label>Bot Name</label><input type="text" id="bot-name" class="log-input" style="width:100%" placeholder="cbot_1"></div>
                        <div><label>Description</label><input type="text" id="bot-desc" class="log-input" style="width:100%" placeholder="EURUSD 1M Bot"></div>
                    </div>
                    <div style="margin-top: 12px;">
                        <label>Docker Run Command (Full command)</label>
                        <textarea id="bot-cmd" class="log-input" style="width:100%; height:60px; font-family:monospace" placeholder="docker run -e API_KEY=xyz my-cbot-image"></textarea>
                    </div>
                    <div style="margin-top: 12px; display:flex; gap: 8px;">
                        <button id="save-bot-btn" class="log-btn" style="background:var(--color-profit); color:#000;">Save</button>
                        <button id="cancel-bot-btn" class="log-btn">Cancel</button>
                    </div>
                </div>
                <div class="table-wrap">
                    <table class="data-table" id="bots-table">
                        <thead><tr>
                            <th>Bot Name</th><th>Status</th><th>Container ID</th><th>Description</th><th>Command</th><th style="text-align:right">Actions</th>
                        </tr></thead>
                        <tbody id="bots-tbody">
                            <tr><td colspan="6" class="td-dim">Loading bots...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </section>
"""

js_code = """
        // --- Docker Bot Management ---
        async function fetchBots() {
            try {
                const res = await fetch("/api/bots");
                const data = await res.json();
                renderBots(data.bots, data.docker_available);
            } catch(e) { console.error("Error fetching bots", e); }
        }
        function renderBots(bots, dockerOk) {
            const tbody = document.getElementById("bots-tbody");
            if(!dockerOk) {
                tbody.innerHTML = `<tr><td colspan="6" class="td-dim" style="color:var(--color-loss)">Docker is not available on the server. Please ensure Docker daemon is running.</td></tr>`;
                return;
            }
            if(!bots || bots.length === 0) {
                tbody.innerHTML = `<tr><td colspan="6" class="td-dim">No bots configured.</td></tr>`;
                return;
            }
            let html = "";
            bots.forEach(b => {
                const isRunning = b.status === "running";
                const statusColor = isRunning ? "var(--color-profit)" : (b.status === "error" ? "var(--color-loss)" : "var(--color-dim)");
                const escapedCmd = b.run_command.replace(/"/g, '&quot;');
                
                html += `<tr>
                    <td><strong>${b.name}</strong></td>
                    <td><span style="color:${statusColor}">${b.status}</span></td>
                    <td class="td-dim">${b.container_id || "-"}</td>
                    <td class="td-dim">${b.description || "-"}</td>
                    <td class="td-dim" style="font-family:monospace; max-width:200px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis" title="${escapedCmd}">${b.run_command}</td>
                    <td style="text-align:right">
                        ${isRunning 
                            ? `<button class="log-btn action-stop" data-name="${b.name}" style="background:rgba(229,57,53,0.2)">Stop</button>`
                            : `<button class="log-btn action-start" data-name="${b.name}" style="background:rgba(125,189,30,0.2)">Start</button>`
                        }
                        <button class="log-btn action-delete" data-name="${b.name}">Delete</button>
                    </td>
                </tr>`;
            });
            tbody.innerHTML = html;
            
            document.querySelectorAll(".action-start").forEach(btn => btn.addEventListener("click", e => actionBot(e.target.dataset.name, "start")));
            document.querySelectorAll(".action-stop").forEach(btn => btn.addEventListener("click", e => actionBot(e.target.dataset.name, "stop")));
            document.querySelectorAll(".action-delete").forEach(btn => btn.addEventListener("click", e => deleteBot(e.target.dataset.name)));
        }
        async function actionBot(name, action) {
            try {
                const res = await fetch(`/api/bots/${name}/${action}`, { method: "POST" });
                const data = await res.json();
                if(!data.success) alert(data.message || "Error");
                fetchBots();
            } catch(e) { alert("Error: " + e); }
        }
        async function deleteBot(name) {
            if(!confirm(`Delete bot ${name}?`)) return;
            try {
                await fetch(`/api/bots/${name}`, { method: "DELETE" });
                fetchBots();
            } catch(e) { alert("Error: " + e); }
        }
        
        document.addEventListener("DOMContentLoaded", () => {
            fetchBots();
            setInterval(fetchBots, 10000); // Auto-refresh bot status
            
            const form = document.getElementById("bot-add-form");
            document.getElementById("add-bot-btn").addEventListener("click", () => form.style.display="block");
            document.getElementById("cancel-bot-btn").addEventListener("click", () => form.style.display="none");
            
            document.getElementById("save-bot-btn").addEventListener("click", async () => {
                const name = document.getElementById("bot-name").value.trim();
                const desc = document.getElementById("bot-desc").value.trim();
                const cmd = document.getElementById("bot-cmd").value.trim();
                if(!name || !cmd) return alert("Name and Command required");
                try {
                    const res = await fetch("/api/bots", {
                        method: "POST", 
                        headers: {"Content-Type": "application/json"},
                        body: JSON.stringify({name: name, description: desc, run_command: cmd})
                    });
                    const data = await res.json();
                    if(!data.success) return alert(data.message);
                    form.style.display = "none";
                    document.getElementById("bot-name").value = "";
                    document.getElementById("bot-desc").value = "";
                    document.getElementById("bot-cmd").value = "";
                    fetchBots();
                } catch(e) { alert("Error: " + e); }
            });
        });
"""

with open("templates/dashboard.html", "r") as f:
    content = f.read()

# Insert html_code before <section class="panel" style="margin-top: 16px;">
target_html = '<section class="panel" style="margin-top: 16px;">\n            <div class="panel-header">\n                <span class="panel-title">🧠 Agent Reasoning &amp; Live Logs</span>'
content = content.replace(target_html, html_code + "\n        " + target_html)

# Insert js_code after <script>
target_js = "<script>"
content = content.replace(target_js, target_js + "\n" + js_code)

with open("templates/dashboard.html", "w") as f:
    f.write(content)
