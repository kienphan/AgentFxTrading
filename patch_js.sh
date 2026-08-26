sed -i '' -e '/<script>/a\
        \
        // --- Docker Bot Management ---\
        async function fetchBots() {\
            try {\
                const res = await fetch("/api/bots");\
                const data = await res.json();\
                renderBots(data.bots, data.docker_available);\
            } catch(e) { console.error("Error fetching bots", e); }\
        }\
        function renderBots(bots, dockerOk) {\
            const tbody = document.getElementById("bots-tbody");\
            if(!dockerOk) {\
                tbody.innerHTML = `<tr><td colspan="6" class="td-dim" style="color:var(--color-loss)">Docker is not available on the server. Please ensure Docker daemon is running.</td></tr>`;\
                return;\
            }\
            if(!bots || bots.length === 0) {\
                tbody.innerHTML = `<tr><td colspan="6" class="td-dim">No bots configured.</td></tr>`;\
                return;\
            }\
            let html = "";\
            bots.forEach(b => {\
                const isRunning = b.status === "running";\
                const statusColor = isRunning ? "var(--color-profit)" : "var(--color-dim)";\
                html += `<tr>\
                    <td><strong>${b.name}</strong></td>\
                    <td><span style="color:${statusColor}">${b.status}</span></td>\
                    <td class="td-dim">${b.container_id || "-"}</td>\
                    <td class="td-dim">${b.description || "-"}</td>\
                    <td class="td-dim" style="font-family:monospace; max-width:200px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis" title="${b.run_command.replace(/"/g, '&quot;')}">${b.run_command}</td>\
                    <td style="text-align:right">\
                        ${isRunning \
                            ? `<button class="log-btn action-stop" data-name="${b.name}" style="background:rgba(229,57,53,0.2)">Stop</button>`\
                            : `<button class="log-btn action-start" data-name="${b.name}" style="background:rgba(125,189,30,0.2)">Start</button>`\
                        }\
                        <button class="log-btn action-delete" data-name="${b.name}">Delete</button>\
                    </td>\
                </tr>`;\
            });\
            tbody.innerHTML = html;\
            \
            document.querySelectorAll(".action-start").forEach(btn => btn.addEventListener("click", e => actionBot(e.target.dataset.name, "start")));\
            document.querySelectorAll(".action-stop").forEach(btn => btn.addEventListener("click", e => actionBot(e.target.dataset.name, "stop")));\
            document.querySelectorAll(".action-delete").forEach(btn => btn.addEventListener("click", e => deleteBot(e.target.dataset.name)));\
        }\
        async function actionBot(name, action) {\
            try {\
                const res = await fetch(`/api/bots/${name}/${action}`, { method: "POST" });\
                const data = await res.json();\
                if(!data.success) alert(data.message || "Error");\
                fetchBots();\
            } catch(e) { alert("Error: " + e); }\
        }\
        async function deleteBot(name) {\
            if(!confirm(`Delete bot ${name}?`)) return;\
            try {\
                await fetch(`/api/bots/${name}`, { method: "DELETE" });\
                fetchBots();\
            } catch(e) { alert("Error: " + e); }\
        }\
        \
        document.addEventListener("DOMContentLoaded", () => {\
            fetchBots();\
            setInterval(fetchBots, 10000); // Auto-refresh bot status\
            \
            const form = document.getElementById("bot-add-form");\
            document.getElementById("add-bot-btn").addEventListener("click", () => form.style.display="block");\
            document.getElementById("cancel-bot-btn").addEventListener("click", () => form.style.display="none");\
            \
            document.getElementById("save-bot-btn").addEventListener("click", async () => {\
                const name = document.getElementById("bot-name").value.trim();\
                const desc = document.getElementById("bot-desc").value.trim();\
                const cmd = document.getElementById("bot-cmd").value.trim();\
                if(!name || !cmd) return alert("Name and Command required");\
                try {\
                    const res = await fetch("/api/bots", {\
                        method: "POST", \
                        headers: {"Content-Type": "application/json"},\
                        body: JSON.stringify({name: name, description: desc, run_command: cmd})\
                    });\
                    const data = await res.json();\
                    if(!data.success) return alert(data.message);\
                    form.style.display = "none";\
                    document.getElementById("bot-name").value = "";\
                    document.getElementById("bot-desc").value = "";\
                    document.getElementById("bot-cmd").value = "";\
                    fetchBots();\
                } catch(e) { alert("Error: " + e); }\
            });\
        });\
' templates/dashboard.html
