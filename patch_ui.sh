sed -i '' -e '/<section class="panel" style="margin-top: 16px;">/i\
        <section class="panel" style="margin-top: 16px;" id="bot-management-section">\
            <div class="panel-header">\
                <span class="panel-title">🤖 Docker Bot Management</span>\
                <button id="add-bot-btn" class="log-btn" style="margin-left:auto;">+ Add Bot</button>\
            </div>\
            <div class="panel-body">\
                <div id="bot-add-form" style="display:none; margin-bottom: 16px; padding: 16px; background: var(--bg-surface-2); border-radius: 6px;">\
                    <h4 style="margin-top:0; margin-bottom:12px;">Add New Bot Configuration</h4>\
                    <div style="display:grid; gap: 12px; grid-template-columns: 1fr 1fr;">\
                        <div><label>Bot Name</label><input type="text" id="bot-name" class="log-input" style="width:100%"></div>\
                        <div><label>Description</label><input type="text" id="bot-desc" class="log-input" style="width:100%"></div>\
                    </div>\
                    <div style="margin-top: 12px;">\
                        <label>Docker Run Command (Full command)</label>\
                        <textarea id="bot-cmd" class="log-input" style="width:100%; height:60px; font-family:monospace" placeholder="docker run -e API_KEY=xyz my-cbot-image"></textarea>\
                    </div>\
                    <div style="margin-top: 12px; display:flex; gap: 8px;">\
                        <button id="save-bot-btn" class="log-btn" style="background:var(--color-profit); color:#000;">Save</button>\
                        <button id="cancel-bot-btn" class="log-btn">Cancel</button>\
                    </div>\
                </div>\
                <div class="table-wrap">\
                    <table class="data-table" id="bots-table">\
                        <thead><tr>\
                            <th>Bot Name</th><th>Status</th><th>Container ID</th><th>Description</th><th>Command</th><th style="text-align:right">Actions</th>\
                        </tr></thead>\
                        <tbody id="bots-tbody">\
                            <tr><td colspan="6" class="td-dim">Loading bots...</td></tr>\
                        </tbody>\
                    </table>\
                </div>\
            </div>\
        </section>\
' templates/dashboard.html
