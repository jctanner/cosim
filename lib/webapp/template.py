"""Single-page HTML/CSS/JS template for the web UI."""


WEB_UI = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CoSim</title>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/js-yaml@4/dist/js-yaml.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }

  /* -- Theme System -- */
  :root {
    --bg: #1a1a2e;
    --panel: #16213e;
    --sidebar: #121a30;
    --border: #0f3460;
    --border-mid: #1a1a2e;
    --border-dark: #333;
    --input-bg: #111;
    --accent: #e94560;
    --accent-dark: #c0392b;
    --text: #e0e0e0;
    --text-dim: #888;
    --text-dimmer: #555;
    --text-bright: #fff;
    --highlight: #4fc3f7;
  }
  [data-theme="stadium"] {
    --bg: #000000;
    --panel: #0d0d0d;
    --sidebar: #050505;
    --border: #2a2a2a;
    --border-mid: #1a1a1a;
    --border-dark: #1a1a1a;
    --input-bg: #111111;
    --accent: #00e5ff;
    --accent-dark: #00b8cc;
    --text: #ffffff;
    --text-dim: #aaaaaa;
    --text-dimmer: #666666;
    --text-bright: #ffffff;
    --highlight: #ffeb3b;
  }
  [data-theme="field"] {
    --bg: #0a1a0a;
    --panel: #0f200f;
    --sidebar: #081408;
    --border: #1e4d1e;
    --border-mid: #162816;
    --border-dark: #1e3a1e;
    --input-bg: #071207;
    --accent: #f5a623;
    --accent-dark: #d4891a;
    --text: #e8f0e8;
    --text-dim: #7a9e7a;
    --text-dimmer: #4a6a4a;
    --text-bright: #ffffff;
    --highlight: #7dff8a;
  }
  [data-theme="solarized-dark"] {
    --bg: #002b36;
    --panel: #073642;
    --sidebar: #002029;
    --border: #586e75;
    --border-mid: #073642;
    --border-dark: #2a4a52;
    --input-bg: #003847;
    --accent: #cb4b16;
    --accent-dark: #a83c11;
    --text: #839496;
    --text-dim: #657b83;
    --text-dimmer: #586e75;
    --text-bright: #93a1a1;
    --highlight: #2aa198;
  }
  [data-theme="solarized-light"] {
    --bg: #fdf6e3;
    --panel: #eee8d5;
    --sidebar: #f5efdc;
    --border: #93a1a1;
    --border-mid: #eee8d5;
    --border-dark: #d3cbb7;
    --input-bg: #fff8e7;
    --accent: #cb4b16;
    --accent-dark: #a83c11;
    --text: #657b83;
    --text-dim: #839496;
    --text-dimmer: #93a1a1;
    --text-bright: #073642;
    --highlight: #268bd2;
  }

  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }

  /* -- Header with tabs -- */
  #header { background: var(--panel); padding: 0 20px; border-bottom: 1px solid var(--border);
            display: flex; align-items: stretch; gap: 0; }
  #header h1 { font-size: 18px; color: var(--accent); display: flex; align-items: center; padding: 12px 16px 12px 0;
               border-right: 1px solid var(--border); margin-right: 0; }
  .header-tab { padding: 12px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
                background: transparent; border: none; color: var(--text-dim);
                border-bottom: 2px solid transparent; transition: all 0.15s ease; }
  .header-tab:hover { color: var(--text); }
  .header-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  #session-controls { margin-left: auto; display: flex; align-items: center; gap: 6px; padding: 8px 0; }
  .session-btn { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 12px;
                 border-radius: 6px; font-size: 12px; cursor: pointer; font-weight: 600; }
  .session-btn:hover { border-color: var(--accent); color: var(--accent); }
  #session-load-select { background: var(--bg); color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 8px;
                         border-radius: 6px; font-size: 12px; max-width: 200px; }
  #orch-status { display: flex; align-items: center; gap: 5px; margin-right: 8px;
                 padding: 4px 10px; border: 1px solid var(--border-dark); border-radius: 6px; }
  #orch-label { font-size: 11px; color: var(--text-dim); }
  .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .status-dot.disconnected { background: #666; }
  .status-dot.waiting { background: #f39c12; }
  .status-dot.connecting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .status-dot.starting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .status-dot.ready { background: #2ecc71; }
  .status-dot.responding { background: #3498db; animation: pulse 0.5s ease-in-out infinite; }
  .status-dot.restarting { background: #e94560; animation: pulse 0.8s ease-in-out infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  /* -- NPCs tab -- */
  #npcs-pane { padding: 0; flex-direction: row; }
  #npcs-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  #npcs-main { flex: 1; overflow-y: auto; padding: 20px; }
  #npcs-content { max-width: 1000px; }
  #npcs-empty { color: var(--text-dimmer); text-align: center; padding: 40px; }
  .npc-tier-section { margin-bottom: 24px; }
  .npc-tier-header { font-size: 13px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: var(--text-dim); margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid var(--border-dark); }
  .npc-tier-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .npc-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
              padding: 14px 16px; flex: 1 1 160px; max-width: 220px; min-width: 160px;
              transition: border-color 0.15s; }
  .npc-card:hover { border-color: var(--text-dimmer); }
  .npc-card.offline { opacity: 0.6; }
  .npc-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .npc-status-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
  .npc-status-dot.ready { background: #2ecc71; }
  .npc-status-dot.starting { background: #f39c12; animation: pulse 1s ease-in-out infinite; }
  .npc-status-dot.responding { background: #3498db; animation: pulse 0.5s ease-in-out infinite; }
  .npc-status-dot.writing-docs { background: #9b59b6; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.committing-code { background: #e67e22; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.managing-tickets { background: #1abc9c; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.processing-commands { background: #f39c12; animation: pulse 0.8s ease-in-out infinite; }
  .npc-status-dot.firing { background: #e94560; animation: pulse 1.5s ease-in-out infinite; }
  .npc-status-dot.offline { background: #666; }
  .npc-status-dot.disconnected { background: #444; }
  .npc-status-dot.unknown { background: #444; }
  .npc-card-state { font-size: 10px; color: var(--text-dimmer); margin-left: auto; }
  .npc-card-name { font-size: 14px; font-weight: 700; color: var(--text); }
  .npc-card-desc { font-size: 11px; color: var(--text-dim); margin-bottom: 8px; line-height: 1.4; }
  .npc-card-section-label { font-size: 10px; font-weight: 600; text-transform: uppercase;
                           letter-spacing: 0.5px; color: var(--text-dimmer); margin-bottom: 3px; margin-top: 6px; }
  .npc-card-tags { margin-bottom: 4px; line-height: 1.8; }
  .npc-tag { background: var(--input-bg); color: var(--text-dim); padding: 1px 6px; border-radius: 4px; font-size: 11px;
             margin-right: 3px; display: inline-block; }
  .npc-tag-folder { border-left: 2px solid #3498db; }
  .npc-toggle-btn { width: 100%; background: transparent; border: 1px solid var(--border-dark);
                    color: var(--text-dim); padding: 5px; border-radius: 6px; font-size: 11px;
                    cursor: pointer; transition: all 0.15s; }
  .npc-toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .npc-toggle-btn.is-online:hover { border-color: #f39c12; color: #f39c12; }
  .npc-detail-tab { transition: all 0.15s; }
  .npc-detail-tab.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .npc-config-check { display: flex; align-items: center; gap: 4px; background: var(--bg);
                      padding: 4px 10px; border-radius: 6px; border: 1px solid var(--border-dark);
                      font-size: 12px; color: var(--text-dim); cursor: pointer; }
  .npc-config-check:hover { border-color: var(--text-dimmer); }
  .npc-config-check input { accent-color: #e94560; }
  .npc-config-check.checked { color: var(--text); border-color: var(--text-dimmer); }
  .thought-item { padding: 8px 12px; cursor: pointer; border-bottom: 1px solid var(--bg);
                  font-size: 11px; color: var(--text-dim); transition: background 0.1s; }
  .thought-item:hover { background: var(--border-mid); }
  .thought-item.active { background: var(--border-mid); color: var(--text); border-left: 3px solid var(--accent); }
  .thought-item-time { color: var(--text-dimmer); font-size: 10px; }
  .thought-item-preview { color: var(--text-dim); margin-top: 2px; overflow: hidden;
                          text-overflow: ellipsis; white-space: nowrap; }

  /* -- Usage tab -- */
  #usage-pane { padding: 0; flex-direction: row; }
  #usage-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .usage-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                           letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .usage-stat { padding: 4px 14px; font-size: 12px; color: var(--text-dim); }
  .usage-stat strong { color: var(--text); }
  #usage-main { flex: 1; overflow-y: auto; padding: 20px; }
  #usage-content { max-width: 1000px; }
  #usage-empty { color: var(--text-dimmer); text-align: center; padding: 40px; }
  .usage-grid { display: flex; flex-wrap: wrap; gap: 12px; }
  .usage-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
                padding: 14px 16px; flex: 1 1 200px; max-width: 280px; min-width: 200px;
                transition: border-color 0.15s; }
  .usage-card:hover { border-color: var(--text-dimmer); }
  .usage-card-name { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 10px;
                     padding-bottom: 6px; border-bottom: 1px solid var(--border-dark); }
  .usage-card-row { display: flex; justify-content: space-between; padding: 3px 0;
                    font-size: 12px; color: var(--text-dim); }
  .usage-card-row .label { color: var(--text-dimmer); }
  .usage-card-row .value { color: var(--text); font-weight: 600; font-family: monospace; }
  .usage-card-row .value.cost { color: #2ecc71; }

  /* -- Advanced tab -- */
  #advanced-pane { padding: 0; }

  /* -- Recap tab -- */
  #recap-pane { padding: 0; flex-direction: row; }
  #recap-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  #recap-main { flex: 1; overflow-y: auto; }
  .recap-item { padding: 8px 14px; cursor: pointer; border-bottom: 1px solid var(--bg);
                font-size: 12px; color: var(--text-dim); transition: background 0.1s; }
  .recap-item:hover { background: var(--border-mid); }
  .recap-item.active { background: var(--border-mid); color: var(--text); border-left: 3px solid var(--accent); }
  .recap-item-style { font-weight: 600; color: var(--highlight); }
  .recap-item-time { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }

  /* -- Email tab -- */
  #email-pane { padding: 0; flex-direction: row; }
  #email-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow: hidden; }
  #email-main { flex: 1; overflow-y: auto; padding: 20px; }
  .email-item { padding: 10px 12px; border-bottom: 1px solid var(--bg); cursor: pointer; transition: background 0.1s; }
  .email-item:hover { background: var(--border-mid); }
  .email-item.active { background: var(--border-mid); border-left: 3px solid #3498db; }
  .email-item-from { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .email-item-subject { font-size: 13px; color: var(--text); margin-top: 2px; overflow: hidden;
                        text-overflow: ellipsis; white-space: nowrap; }
  .email-item-date { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }

  /* -- Memos tab -- */
  #memos-pane { padding: 0; flex-direction: row; }
  #memos-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                   display: flex; flex-direction: column; overflow: hidden; }
  #memos-main { flex: 1; overflow-y: auto; padding: 20px; }
  .memo-thread-item { padding: 10px 12px; border-bottom: 1px solid var(--bg); cursor: pointer; transition: background 0.1s; }
  .memo-thread-item:hover { background: var(--border-mid); }
  .memo-thread-item.active { background: var(--border-mid); border-left: 3px solid #2ecc71; }
  .memo-thread-title { font-size: 13px; font-weight: 700; color: var(--text); }
  .memo-thread-preview { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; overflow: hidden;
                         text-overflow: ellipsis; white-space: nowrap; }
  .memo-thread-meta { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }
  .memo-post { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 14px; margin-bottom: 10px; }
  .memo-post-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .memo-post-date { font-size: 10px; color: var(--text-dimmer); margin-left: 8px; }
  .memo-post-text { font-size: 13px; color: var(--text); margin-top: 8px; line-height: 1.5; }
  .memo-post-text p { margin: 0 0 8px 0; }
  .memo-post-text p:last-child { margin-bottom: 0; }
  .memo-post-text ul, .memo-post-text ol { margin: 4px 0 8px 20px; padding: 0; }
  .memo-post-text pre { background: var(--input-bg); padding: 8px 10px; border-radius: 4px; overflow-x: auto; margin: 8px 0; }
  .memo-post-text code { background: var(--input-bg); padding: 1px 4px; border-radius: 3px; font-size: 12px; }
  .memo-post-text pre code { background: none; padding: 0; }
  .memo-post-text h1, .memo-post-text h2, .memo-post-text h3, .memo-post-text h4 { margin: 12px 0 6px 0; color: var(--text-bright); }
  .memo-post-text blockquote { border-left: 3px solid var(--border-dark); margin: 8px 0; padding: 4px 12px; color: var(--text-dim); }
  .memo-post-text table { border-collapse: collapse; margin: 8px 0; }
  .memo-post-text th, .memo-post-text td { border: 1px solid var(--border-dark); padding: 4px 8px; font-size: 12px; }

  /* -- Blog tab -- */
  #blog-pane { padding: 0; flex-direction: row; }
  #blog-sidebar { width: 300px; min-width: 300px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow: hidden; }
  #blog-main { flex: 1; overflow-y: auto; padding: 20px; }
  .blog-filter-bar { display: flex; gap: 4px; padding: 8px 10px; border-bottom: 1px solid var(--border-dark); }
  .blog-filter-btn { flex: 1; background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim);
                     padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: 600; }
  .blog-filter-btn.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .blog-post-item { padding: 10px 12px; border-bottom: 1px solid var(--border-mid); cursor: pointer; transition: background 0.1s; }
  .blog-post-item:hover { background: var(--border-mid); }
  .blog-post-item.active { background: var(--border-mid); border-left: 3px solid var(--accent); }
  .blog-post-title { font-size: 13px; font-weight: 700; color: var(--text); }
  .blog-post-preview { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; overflow: hidden;
                       text-overflow: ellipsis; white-space: nowrap; }
  .blog-post-meta { font-size: 10px; color: var(--text-dimmer); margin-top: 2px; }
  .blog-external-badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         background: #2ecc71; color: var(--text-bright); padding: 1px 5px; border-radius: 3px; margin-left: 6px; }
  .blog-internal-badge { font-size: 9px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px;
                         background: var(--border-dark); color: var(--text-dim); padding: 1px 5px; border-radius: 3px; margin-left: 6px; }
  .blog-tag { font-size: 10px; background: var(--input-bg); color: var(--text-dim); padding: 1px 6px;
              border-radius: 4px; margin-right: 3px; display: inline-block; }
  .blog-reply { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px; margin-bottom: 8px; }
  .blog-reply-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .blog-reply-date { font-size: 10px; color: var(--text-dimmer); margin-left: 8px; }
  .blog-reply-text { font-size: 13px; color: var(--text); margin-top: 6px; line-height: 1.5; white-space: pre-wrap; }

  /* -- Events tab -- */
  #events-pane { padding: 0; flex-direction: row; }
  .events-sub-tab.active { background: var(--accent); border-color: var(--accent); color: var(--text-bright); }
  .event-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 10px;
                padding: 14px 16px; flex: 1 1 250px; max-width: 350px; min-width: 220px;
                transition: border-color 0.15s; }
  .event-card:hover { border-color: var(--text-dimmer); }
  .event-card-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .event-card-name { font-size: 14px; font-weight: 700; color: var(--text); }
  .event-card-severity { font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px;
                         text-transform: uppercase; letter-spacing: 0.5px; }
  .event-sev-critical { background: #e94560; color: #fff; }
  .event-sev-high { background: #e67e22; color: #fff; }
  .event-sev-medium { background: #f39c12; color: #111; }
  .event-sev-low { background: #2ecc71; color: #111; }
  .event-card-actions { font-size: 11px; color: var(--text-dim); margin-bottom: 8px; }
  .event-card-preview { font-size: 11px; color: var(--text-dimmer); margin-bottom: 10px; overflow: hidden;
                        text-overflow: ellipsis; white-space: nowrap; }
  .event-card-btns { display: flex; gap: 4px; }
  .event-card-btns button { flex: 1; }
  .event-trigger-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 5px; border-radius: 6px;
                       cursor: pointer; font-size: 11px; font-weight: 600; }
  .event-trigger-btn:hover { background: var(--accent-dark); }
  .event-log-row { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px;
                   padding: 10px 14px; margin-bottom: 8px; display: flex; align-items: center; gap: 12px; }
  .event-log-row:hover { border-color: var(--text-dimmer); }
  .event-log-time { font-size: 11px; color: var(--text-dimmer); min-width: 80px; }
  .event-log-name { font-size: 13px; font-weight: 600; color: var(--text); flex: 1; }
  .event-log-actions { font-size: 10px; color: var(--text-dimmer); }

  /* -- Modal overlay -- */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
                   z-index: 1000; align-items: center; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 12px;
           padding: 24px; min-width: 380px; max-width: 500px; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
  .modal h2 { margin: 0 0 16px; font-size: 16px; color: var(--accent); }
  .modal-field { margin-bottom: 14px; }
  .modal-field label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 4px; font-weight: 600;
                       text-transform: uppercase; letter-spacing: 0.5px; }
  .modal-field input, .modal-field select, .modal-field textarea {
    width: 100%; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 8px 12px;
    border-radius: 8px; font-size: 14px; outline: none; box-sizing: border-box; }
  .modal-field input:focus, .modal-field select:focus, .modal-field textarea:focus { border-color: var(--accent); }
  .modal-field textarea { resize: vertical; min-height: 60px; font-family: inherit; }
  .modal-field .field-hint { font-size: 11px; color: var(--text-dimmer); margin-top: 4px; }
  .modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 18px; }
  .modal-btn-primary { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 20px;
                       border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; }
  .modal-btn-primary:hover { background: var(--accent-dark); }
  .modal-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .modal-btn-cancel { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 8px 20px;
                      border-radius: 8px; cursor: pointer; font-size: 13px; }
  .modal-btn-cancel:hover { border-color: var(--accent); color: var(--accent); }
  .modal-status { font-size: 12px; color: var(--highlight); margin-top: 10px; min-height: 16px; }

  /* -- Loading overlay -- */
  #loading-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8);
                     z-index: 2000; align-items: center; justify-content: center; flex-direction: column; gap: 12px; }
  #loading-overlay.open { display: flex; }
  #loading-overlay .spinner { width: 32px; height: 32px; border: 3px solid var(--border-dark);
                              border-top-color: var(--accent); border-radius: 50%;
                              animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #loading-text { color: var(--text); font-size: 14px; }

  #main-layout { flex: 1; display: flex; overflow: hidden; }

  /* -- Sidebar -- */
  #sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
             display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                     letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .channel-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                 background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                 font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .channel-btn:hover { background: var(--border-mid); color: var(--text); }
  .channel-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  .channel-btn .unread-badge { background: var(--accent); color: var(--text-bright); font-size: 10px;
                               padding: 1px 6px; border-radius: 8px; margin-left: auto;
                               font-weight: 700; display: none; }
  .channel-btn .unread-badge.visible { display: inline; }
  .sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 6px 14px; }

  /* -- Tab panes -- */
  .tab-pane { display: none; flex: 1; overflow: hidden; }
  .tab-pane.active { display: flex; }
  #chat-pane { flex-direction: row; }
  #docs-pane { flex-direction: column; }
  #chat-area { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

  /* -- Chat tab -- */
  #channel-header { background: var(--panel); padding: 8px 20px; border-bottom: 1px solid var(--border);
                    font-size: 15px; font-weight: 700; color: var(--text); }
  #channel-header .ch-desc { font-size: 12px; color: var(--text-dim); font-weight: 400; margin-left: 10px; }
  #channel-members { font-size: 11px; color: var(--text-dimmer); margin-top: 2px; }
  #messages-panel { flex: 1; overflow-y: auto; padding: 12px 20px; display: flex;
                    flex-direction: column; gap: 6px; }
  .msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; line-height: 1.5; }
  .msg-row { display: flex; gap: 10px; align-items: flex-start; }
  .msg-body { flex: 1; min-width: 0; }
  .msg-avatar { width: 32px; height: 32px; border-radius: 6px; flex-shrink: 0;
                display: flex; align-items: center; justify-content: center;
                font-size: 14px; font-weight: 700; color: #fff; margin-top: 1px; }
  .msg-avatar img { width: 32px; height: 32px; border-radius: 6px; object-fit: cover; }
  .msg .sender { font-weight: 700; font-size: 13px; margin-bottom: 4px; }
  .msg .content { font-size: 14px; word-break: break-word; }
  .msg .content h1 { font-size: 16px; margin: 8px 0 4px; color: var(--text); }
  .msg .content h2 { font-size: 15px; margin: 6px 0 3px; color: var(--text); }
  .msg .content h3 { font-size: 14px; margin: 5px 0 2px; color: var(--text); }
  .msg .content p { margin: 4px 0; }
  .msg .content ul, .msg .content ol { margin: 4px 0 4px 20px; }
  .msg .content li { margin: 2px 0; }
  .msg .content strong { color: var(--text-bright); }
  .msg .content code { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; font-size: 13px; }
  .msg .content pre { background: rgba(0,0,0,0.3); padding: 8px; border-radius: 6px; margin: 4px 0;
                      overflow-x: auto; }
  .msg .content pre code { background: none; padding: 0; }
  .msg .content hr { border: none; border-top: 1px solid var(--border-dark); margin: 8px 0; }
  .msg .content input[type="checkbox"] { margin-right: 4px; }
  .msg .ts { font-size: 11px; color: var(--text-dim); margin-top: 4px; }
  .msg-customer { align-self: flex-end; background: var(--border); border-bottom-right-radius: 4px; }
  .msg-customer .sender { color: #4fc3f7; }
  .msg-board .sender { color: #ffd700; }
  .msg-hacker .sender { color: #00ff41; }
  .msg-god .sender { color: #ff6ff2; }
  .msg-intern .sender { color: #a8e6cf; }
  .msg-competitor .sender { color: #ff4444; }
  .msg-regulator .sender { color: #ff9800; }
  .msg-investor .sender { color: #7c4dff; }
  .msg-press .sender { color: #ffab40; }
  .msg-agent { align-self: flex-start; background: var(--border-mid); border: 1px solid var(--border-dark); border-bottom-left-radius: 4px; }
  .msg-pm .sender { color: #e94560; }
  .msg-engmgr .sender { color: #f39c12; }
  .msg-architect .sender { color: #9b59b6; }
  .msg-senior .sender { color: #2ecc71; }
  .msg-support .sender { color: #1abc9c; }
  .msg-sales .sender { color: #e67e22; }
  .msg-ceo .sender { color: #f1c40f; }
  .msg-cfo .sender { color: #3498db; }
  .msg-marketing .sender { color: #e056a0; }
  .msg-devops .sender { color: #00bcd4; }
  .msg-projmgr .sender { color: #26c6da; }
  .msg-default .sender { color: #95a5a6; }

  /* -- Persona bar -- */
  #persona-bar { background: var(--sidebar); padding: 6px 20px; border-top: 1px solid var(--border);
                 display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  /* -- Input area -- */
  #input-area { background: var(--panel); padding: 10px 20px; border-top: 1px solid var(--border);
                display: flex; gap: 8px; align-items: center; }
  #sender-name { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #sender-name:focus { border-color: var(--accent); }
  #sender-role, #sender-role-custom { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                   padding: 8px 12px; border-radius: 8px; font-size: 14px; }
  #msg-input { flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
               padding: 10px 14px; border-radius: 8px; font-size: 14px; outline: none; }
  #msg-input:focus { border-color: var(--accent); }
  #send-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 10px 20px;
              border-radius: 8px; font-size: 14px; cursor: pointer; font-weight: 600; }
  #send-btn:hover { background: var(--accent-dark); }
  #clear-btn { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 10px 14px;
               border-radius: 8px; font-size: 14px; cursor: pointer; }
  #clear-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* -- Docs tab -- */
  #docs-pane { padding: 0; flex-direction: row; }
  #docs-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                  display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .docs-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                          letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .docs-sidebar-divider { border: none; border-top: 1px solid var(--border); margin: 6px 14px; }
  .folder-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .folder-btn:hover { background: var(--border-mid); color: var(--text); }
  .folder-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  #docs-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #docs-toolbar { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                  display: flex; align-items: center; }
  #docs-search { width: 100%; max-width: 400px; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                 padding: 8px 12px; border-radius: 8px; font-size: 14px; outline: none; }
  #docs-search:focus { border-color: var(--accent); }
  #new-doc-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 16px;
                 border-radius: 8px; font-size: 13px; cursor: pointer; font-weight: 600;
                 margin-left: 8px; white-space: nowrap; }
  #new-doc-btn:hover { background: var(--accent-dark); }
  #doc-editor { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #doc-editor-header { display: flex; align-items: center; justify-content: space-between;
                       padding: 10px 20px; border-bottom: 1px solid var(--border); background: var(--panel); }
  #doc-editor-header button { background: transparent; color: var(--text); border: 1px solid var(--border-dark);
                              padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-editor-save { background: var(--accent) !important; border-color: var(--accent) !important; font-weight: 600; }
  #doc-editor-save:hover { background: var(--accent-dark) !important; }
  #doc-editor-form { flex: 1; display: flex; flex-direction: column; gap: 10px; padding: 16px 20px; overflow-y: auto; }
  #doc-editor-title { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                      padding: 10px 14px; border-radius: 8px; font-size: 16px; font-weight: 700; outline: none; }
  #doc-editor-title:focus { border-color: var(--accent); }
  #doc-editor-folder { background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                       padding: 8px 12px; border-radius: 8px; font-size: 14px; width: 200px; }
  #doc-editor-content { flex: 1; background: var(--bg); color: var(--text); border: 1px solid var(--border-dark);
                        padding: 14px; border-radius: 8px; font-size: 14px; outline: none;
                        font-family: monospace; resize: none; min-height: 300px; }
  #doc-editor-content:focus { border-color: var(--accent); }
  #docs-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  .doc-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px 16px;
              margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .doc-card:hover { border-color: var(--accent); }
  .doc-card-title { font-size: 14px; font-weight: 700; color: var(--highlight); margin-bottom: 4px; }
  .doc-card-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .doc-card-folder { font-size: 11px; background: var(--border); color: var(--highlight); padding: 2px 8px;
                     border-radius: 4px; font-weight: 600; }
  .doc-card-preview { font-size: 13px; color: var(--text-dim); overflow: hidden; text-overflow: ellipsis;
                      white-space: nowrap; }
  #docs-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  #doc-viewer { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #doc-viewer.open { display: flex; }
  #doc-viewer-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                       display: flex; align-items: center; gap: 10px; }
  #doc-back-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 12px;
                  border-radius: 6px; cursor: pointer; font-size: 13px; }
  #doc-back-btn:hover { border-color: var(--accent); color: var(--accent); }
  #doc-viewer-title { font-size: 16px; font-weight: 700; color: var(--highlight); }
  #doc-viewer-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                        color: var(--text); line-height: 1.7; }
  #doc-viewer-content h1 { font-size: 20px; margin: 12px 0 8px; }
  #doc-viewer-content h2 { font-size: 17px; margin: 10px 0 6px; }
  #doc-viewer-content h3 { font-size: 15px; margin: 8px 0 4px; }
  #doc-viewer-content p { margin: 6px 0; }
  #doc-viewer-content ul, #doc-viewer-content ol { margin: 6px 0 6px 24px; }
  #doc-viewer-content li { margin: 3px 0; }
  #doc-viewer-content strong { color: var(--text-bright); }
  #doc-viewer-content code { background: rgba(255,255,255,0.1); padding: 2px 5px; border-radius: 3px; }
  #doc-viewer-content pre { background: rgba(0,0,0,0.3); padding: 12px; border-radius: 6px; margin: 6px 0;
                            overflow-x: auto; white-space: pre-wrap; word-break: break-word; }
  #doc-viewer-content pre code { background: none; padding: 0; }
  #doc-viewer-content hr { border: none; border-top: 1px solid var(--border-dark); margin: 10px 0; }
  #doc-viewer-content input[type="checkbox"] { margin-right: 4px; }

  /* -- GitLab tab -- */
  #gitlab-pane { padding: 0; flex-direction: row; }
  #gitlab-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                    display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .gitlab-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                            letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .repo-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
              background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
              font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .repo-btn:hover { background: var(--border-mid); color: var(--text); }
  .repo-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  #gitlab-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #gitlab-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                   display: flex; align-items: center; gap: 12px; }
  #gitlab-repo-title { font-size: 16px; font-weight: 700; color: var(--highlight); }
  #gitlab-repo-desc { font-size: 13px; color: var(--text-dim); }
  .gitlab-toggle-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 14px;
                       border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .gitlab-toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .gitlab-toggle-btn.active { background: #0f3460; color: #4fc3f7; border-color: #4fc3f7; }
  #gitlab-toggle-bar { padding: 8px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                       display: flex; gap: 6px; }
  #gitlab-content { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #gitlab-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  .gitlab-breadcrumbs { font-size: 13px; color: var(--text-dim); margin-bottom: 12px; }
  .gitlab-breadcrumbs a { color: var(--highlight); cursor: pointer; text-decoration: none; }
  .gitlab-breadcrumbs a:hover { text-decoration: underline; }
  .tree-item { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border-dark);
               cursor: pointer; font-size: 14px; color: var(--text); }
  .tree-item:hover { background: var(--border-mid); }
  .tree-item-icon { font-size: 14px; width: 20px; text-align: center; }
  .tree-item-name { flex: 1; }
  .gitlab-file-viewer { background: var(--input-bg); border: 1px solid var(--border-dark); border-radius: 6px; padding: 16px;
                        font-family: monospace; font-size: 13px; white-space: pre-wrap; word-break: break-word;
                        color: var(--text); line-height: 1.6; }
  .commit-item { padding: 10px 12px; border-bottom: 1px solid var(--border-dark); }
  .commit-item-id { font-family: monospace; font-size: 12px; color: var(--highlight); margin-right: 8px; }
  .commit-item-msg { font-size: 14px; color: var(--text); }
  .commit-item-meta { font-size: 12px; color: var(--text-dimmer); margin-top: 4px; }

  /* -- Tickets tab -- */
  #tickets-pane { padding: 0; flex-direction: row; }
  #tickets-sidebar { width: 200px; min-width: 200px; background: var(--sidebar); border-right: 1px solid var(--border);
                     display: flex; flex-direction: column; overflow-y: auto; padding: 8px 0; }
  .tickets-sidebar-section { font-size: 11px; font-weight: 700; text-transform: uppercase;
                             letter-spacing: 1px; color: var(--text-dimmer); padding: 10px 14px 4px; }
  .tickets-filter-btn { display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
                        background: transparent; border: none; color: var(--text-dim); padding: 5px 14px;
                        font-size: 13px; cursor: pointer; transition: all 0.1s ease; }
  .tickets-filter-btn:hover { background: var(--border-mid); color: var(--text); }
  .tickets-filter-btn.active { background: var(--border-mid); color: var(--text-bright); font-weight: 700; }
  .tickets-filter-btn .tk-count { margin-left: auto; font-size: 11px; color: var(--text-dimmer); }
  #tickets-main { flex: 1; display: flex; flex-direction: column; overflow: hidden; min-width: 0; }
  #tickets-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                    font-size: 15px; font-weight: 700; color: var(--text); }
  #tickets-list { flex: 1; overflow-y: auto; padding: 16px 20px; }
  #tickets-empty { color: var(--text-dimmer); font-size: 14px; text-align: center; padding: 40px 20px; }
  .ticket-card { background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px; padding: 12px 16px;
                 margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s ease; }
  .ticket-card:hover { border-color: var(--accent); }
  .ticket-card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .ticket-card-id { font-family: monospace; font-size: 11px; color: var(--text-dim); }
  .ticket-card-title { font-size: 14px; font-weight: 700; color: var(--text); flex: 1; }
  .ticket-card-bottom { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .tk-badge { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
  .tk-status-open { background: #1b5e20; color: #a5d6a7; }
  .tk-status-in_progress { background: #0d47a1; color: #90caf9; }
  .tk-status-resolved { background: #4a148c; color: #ce93d8; }
  .tk-status-closed { background: #333; color: #888; }
  .tk-priority-low { background: #263238; color: #78909c; }
  .tk-priority-medium { background: #33691e; color: #aed581; }
  .tk-priority-high { background: #e65100; color: #ffcc80; }
  .tk-priority-critical { background: #b71c1c; color: #ef9a9a; }
  .tk-assignee { font-size: 11px; color: var(--highlight); margin-left: auto; }
  #ticket-detail { display: none; flex-direction: column; flex: 1; overflow: hidden; }
  #ticket-detail.open { display: flex; }
  #ticket-detail-header { padding: 12px 20px; border-bottom: 1px solid var(--border); background: var(--panel);
                          display: flex; align-items: center; gap: 10px; }
  #ticket-back-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text-dim); padding: 6px 12px;
                     border-radius: 6px; cursor: pointer; font-size: 13px; }
  #ticket-back-btn:hover { border-color: var(--accent); color: var(--accent); }
  #ticket-detail-title { font-size: 16px; font-weight: 700; color: var(--text); }
  #ticket-detail-id { font-family: monospace; font-size: 12px; color: var(--text-dim); margin-left: 8px; }
  #ticket-detail-content { flex: 1; overflow-y: auto; padding: 20px; font-size: 14px;
                           color: var(--text); line-height: 1.7; }
  .tk-detail-meta { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
  .tk-detail-field { font-size: 13px; color: var(--text-dim); }
  .tk-detail-field strong { color: var(--text); }
  .tk-detail-desc { background: var(--input-bg); border: 1px solid var(--border-dark); border-radius: 6px; padding: 12px;
                    margin-bottom: 16px; white-space: pre-wrap; word-break: break-word; }
  .tk-detail-deps { margin-bottom: 16px; font-size: 13px; }
  .tk-detail-deps span { color: var(--highlight); font-family: monospace; cursor: pointer; }
  .tk-comments-header { font-size: 14px; font-weight: 700; color: var(--text); margin-bottom: 8px;
                        border-bottom: 1px solid var(--border-dark); padding-bottom: 4px; }
  .tk-comment { background: var(--input-bg); border-left: 3px solid var(--border); padding: 8px 12px; margin-bottom: 8px;
                border-radius: 0 6px 6px 0; }
  .tk-comment-author { font-size: 12px; font-weight: 700; color: var(--highlight); }
  .tk-comment-time { font-size: 11px; color: var(--text-dimmer); margin-left: 8px; }
  .tk-comment-text { font-size: 13px; color: var(--text); margin-top: 4px; }
  #tk-create-btn { background: var(--accent); color: var(--text-bright); border: none; padding: 6px 14px; border-radius: 6px;
                   cursor: pointer; font-size: 12px; font-weight: 600; margin-left: auto; }
  #tk-create-btn:hover { background: var(--accent-dark); }
  #tk-create-form { display: none; background: var(--bg); border: 1px solid var(--border-dark); border-radius: 8px;
                    padding: 16px; margin-bottom: 12px; }
  #tk-create-form.open { display: block; }
  .tk-form-row { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; }
  .tk-form-row label { font-size: 12px; color: var(--text-dim); min-width: 70px; }
  .tk-form-input { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                   border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-input:focus { border-color: var(--accent); }
  .tk-form-select { background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                    border-radius: 6px; font-size: 13px; outline: none; }
  .tk-form-textarea { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 6px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 60px;
                      font-family: inherit; }
  .tk-form-textarea:focus { border-color: var(--accent); }
  .tk-form-actions { display: flex; gap: 8px; justify-content: flex-end; }
  .tk-form-submit { background: var(--accent); color: var(--text-bright); border: none; padding: 6px 16px; border-radius: 6px;
                    cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-form-submit:hover { background: var(--accent-dark); }
  .tk-form-cancel { background: transparent; color: var(--text-dim); border: 1px solid var(--border-dark); padding: 6px 16px;
                    border-radius: 6px; cursor: pointer; font-size: 12px; }
  .tk-form-cancel:hover { border-color: var(--accent); color: var(--accent); }
  .tk-detail-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px;
                       padding-bottom: 12px; border-bottom: 1px solid var(--border-dark); }
  .tk-action-btn { background: transparent; border: 1px solid var(--border-dark); color: var(--text); padding: 5px 12px;
                   border-radius: 6px; cursor: pointer; font-size: 12px; font-weight: 600; }
  .tk-action-btn:hover { border-color: var(--accent); color: var(--accent); }
  .tk-action-btn.primary { background: #0d47a1; border-color: #0d47a1; color: #90caf9; }
  .tk-action-btn.primary:hover { background: #1565c0; }
  .tk-action-btn.danger { border-color: #b71c1c; color: #ef9a9a; }
  .tk-action-btn.danger:hover { background: #b71c1c; color: var(--text-bright); }
  .tk-action-btn.success { border-color: #1b5e20; color: #a5d6a7; }
  .tk-action-btn.success:hover { background: #1b5e20; color: var(--text-bright); }
  .tk-assign-row { display: flex; gap: 8px; align-items: center; }
  .tk-assign-select { background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 4px 8px;
                      border-radius: 6px; font-size: 12px; }
  .tk-comment-input-area { display: flex; gap: 8px; margin-top: 12px; align-items: flex-start; }
  .tk-comment-input { flex: 1; background: var(--input-bg); color: var(--text); border: 1px solid var(--border-dark); padding: 8px 10px;
                      border-radius: 6px; font-size: 13px; outline: none; resize: vertical; min-height: 36px;
                      font-family: inherit; }
  .tk-comment-input:focus { border-color: var(--accent); }
  .tk-comment-submit { background: var(--accent); color: var(--text-bright); border: none; padding: 8px 14px; border-radius: 6px;
                       cursor: pointer; font-size: 12px; font-weight: 600; align-self: flex-end; }
  .tk-comment-submit:hover { background: var(--accent-dark); }
</style>
</head>
<body>
<div id="header">
  <h1>CoSim</h1>
  <button class="header-tab active" data-tab="chat">Chat</button>
  <button class="header-tab" data-tab="docs">Docs</button>
  <button class="header-tab" data-tab="gitlab">GitLab</button>
  <button class="header-tab" data-tab="tickets">Tickets</button>
  <button class="header-tab" data-tab="email">Email</button>
  <button class="header-tab" data-tab="memos">Memos</button>
  <button class="header-tab" data-tab="blog">Blog</button>
  <button class="header-tab" data-tab="events">Events</button>
  <button class="header-tab" data-tab="npcs">NPCs</button>
  <button class="header-tab" data-tab="usage">Usage</button>
  <button class="header-tab" data-tab="recap">Recap</button>
  <button class="header-tab" data-tab="advanced">Advanced</button>
  <select id="theme-select" title="Theme" style="background:var(--input-bg);color:var(--text-dim);border:1px solid var(--border-dark);padding:4px 6px;border-radius:6px;font-size:11px;font-weight:600;cursor:pointer;outline:none;margin:auto 0;margin-left:8px">
    <option value="default">Default</option>
    <option value="stadium">Stadium</option>
    <option value="field">Field</option>
    <option value="solarized-dark">Solarized Dark</option>
    <option value="solarized-light">Solarized Light</option>
  </select>
  <div id="session-controls">
    <span id="orch-status" title="Orchestrator status">
      <span id="orch-dot" class="status-dot disconnected"></span>
      <span id="orch-label">Disconnected</span>
    </span>
    <button id="session-new-btn" class="session-btn" title="New session">New</button>
    <button id="session-save-btn" class="session-btn" title="Save session">Save</button>
    <select id="session-load-select" title="Load session">
      <option value="" disabled selected>Load...</option>
    </select>
  </div>
</div>
<div id="main-layout">
  <!-- Chat tab: sidebar + chat area -->
  <div id="chat-pane" class="tab-pane active">
    <div id="sidebar">
      <div class="sidebar-section">Internal</div>
      <div id="internal-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">External</div>
      <div id="external-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Scenario Director</div>
      <div id="director-channels"></div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">System</div>
      <div id="system-channels"></div>
    </div>
    <div id="chat-area">
      <div id="channel-header">
        <span id="channel-title">#general</span>
        <span class="ch-desc" id="channel-desc"></span>
        <div id="channel-members"></div>
      </div>
      <div id="messages-panel"></div>
      <div id="persona-bar">
        <input id="sender-name" type="text" placeholder="Your name..." value="" style="width:120px" />
        <select id="sender-role"></select>
        <input id="sender-role-custom" type="text" placeholder="Custom role..." style="width:100px;display:none" />
      </div>
      <div id="input-area">
        <input id="msg-input" type="text" placeholder="Type a message..." autocomplete="off" />
        <button id="send-btn">Send</button>
      </div>
    </div>
  </div>
  <!-- Docs tab -->
  <div id="docs-pane" class="tab-pane">
    <div id="docs-sidebar">
      <div class="docs-sidebar-section">All</div>
      <button class="folder-btn active" data-folder="" id="folder-all">All Folders</button>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Shared</div>
      <div id="shared-folders"></div>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Departments</div>
      <div id="dept-folders"></div>
      <hr class="docs-sidebar-divider">
      <div class="docs-sidebar-section">Personal</div>
      <div id="personal-folders"></div>
    </div>
    <div id="docs-main">
      <div id="docs-toolbar">
        <input id="docs-search" type="text" placeholder="Search documents..." autocomplete="off" />
        <button id="new-doc-btn">+ New Document</button>
      </div>
      <div id="doc-editor" style="display:none">
        <div id="doc-editor-header">
          <button id="doc-editor-cancel">Cancel</button>
          <span style="font-weight:700;font-size:14px">New Document</span>
          <button id="doc-editor-save">Save</button>
        </div>
        <div id="doc-editor-form">
          <input id="doc-editor-title" type="text" placeholder="Document title..." autocomplete="off" />
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <select id="doc-editor-folder">
            </select>
            <span style="font-size:11px;color:var(--text-dimmer)">Author:</span>
            <input id="doc-author-name" type="text" placeholder="Your name..." style="width:120px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-author-role" style="background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px"></select>
          </div>
          <textarea id="doc-editor-content" placeholder="Write your document content here (Markdown supported)..." rows="16"></textarea>
        </div>
      </div>
      <div id="docs-list">
        <div id="docs-empty">No documents yet.</div>
      </div>
      <div id="doc-viewer">
        <div id="doc-viewer-header">
          <button id="doc-back-btn">Back</button>
          <span id="doc-viewer-title"></span>
          <div style="margin-left:auto;display:flex;gap:6px">
            <button id="doc-download-btn" class="session-btn" style="font-size:11px">Download</button>
            <button id="doc-history-btn" class="session-btn" style="font-size:11px">History</button>
            <button id="doc-edit-btn" class="session-btn" style="font-size:11px">Edit Latest Version</button>
          </div>
        </div>
        <div id="doc-viewer-body" style="display:flex;flex:1;min-height:0;overflow:hidden">
          <div id="doc-viewer-content" style="flex:1;overflow-y:auto"></div>
          <div id="doc-history-panel" style="display:none;width:220px;min-width:220px;border-left:1px solid var(--border-dark);background:var(--sidebar);overflow-y:auto">
            <div style="padding:8px 12px;font-size:11px;font-weight:700;color:var(--text-dimmer);text-transform:uppercase;letter-spacing:0.5px">Version History</div>
            <div id="doc-history-list"></div>
          </div>
        </div>
        <div id="doc-edit-area" style="display:none;flex:1;min-height:0;flex-direction:column;padding:12px 20px;gap:8px">
          <div style="display:flex;gap:8px;align-items:center">
            <span style="font-size:11px;color:var(--text-dimmer)">Editing as:</span>
            <input id="doc-edit-author-name" type="text" placeholder="Your name..." style="width:120px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px" />
            <select id="doc-edit-author-role" style="background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px"></select>
            <div style="margin-left:auto;display:flex;gap:6px">
              <button id="doc-edit-cancel" class="session-btn" style="font-size:11px">Cancel</button>
              <button id="doc-edit-save" class="session-btn" style="font-size:11px;background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Save</button>
            </div>
          </div>
          <textarea id="doc-edit-textarea" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:monospace;resize:none;outline:none"></textarea>
        </div>
      </div>
    </div>
  </div>
  <!-- GitLab tab -->
  <div id="gitlab-pane" class="tab-pane">
    <div id="gitlab-sidebar">
      <div class="gitlab-sidebar-section">Repositories</div>
      <div id="gitlab-repo-list"></div>
      <div style="padding:8px 10px">
        <button id="gl-new-repo-btn" class="session-btn" style="width:100%;font-size:11px">+ New Repo</button>
      </div>
      <div id="gl-new-repo-form" style="display:none;padding:4px 10px 10px">
        <input id="gl-new-repo-name" type="text" placeholder="repo-name" autocomplete="off"
               style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <input id="gl-new-repo-desc" type="text" placeholder="Description (optional)" autocomplete="off"
               style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:5px 8px;border-radius:6px;font-size:12px;margin-bottom:6px;box-sizing:border-box" />
        <div style="display:flex;gap:4px">
          <button id="gl-new-repo-cancel" class="session-btn" style="flex:1;font-size:11px">Cancel</button>
          <button id="gl-new-repo-save" class="session-btn" style="flex:1;font-size:11px;background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Create</button>
        </div>
      </div>
    </div>
    <div id="gitlab-main">
      <div id="gitlab-header">
        <span id="gitlab-repo-title">Select a repository</span>
        <span id="gitlab-repo-desc"></span>
      </div>
      <div id="gitlab-toggle-bar">
        <button class="gitlab-toggle-btn active" data-view="tree" id="gl-toggle-tree">Files</button>
        <button class="gitlab-toggle-btn" data-view="commits" id="gl-toggle-commits">Commits</button>
      </div>
      <div id="gitlab-content">
        <div id="gitlab-empty">No repositories yet.</div>
      </div>
    </div>
  </div>
  <!-- Tickets tab -->
  <div id="tickets-pane" class="tab-pane">
    <div id="tickets-sidebar">
      <div class="tickets-sidebar-section">Status Filter</div>
      <button class="tickets-filter-btn active" data-status="" id="tk-filter-all">All <span class="tk-count" id="tk-count-all"></span></button>
      <button class="tickets-filter-btn" data-status="open">Open <span class="tk-count" id="tk-count-open"></span></button>
      <button class="tickets-filter-btn" data-status="in_progress">In Progress <span class="tk-count" id="tk-count-in_progress"></span></button>
      <button class="tickets-filter-btn" data-status="resolved">Resolved <span class="tk-count" id="tk-count-resolved"></span></button>
      <button class="tickets-filter-btn" data-status="closed">Closed <span class="tk-count" id="tk-count-closed"></span></button>
    </div>
    <div id="tickets-main">
      <div id="tickets-header" style="display:flex;align-items:center;">
        <span>Tickets</span>
        <button id="tk-create-btn" onclick="toggleCreateForm()">+ New Ticket</button>
      </div>
      <div id="tickets-list">
        <div id="tk-create-form">
          <div class="tk-form-row">
            <label>Title</label>
            <input class="tk-form-input" id="tk-form-title" placeholder="Ticket title" />
          </div>
          <div class="tk-form-row">
            <label>Priority</label>
            <select class="tk-form-select" id="tk-form-priority">
              <option value="low">Low</option>
              <option value="medium" selected>Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
            <label style="margin-left:12px;">Assignee</label>
            <select class="tk-form-select" id="tk-form-assignee">
              <option value="">Unassigned</option>
            </select>
          </div>
          <div class="tk-form-row">
            <label>Created by</label>
            <select class="tk-form-select" id="tk-form-author">
            </select>
          </div>
          <div class="tk-form-row">
            <label>Description</label>
            <textarea class="tk-form-textarea" id="tk-form-desc" placeholder="Describe the work to be done..."></textarea>
          </div>
          <div class="tk-form-row">
            <label>Notify channel</label>
            <select class="tk-form-select" id="tk-form-notify">
              <option value="">Don't notify</option>
            </select>
          </div>
          <div class="tk-form-actions">
            <button class="tk-form-cancel" onclick="toggleCreateForm()">Cancel</button>
            <button class="tk-form-submit" onclick="submitCreateTicket()">Create Ticket</button>
          </div>
        </div>
        <div id="tickets-empty">No tickets yet.</div>
      </div>
      <div id="ticket-detail">
        <div id="ticket-detail-header">
          <button id="ticket-back-btn">Back</button>
          <span id="ticket-detail-title"></span>
          <span id="ticket-detail-id"></span>
        </div>
        <div style="padding:8px 20px;background:var(--sidebar);border-bottom:1px solid var(--border-dark);display:flex;align-items:center;gap:8px">
          <span style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px">Acting as</span>
          <select class="tk-form-select" id="tk-acting-as" style="font-size:12px;">
          </select>
          <span style="font-size:10px;color:var(--text-dimmer)">All actions (status, assign, comments) use this identity</span>
        </div>
        <div id="ticket-detail-content"></div>
      </div>
    </div>
  </div>
  <!-- NPCs tab -->
  <div id="npcs-pane" class="tab-pane">
    <div id="npcs-sidebar">
      <div class="sidebar-section">Scenario</div>
      <div id="npcs-scenario-info" style="padding:8px 14px;font-size:12px;color:var(--text-dim);">No scenario loaded</div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Summary</div>
      <div id="npcs-summary" style="padding:8px 14px;font-size:12px;color:var(--text-dim);"></div>
      <hr class="sidebar-divider">
      <div style="padding:8px 10px">
        <button id="npc-hire-btn" class="session-btn" style="width:100%;background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:11px">+ Hire Agent</button>
      </div>
    </div>
    <div id="npcs-main">
      <div id="npcs-content">
        <div id="npcs-empty">No scenario loaded. Click New to start a session.</div>
      </div>
    </div>
  </div>
  <!-- Events tab -->
  <div id="events-pane" class="tab-pane">
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div style="padding:10px 20px;background:var(--panel);border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
        <button class="session-btn events-sub-tab active" data-events-tab="pool">Event Pool</button>
        <button class="session-btn events-sub-tab" data-events-tab="log">Event Log</button>
        <div style="margin-left:auto">
          <button id="events-add-btn" class="session-btn" style="background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:11px">+ Add Event</button>
        </div>
      </div>
      <div id="events-pool-view" style="flex:1;overflow-y:auto;padding:20px">
        <div id="events-pool-grid" style="display:flex;flex-wrap:wrap;gap:12px"></div>
        <div id="events-pool-empty" style="color:var(--text-dimmer);text-align:center;padding:40px">No events configured for this scenario.</div>
      </div>
      <div id="events-log-view" style="flex:1;overflow-y:auto;padding:20px;display:none">
        <div id="events-log-list"></div>
        <div id="events-log-empty" style="color:var(--text-dimmer);text-align:center;padding:40px">No events fired yet.</div>
      </div>
    </div>
  </div>
  <!-- Usage tab -->
  <div id="usage-pane" class="tab-pane">
    <div id="usage-sidebar">
      <div class="usage-sidebar-section">Session Totals</div>
      <div id="usage-totals" style="padding:4px 0;"></div>
    </div>
    <div id="usage-main">
      <div id="usage-content">
        <div id="usage-empty">No usage data yet. Send messages so agents produce responses.</div>
      </div>
    </div>
  </div>
  <!-- Recap tab -->
  <div id="recap-pane" class="tab-pane">
    <div id="recap-sidebar">
      <div class="sidebar-section">Generate Recap</div>
      <div style="padding:8px 14px">
        <select id="recap-style" style="width:100%;background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:12px;margin-bottom:8px">
          <option value="normal">Normal</option>
          <option value="ye-olde-english">Ye Olde English</option>
          <option value="tolkien">Tolkien Fantasy</option>
          <option value="star-wars">Star Wars Crawl</option>
          <option value="star-trek">Star Trek Captain's Log</option>
          <option value="dr-who">Doctor Who</option>
          <option value="morse-code">Morse Code / Telegraph</option>
          <option value="dr-seuss">Dr. Seuss</option>
          <option value="shakespeare">Shakespearean</option>
          <option value="80s-rock-ballad">80s Rock Ballad</option>
          <option value="90s-alternative">90s Alternative</option>
          <option value="heavy-metal">Heavy Metal</option>
          <option value="dystopian">Dystopian</option>
          <option value="matrix">The Matrix</option>
          <option value="pharaoh">Pharaoh's Decree</option>
          <option value="tombstone">Tombstone Western</option>
          <option value="survivor">Survivor Tribal Council</option>
          <option value="hackernews">HackerNews Blog Post</option>
        </select>
        <button id="recap-generate-btn" class="session-btn" style="width:100%;background:var(--accent);border-color:var(--accent);color:var(--text-bright);font-size:12px">Generate Recap</button>
      </div>
      <hr class="sidebar-divider">
      <div class="sidebar-section">Saved Recaps</div>
      <div id="recap-list" style="flex:1;overflow-y:auto"></div>
    </div>
    <div id="recap-main">
      <div id="recap-content" style="padding:20px;font-size:14px;color:var(--text);line-height:1.8;white-space:pre-wrap">
        <div id="recap-empty" style="color:var(--text-dimmer);text-align:center;padding:60px">Pick a style and generate a recap of this session.</div>
      </div>
    </div>
  </div>
  <!-- Email tab -->
  <div id="email-pane" class="tab-pane">
    <div id="email-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="compose-email-btn" class="session-btn" style="width:100%;background:#3498db;border-color:#3498db;color:var(--text-bright);font-size:12px">Compose Email</button>
      </div>
      <div id="email-list" style="flex:1;overflow-y:auto"></div>
      <div id="email-list-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No emails sent yet.</div>
    </div>
    <div id="email-main">
      <div id="email-viewer" style="display:none">
        <div id="email-viewer-from" style="font-size:13px;color:var(--highlight);font-weight:700;margin-bottom:4px"></div>
        <div id="email-viewer-subject" style="font-size:18px;font-weight:700;color:var(--text);margin-bottom:4px"></div>
        <div id="email-viewer-date" style="font-size:11px;color:var(--text-dimmer);margin-bottom:16px"></div>
        <div id="email-viewer-body" style="font-size:14px;color:var(--text);line-height:1.6;white-space:pre-wrap"></div>
      </div>
      <div id="email-compose" style="display:none;max-width:600px">
        <h3 style="color:var(--text);margin-bottom:12px">Compose Email</h3>
        <div class="modal-field">
          <label>From</label>
          <div style="display:flex;gap:8px">
            <input id="email-compose-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
            <select id="email-compose-role" style="flex:1"></select>
          </div>
          <input id="email-compose-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
        </div>
        <div class="modal-field">
          <label>Subject</label>
          <input id="email-compose-subject" type="text" placeholder="Subject line..." autocomplete="off" />
        </div>
        <div class="modal-field">
          <label>Body</label>
          <textarea id="email-compose-body" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:inherit;resize:vertical;line-height:1.6" placeholder="Write your email..."></textarea>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="session-btn" id="email-compose-cancel">Cancel</button>
          <button class="modal-btn-primary" id="email-compose-send" style="background:#3498db">Send</button>
        </div>
      </div>
      <div id="email-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">Select an email to read, or compose a new one.</div>
    </div>
  </div>
  <!-- Memos tab -->
  <div id="memos-pane" class="tab-pane">
    <div id="memos-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="create-memo-thread-btn" class="session-btn" style="width:100%;background:#2ecc71;border-color:#2ecc71;color:var(--text-bright);font-size:12px">New Discussion</button>
      </div>
      <div id="memo-threads-list" style="flex:1;overflow-y:auto"></div>
      <div id="memo-threads-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No discussion threads yet.</div>
    </div>
    <div id="memos-main">
      <div id="memo-thread-viewer" style="display:none">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px">
          <div>
            <h2 id="memo-thread-title" style="color:var(--text);margin:0 0 4px 0;font-size:18px"></h2>
            <div id="memo-thread-meta" style="font-size:11px;color:var(--text-dimmer)"></div>
            <div id="memo-thread-description" style="font-size:13px;color:var(--text-dim);margin-top:8px"></div>
          </div>
          <button id="memo-delete-btn" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer" title="Delete thread">Delete</button>
        </div>
        <div id="memo-posts-list" style="margin:16px 0"></div>
        <div style="border-top:1px solid var(--border-dark);padding-top:12px">
          <div style="display:flex;gap:8px;margin-bottom:8px">
            <input id="memo-reply-name" type="text" placeholder="Name" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px" autocomplete="off" />
            <select id="memo-reply-role" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px"></select>
          </div>
          <input id="memo-reply-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:8px;box-sizing:border-box" autocomplete="off" />
          <textarea id="memo-reply-text" placeholder="Post a reply..." style="width:100%;min-height:80px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;justify-content:flex-end">
            <button id="memo-reply-send" class="modal-btn-primary" style="background:#2ecc71;font-size:12px">Post Reply</button>
          </div>
        </div>
      </div>
      <div id="memo-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">Select a discussion thread or create a new one.</div>
    </div>
  </div>
  <!-- Blog tab -->
  <div id="blog-pane" class="tab-pane">
    <div id="blog-sidebar">
      <div style="padding:10px;border-bottom:1px solid var(--border-dark)">
        <button id="create-blog-post-btn" class="session-btn" style="width:100%;background:var(--accent);border-color:var(--accent);color:var(--text-bright);font-size:12px">New Post</button>
      </div>
      <div class="blog-filter-bar">
        <button class="blog-filter-btn active" data-blog-filter="all">All</button>
        <button class="blog-filter-btn" data-blog-filter="internal">Internal</button>
        <button class="blog-filter-btn" data-blog-filter="external">External</button>
      </div>
      <div id="blog-posts-list" style="flex:1;overflow-y:auto"></div>
      <div id="blog-posts-empty" style="color:var(--text-dimmer);text-align:center;padding:20px;font-size:12px">No blog posts yet.</div>
    </div>
    <div id="blog-main">
      <div id="blog-post-viewer" style="display:none">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px">
          <div>
            <div style="display:flex;align-items:center">
              <h2 id="blog-post-title" style="color:var(--text);margin:0;font-size:20px"></h2>
              <span id="blog-post-badge"></span>
            </div>
            <div id="blog-post-author" style="font-size:13px;color:var(--highlight);font-weight:700;margin-top:4px"></div>
            <div id="blog-post-date" style="font-size:11px;color:var(--text-dimmer);margin-top:2px"></div>
            <div id="blog-post-tags" style="margin-top:6px"></div>
          </div>
          <div style="display:flex;gap:4px">
            <button id="blog-publish-btn" style="background:#2ecc71;border:none;color:var(--text-bright);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;display:none" title="Publish">Publish</button>
            <button id="blog-unpublish-btn" style="background:transparent;border:1px solid #f39c12;color:#f39c12;padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer;display:none" title="Unpublish">Unpublish</button>
            <button id="blog-download-btn" style="background:transparent;border:1px solid var(--text-dim);color:var(--text-dim);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer" title="Download raw content">Download</button>
            <button id="blog-delete-btn" style="background:transparent;border:1px solid var(--accent);color:var(--accent);padding:4px 10px;border-radius:4px;font-size:11px;cursor:pointer" title="Delete post">Delete</button>
          </div>
        </div>
        <div id="blog-post-body" style="font-size:14px;color:var(--text);line-height:1.7;margin-bottom:20px"></div>
        <div style="border-top:1px solid var(--border-dark);padding-top:12px">
          <h3 id="blog-replies-header" style="font-size:14px;color:var(--text);margin-bottom:10px"></h3>
          <div id="blog-replies-list" style="margin-bottom:16px"></div>
          <div style="display:flex;gap:8px;margin-bottom:8px">
            <input id="blog-reply-name" type="text" placeholder="Name" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px" autocomplete="off" />
            <select id="blog-reply-role" style="flex:1;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px"></select>
          </div>
          <input id="blog-reply-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:8px;box-sizing:border-box" autocomplete="off" />
          <textarea id="blog-reply-text" placeholder="Write a reply..." style="width:100%;min-height:60px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box"></textarea>
          <div style="display:flex;gap:8px;margin-top:8px;justify-content:flex-end">
            <button id="blog-reply-send" class="modal-btn-primary" style="font-size:12px">Post Reply</button>
          </div>
        </div>
      </div>
      <div id="blog-empty-state" style="color:var(--text-dimmer);text-align:center;padding:60px;font-size:14px">No blog posts yet — write the first one.</div>
    </div>
  </div>
  <!-- Advanced tab -->
  <div id="advanced-pane" class="tab-pane">
    <div id="advanced-main" style="flex:1;padding:20px;overflow-y:auto">
      <div style="max-width:800px">
        <h3 style="color:var(--text);margin-bottom:16px">Advanced Actions</h3>

        <!-- Session Manager -->
        <div style="margin-bottom:32px">
          <div style="font-size:12px;font-weight:600;color:var(--text);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Session Manager</div>
          <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">Manage saved sessions — load, rename, or delete.</p>
          <div id="session-manager-table-wrap" style="overflow-x:auto">
            <table id="session-manager-table" style="width:100%;border-collapse:collapse;font-size:13px">
              <thead>
                <tr style="border-bottom:1px solid var(--border-dark);text-align:left">
                  <th data-sm-sort="name" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Name <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="scenario" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Scenario <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="created_at" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Created <span class="sm-sort-arrow"></span></th>
                  <th data-sm-sort="saved_at" style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Last Saved <span class="sm-sort-arrow"></span></th>
                  <th style="padding:8px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;text-align:right">Actions</th>
                </tr>
              </thead>
              <tbody id="session-manager-body">
                <tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Danger Zone -->
        <div style="margin-bottom:24px">
          <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Danger Zone</div>
          <p style="font-size:12px;color:var(--text-dim);margin-bottom:12px">These actions are destructive and cannot be undone. Save your session first.</p>
          <button id="clear-chat-btn" class="session-btn" style="border-color:var(--accent);color:var(--accent);margin-right:8px">Clear Chat History</button>
          <button id="clear-all-btn" class="session-btn" style="background:var(--accent);border-color:var(--accent);color:var(--text-bright)">Clear Everything</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Blog Create Post Modal -->
<div class="modal-overlay" id="blog-create-modal">
  <div class="modal" style="max-width:600px">
    <h2>New Blog Post</h2>
    <div class="modal-field">
      <label>Author</label>
      <div style="display:flex;gap:8px">
        <input id="blog-create-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
        <select id="blog-create-role" style="flex:1"></select>
      </div>
      <input id="blog-create-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Title</label>
      <input id="blog-create-title" type="text" placeholder="Blog post title..." autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Body</label>
      <textarea id="blog-create-body" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:14px;font-family:inherit;resize:vertical;line-height:1.6;box-sizing:border-box" placeholder="Write your blog post..."></textarea>
    </div>
    <div class="modal-field">
      <label>Tags (comma-separated)</label>
      <input id="blog-create-tags" type="text" placeholder="engineering, api, release" autocomplete="off" />
    </div>
    <div class="modal-field" style="display:flex;align-items:center;gap:8px">
      <input id="blog-create-external" type="checkbox" style="accent-color:var(--accent)" />
      <label style="margin:0;text-transform:none;letter-spacing:0;font-size:13px;color:var(--text)">External (customer-facing)</label>
    </div>
    <div class="modal-actions">
      <button class="session-btn" id="blog-create-cancel">Cancel</button>
      <button class="session-btn" id="blog-create-draft" style="border-color:#f39c12;color:#f39c12">Save Draft</button>
      <button class="modal-btn-primary" id="blog-create-submit">Publish</button>
    </div>
  </div>
</div>

<!-- Memo Create Thread Modal -->
<div class="modal-overlay" id="memo-create-modal">
  <div class="modal" style="max-width:500px">
    <h2>New Discussion Thread</h2>
    <div class="modal-field">
      <label>Posted by</label>
      <div style="display:flex;gap:8px">
        <input id="memo-create-name" type="text" placeholder="Name" style="flex:1" autocomplete="off" />
        <select id="memo-create-role" style="flex:1"></select>
      </div>
      <input id="memo-create-role-custom" type="text" placeholder="Custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Title</label>
      <input id="memo-create-title" type="text" placeholder="Discussion thread title..." autocomplete="off" />
    </div>
    <div class="modal-field">
      <label>Description (optional)</label>
      <textarea id="memo-create-description" style="width:100%;min-height:60px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:10px;border-radius:6px;font-family:inherit;resize:vertical;font-size:13px;box-sizing:border-box" placeholder="Brief description of the discussion topic..."></textarea>
    </div>
    <div class="modal-actions">
      <button class="session-btn" id="memo-create-cancel">Cancel</button>
      <button class="modal-btn-primary" id="memo-create-submit" style="background:#2ecc71">Create Thread</button>
    </div>
  </div>
</div>

<!-- New Session Modal -->
<div class="modal-overlay" id="new-session-modal">
  <div class="modal">
    <h2>New Session</h2>
    <div class="modal-field">
      <label>Scenario</label>
      <select id="new-session-scenario"></select>
      <div class="field-hint" id="new-session-scenario-desc"></div>
    </div>
    <div class="modal-field">
      <label>Session Name (optional)</label>
      <input id="new-session-name" type="text" placeholder="e.g. consulting-run" autocomplete="off" />
      <div class="field-hint">Leave blank to auto-generate from scenario + date</div>
    </div>
    <div class="modal-status" id="new-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="new-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="new-session-confirm">Create</button>
    </div>
  </div>
</div>

<!-- Save Session Modal -->
<div class="modal-overlay" id="save-session-modal">
  <div class="modal" style="max-width:480px">
    <h2>Save Session</h2>
    <div class="modal-field" id="save-session-existing-wrap">
      <label>Existing saves</label>
      <div id="save-session-list" style="max-height:180px;overflow-y:auto;border:1px solid var(--border-dark);border-radius:6px;background:var(--bg-darker,var(--bg))">
        <div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">Loading...</div>
      </div>
    </div>
    <div class="modal-field">
      <label>Save as</label>
      <input id="save-session-name" type="text" placeholder="e.g. before-demo" autocomplete="off" />
      <div class="field-hint">Leave blank to auto-generate. Click an existing save to branch from it.</div>
    </div>
    <div class="modal-status" id="save-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="save-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="save-session-confirm">Save</button>
    </div>
  </div>
</div>

<!-- Load Session Modal -->
<div class="modal-overlay" id="load-session-modal">
  <div class="modal" style="max-width:600px">
    <h2>Load Session</h2>
    <div class="modal-field">
      <label>Saved Sessions</label>
      <div style="max-height:280px;overflow-y:auto;border:1px solid var(--border-dark);border-radius:6px">
        <table id="load-session-table" style="width:100%;border-collapse:collapse;font-size:13px">
          <thead>
            <tr style="border-bottom:1px solid var(--border-dark);text-align:left;position:sticky;top:0;background:var(--bg-surface,var(--bg))">
              <th data-lm-sort="name" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Name <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="scenario" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Scenario <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="created_at" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Created <span class="lm-sort-arrow"></span></th>
              <th data-lm-sort="saved_at" style="padding:6px 10px;color:var(--text-dim);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;cursor:pointer;user-select:none">Last Saved <span class="lm-sort-arrow"></span></th>
            </tr>
          </thead>
          <tbody id="load-session-body">
            <tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="modal-status" id="load-session-status"></div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" id="load-session-cancel">Cancel</button>
      <button class="modal-btn-primary" id="load-session-confirm" disabled>Load</button>
    </div>
  </div>
</div>

<!-- NPC Detail Modal -->
<div class="modal-overlay" id="npc-detail-modal">
  <div class="modal" style="width:80vw;max-width:1000px;height:75vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 id="npc-detail-title" style="margin:0"></h2>
      <button class="modal-btn-cancel" id="npc-detail-close">Close</button>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:12px">
      <button class="session-btn npc-detail-tab active" data-npc-tab="thoughts">Thoughts</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="character">Character</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="prompt">Prompt</button>
      <button class="session-btn npc-detail-tab" data-npc-tab="config">Config</button>
    </div>
    <div id="npc-detail-thoughts" style="flex:1;min-height:0;display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border-dark)">
      <div style="width:200px;min-width:200px;background:var(--sidebar);border-right:1px solid var(--border-dark);display:flex;flex-direction:column">
        <div style="padding:6px 8px;border-bottom:1px solid var(--border-dark)">
          <input id="npc-thoughts-search" type="text" placeholder="Search thoughts..." autocomplete="off"
                 style="width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:4px 8px;border-radius:6px;font-size:11px;outline:none;box-sizing:border-box" />
        </div>
        <div id="npc-thoughts-list" style="flex:1;overflow-y:auto">
        </div>
      </div>
      <div id="npc-thoughts-content" style="flex:1;overflow-y:auto;background:var(--input-bg);padding:16px;font-size:13px;color:var(--text);white-space:pre-wrap;font-family:monospace;line-height:1.5">
        No thoughts recorded yet.
      </div>
    </div>
    <div id="npc-detail-character" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:20px;display:none">
      <div id="npc-cs-meta" style="margin-bottom:16px"></div>
      <div id="npc-cs-sections" style="font-size:14px;color:var(--text);line-height:1.6"></div>
    </div>
    <div id="npc-detail-prompt" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:16px;font-size:13px;color:var(--text);white-space:pre-wrap;font-family:monospace;line-height:1.5;display:none">
    </div>
    <div id="npc-detail-config" style="flex:1;min-height:0;overflow-y:auto;background:var(--input-bg);border-radius:8px;padding:16px;display:none">
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Response Tier</div>
        <select id="npc-config-tier" style="background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="1">Tier 1 — ICs</option>
          <option value="2">Tier 2 — Managers</option>
          <option value="3">Tier 3 — Executives</option>
        </select>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Verbosity</div>
        <select id="npc-config-verbosity" style="background:var(--bg);color:var(--text);border:1px solid var(--border-dark);padding:6px 10px;border-radius:6px;font-size:13px">
          <option value="concise">Concise — 1-2 sentences</option>
          <option value="brief">Brief — 2-3 sentences</option>
          <option value="normal" selected>Normal — default</option>
          <option value="essay">Essay — 1-2 short paragraphs</option>
          <option value="detailed">Detailed — thorough with examples</option>
          <option value="dissertation">Dissertation — exhaustive analysis</option>
        </select>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Channel Memberships</div>
        <div id="npc-config-channels" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">Doc Folder Access</div>
        <div id="npc-config-folders" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="margin-bottom:16px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px">GitLab Repos</div>
        <div id="npc-config-repos" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div style="display:flex;justify-content:flex-end;padding-top:8px;border-top:1px solid var(--border-dark)">
        <button id="npc-config-save" class="modal-btn-primary" style="font-size:13px">Save Configuration</button>
      </div>
    </div>
  </div>
</div>

<!-- Hire Agent Modal -->
<div class="modal-overlay" id="hire-modal">
  <div class="modal" style="width:80vw;max-width:800px;height:80vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 style="margin:0">Hire New Agent</h2>
      <button class="modal-btn-cancel" id="hire-modal-close">Cancel</button>
    </div>
    <div style="flex:1;min-height:0;overflow-y:auto">
      <div class="modal-field">
        <label>Character Template</label>
        <select id="hire-template">
          <option value="">Start from scratch</option>
        </select>
        <div class="field-hint">Pick a template to pre-fill the character prompt, or write your own.</div>
      </div>
      <div class="modal-field">
        <label>Name / Role / Key</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="hire-name" type="text" placeholder="Name" style="flex:2" autocomplete="off" />
          <select id="hire-role-preset" style="flex:2"></select>
          <input id="hire-key" type="text" placeholder="key (auto)" style="flex:1" autocomplete="off" />
        </div>
        <input id="hire-role-custom" type="text" placeholder="Enter custom role..." style="display:none;width:100%;margin-top:6px" autocomplete="off" />
      </div>
      <div class="modal-field">
        <label>Team Description</label>
        <input id="hire-team-desc" type="text" placeholder="e.g. testing, quality assurance, bug triage" autocomplete="off" />
      </div>
      <div class="modal-field">
        <label>Tier / Verbosity</label>
        <div style="display:flex;gap:8px">
          <select id="hire-tier" style="flex:1">
            <option value="1">Tier 1 — ICs</option>
            <option value="2">Tier 2 — Managers</option>
            <option value="3">Tier 3 — Executives</option>
          </select>
          <select id="hire-verbosity" style="flex:1">
            <option value="concise">Concise — 1-2 sentences</option>
            <option value="brief">Brief — 2-3 sentences</option>
            <option value="normal" selected>Normal</option>
            <option value="essay">Essay — 1-2 paragraphs</option>
            <option value="detailed">Detailed — thorough</option>
            <option value="dissertation">Dissertation — exhaustive</option>
          </select>
        </div>
      </div>
      <div class="modal-field">
        <label>Channels</label>
        <div id="hire-channels" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div class="modal-field">
        <label>Doc Folders</label>
        <div id="hire-folders" style="display:flex;flex-wrap:wrap;gap:6px"></div>
      </div>
      <div class="modal-field">
        <label>Character Prompt</label>
        <textarea id="hire-prompt" style="width:100%;min-height:200px;background:var(--input-bg);color:var(--text);border:1px solid var(--border-dark);padding:14px;border-radius:8px;font-size:13px;font-family:monospace;resize:vertical" placeholder="# Role Name&#10;&#10;You are [Name], the [Role]. You..."></textarea>
        <div class="field-hint">The full role prompt defining this agent's personality, responsibilities, and behavior.</div>
      </div>
    </div>
    <div class="modal-actions">
      <button class="modal-btn-cancel" onclick="closeModal('hire-modal')">Cancel</button>
      <button class="modal-btn-primary" id="hire-confirm" style="background:#2ecc71;border-color:#2ecc71">Hire</button>
    </div>
  </div>
</div>

<!-- Event Edit Modal -->
<div class="modal-overlay" id="event-edit-modal">
  <div class="modal" style="width:80vw;max-width:900px;height:80vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 id="event-edit-title" style="margin:0">Edit Event</h2>
      <div style="display:flex;gap:6px">
        <button class="session-btn" id="event-edit-history-btn" style="font-size:11px">History</button>
        <button class="modal-btn-cancel" id="event-edit-close">Cancel</button>
      </div>
    </div>
    <div style="flex:1;min-height:0;display:flex;gap:0;border-radius:8px;overflow:hidden;border:1px solid var(--border-dark)">
      <div style="flex:1;display:flex;flex-direction:column">
        <textarea id="event-edit-yaml" style="flex:1;background:var(--input-bg);color:var(--text);border:none;padding:16px;font-size:13px;font-family:monospace;line-height:1.5;resize:none;outline:none" placeholder="name: My Event..."></textarea>
      </div>
      <div id="event-edit-history" style="display:none;width:200px;min-width:200px;border-left:1px solid var(--border-dark);background:var(--sidebar);overflow-y:auto">
        <div style="padding:8px 12px;font-size:11px;font-weight:700;color:var(--text-dimmer);text-transform:uppercase;letter-spacing:0.5px">Version History</div>
        <div id="event-edit-history-list"></div>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-top:12px">
      <button class="session-btn" id="event-edit-delete" style="color:var(--accent);font-size:11px">Delete Event</button>
      <div style="flex:1"></div>
      <button class="modal-btn-cancel" onclick="closeModal('event-edit-modal')">Cancel</button>
      <button class="modal-btn-primary" id="event-edit-save">Save</button>
    </div>
  </div>
</div>

<!-- Loading Overlay -->
<div id="loading-overlay">
  <div class="spinner"></div>
  <div id="loading-text">Loading...</div>
</div>

<script>
// -- Theme System --
function applyTheme(t) {
  if (t === 'default') {
    document.documentElement.removeAttribute('data-theme');
  } else {
    document.documentElement.setAttribute('data-theme', t);
  }
  localStorage.setItem('cosimTheme', t);
  const sel = document.getElementById('theme-select');
  if (sel) sel.value = t;
}
(function() { applyTheme(localStorage.getItem('cosimTheme') || 'default'); })();
document.getElementById('theme-select').addEventListener('change', function() { applyTheme(this.value); });

const messagesPanel = document.getElementById('messages-panel');
const input = document.getElementById('msg-input');
const sendBtn = document.getElementById('send-btn');
const senderName = document.getElementById('sender-name');
const senderRole = document.getElementById('sender-role');
const senderRoleCustom = document.getElementById('sender-role-custom');

// Sticky name per role — remember the last name typed for each role
const ROLE_NAMES_KEY = 'company-sim-role-names';

function getRoleNames() {
  try { return JSON.parse(localStorage.getItem(ROLE_NAMES_KEY)) || {}; } catch(e) { return {}; }
}

function saveNameForRole() {
  const role = senderRole.value === 'custom' ? 'custom:' + senderRoleCustom.value.trim() : senderRole.value;
  const names = getRoleNames();
  names[role] = senderName.value.trim();
  localStorage.setItem(ROLE_NAMES_KEY, JSON.stringify(names));
}

function recallNameForRole() {
  const role = senderRole.value === 'custom' ? 'custom:' + senderRoleCustom.value.trim() : senderRole.value;
  const names = getRoleNames();
  if (role in names) senderName.value = names[role];
}

senderName.addEventListener('input', saveNameForRole);

senderRole.addEventListener('change', () => {
  if (senderRole.value === 'custom') {
    senderRoleCustom.style.display = '';
    senderRoleCustom.focus();
  } else {
    senderRoleCustom.style.display = 'none';
  }
  recallNameForRole();
});

senderRoleCustom.addEventListener('input', () => {
  saveNameForRole();
});

// Restore name on page load
recallNameForRole();

function getSenderLabel() {
  const name = senderName.value.trim() || 'Anonymous';
  let role = senderRole.value;
  if (role === 'custom') role = senderRoleCustom.value.trim();
  if (!role) return name;
  return name + ' (' + role + ')';
}

const channelTitle = document.getElementById('channel-title');
const channelDesc = document.getElementById('channel-desc');
const channelMembersEl = document.getElementById('channel-members');

let currentTab = 'chat';
let currentChannel = '#general';
let channelsData = {};
let messagesByChannel = {};
let unreadByChannel = {};
let seenIds = new Set();

// Agent persona maps — loaded dynamically from /api/personas
let SENDER_CLASS_MAP = {};
let PERSONA_DISPLAY = {};
let AGENT_NAMES = new Set();
let PERSONA_AVATARS = {};  // display_name → {avatar: url_or_null, initial: "P", color: "#..."}

// Color palette for agent personas (assigned round-robin on load)
const AGENT_COLORS = [
  'var(--accent)', '#f39c12', '#9b59b6', '#2ecc71', '#1abc9c',
  '#e67e22', '#f1c40f', '#3498db', '#e056a0', '#00bcd4', '#ff6b6b',
];

async function loadPersonas() {
  const resp = await fetch('/api/personas');
  const personas = await resp.json();
  SENDER_CLASS_MAP = {};
  PERSONA_DISPLAY = {};
  PERSONA_AVATARS = {};
  const keys = Object.keys(personas);
  keys.forEach((key, i) => {
    const p = personas[key];
    const cls = 'msg-agent-' + i;
    SENDER_CLASS_MAP[p.display_name] = cls;
    PERSONA_DISPLAY[key] = p.display_name;
    const color = AGENT_COLORS[i % AGENT_COLORS.length];
    PERSONA_AVATARS[p.display_name] = {
      avatar: p.avatar ? '/avatars/' + p.avatar : null,
      initial: p.display_name.charAt(0).toUpperCase(),
      color: color,
    };
  });
  AGENT_NAMES = new Set(Object.keys(SENDER_CLASS_MAP));

  // Inject dynamic CSS for agent colors
  let styleEl = document.getElementById('agent-colors-style');
  if (!styleEl) {
    styleEl = document.createElement('style');
    styleEl.id = 'agent-colors-style';
    document.head.appendChild(styleEl);
  }
  let css = '';
  keys.forEach((key, i) => {
    const color = AGENT_COLORS[i % AGENT_COLORS.length];
    css += '.msg-agent-' + i + ' .sender { color: ' + color + '; } ';
  });
  styleEl.textContent = css;

  // Update ticket dropdowns with current personas
  populateAllRoleDropdowns();
}

// Known human persona CSS classes
const HUMAN_CLASS_MAP = {
  'Customer': 'msg-customer', 'Consultant': 'msg-customer',
  'Board Member': 'msg-board', 'Hacker': 'msg-hacker', 'God': 'msg-god',
  'Intern': 'msg-intern', 'Competitor': 'msg-competitor',
  'Regulator': 'msg-regulator', 'Investor': 'msg-investor', 'The Press': 'msg-press',
};

function isAgent(sender) {
  if (AGENT_NAMES.has(sender)) return true;
  if (sender === 'System') return true;
  // For messages from other scenarios — if sender isn't a known human role, treat as agent
  if (HUMAN_CLASS_MAP[sender]) return false;
  // Check if it looks like "Name (Role)" pattern used by agents vs "Name (Role)" used by humans
  // Human senders come from getSenderLabel() and use roles from the role dropdown
  // Agent senders come from persona display_name which is set in scenario config
  // If it's not in AGENT_NAMES and not in HUMAN_CLASS_MAP, check the role dropdown values
  const humanRoles = ['Consultant','Customer','New Hire','Board Member','Intern','Vendor',
    'Investor','Auditor','Competitor','Regulator','The Press','Hacker','God'];
  for (const role of humanRoles) {
    if (sender.endsWith('(' + role + ')')) return false;
  }
  // If sender has parens and isn't a known human role, likely an agent from another scenario
  if (sender.includes('(') && sender.includes(')')) return true;
  // Bare name with no parens — human with no role selected
  return false;
}

function senderClass(sender) {
  return SENDER_CLASS_MAP[sender] || HUMAN_CLASS_MAP[sender] || (isAgent(sender) ? 'msg-agent' : 'msg-customer');
}

// Generate a consistent color from a string (for human users)
function hashColor(str) {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = ((hash % 360) + 360) % 360;
  return 'hsl(' + hue + ', 70%, 65%)';
}

function renderMarkdown(text) {
  if (typeof marked !== 'undefined') return marked.parse(text);
  return escapeHtml(text);
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// -- Tabs --

document.querySelectorAll('.header-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    const target = tab.dataset.tab;
    if (target === currentTab) return;
    currentTab = target;
    document.querySelectorAll('.header-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
    document.getElementById(target + '-pane').classList.add('active');
    if (target === 'chat') { renderSidebar(); renderMessages(); }
    if (target === 'docs') loadDocs();
    if (target === 'gitlab') loadRepos();
    if (target === 'tickets') loadTickets();
    if (target === 'npcs') loadNPCs();
    if (target === 'events') loadEventPool();
    if (target === 'email') loadEmails();
    if (target === 'memos') loadMemoThreads();
    if (target === 'blog') loadBlogPosts();
    if (target === 'recap') renderRecapList();
    if (target === 'usage') loadUsage();
    if (target === 'advanced') loadSessionManagerTable();
  });
});

// -- Channel sidebar --

async function loadChannels() {
  const resp = await fetch('/api/channels');
  const list = await resp.json();
  channelsData = {};
  list.forEach(ch => {
    channelsData[ch.name] = ch;
    if (!messagesByChannel[ch.name]) messagesByChannel[ch.name] = [];
    if (unreadByChannel[ch.name] === undefined) unreadByChannel[ch.name] = 0;
  });
  renderSidebar();
}

function renderSidebar() {
  const intContainer = document.getElementById('internal-channels');
  const extContainer = document.getElementById('external-channels');
  const dirContainer = document.getElementById('director-channels');
  const sysContainer = document.getElementById('system-channels');
  intContainer.innerHTML = '';
  extContainer.innerHTML = '';
  dirContainer.innerHTML = '';
  sysContainer.innerHTML = '';

  Object.keys(channelsData).sort().forEach(name => {
    const ch = channelsData[name];
    const btn = document.createElement('button');
    btn.className = 'channel-btn' + (name === currentChannel ? ' active' : '');
    const badge = document.createElement('span');
    badge.className = 'unread-badge' + (unreadByChannel[name] > 0 && name !== currentChannel ? ' visible' : '');
    badge.textContent = unreadByChannel[name] || '';
    badge.id = 'badge-' + name.replace('#', '');
    // Show persona display name for director channels instead of raw channel name
    const label = ch.is_director ? (PERSONA_DISPLAY[ch.director_persona] || name) : name;
    btn.innerHTML = '<span>' + escapeHtml(label) + '</span>';
    btn.appendChild(badge);
    btn.addEventListener('click', () => switchChannel(name));
    if (ch.is_system) {
      sysContainer.appendChild(btn);
    } else if (ch.is_director) {
      dirContainer.appendChild(btn);
    } else if (ch.is_external) {
      extContainer.appendChild(btn);
    } else {
      intContainer.appendChild(btn);
    }
  });
}

function switchChannel(name) {
  currentChannel = name;
  unreadByChannel[name] = 0;
  renderSidebar();
  updateChannelHeader();
  renderMessages();
  loadMessages(name);
  updateSenderDropdown();
  // Hide persona bar in director channels
  const ch = channelsData[name];
  const personaBar = document.getElementById('persona-bar');
  if (personaBar) {
    personaBar.style.display = (ch && ch.is_director) ? 'none' : '';
  }
}

function updateChannelHeader() {
  const ch = channelsData[currentChannel];
  channelTitle.textContent = currentChannel;
  channelDesc.textContent = ch ? ch.description : '';
  if (ch && ch.members && ch.members.length > 0) {
    const names = ch.members.map(k => PERSONA_DISPLAY[k] || k).join(', ');
    channelMembersEl.textContent = 'Members: ' + names;
  } else {
    channelMembersEl.textContent = '';
  }
}

function updateSenderDropdown() {
  // Sender controls are always visible — user picks name + role freely via persona bar
}

// -- Messages --

function addMessage(msg) {
  if (seenIds.has(msg.id)) return;
  seenIds.add(msg.id);
  const ch = msg.channel || '#general';
  if (!messagesByChannel[ch]) messagesByChannel[ch] = [];
  messagesByChannel[ch].push(msg);

  if (ch === currentChannel && currentTab === 'chat') {
    appendMessageEl(msg);
  } else {
    unreadByChannel[ch] = (unreadByChannel[ch] || 0) + 1;
    renderSidebar();
  }
}

function appendMessageEl(msg) {
  const div = document.createElement('div');
  const cls = senderClass(msg.sender);
  const agent = isAgent(msg.sender);
  div.className = 'msg ' + (agent ? 'msg-agent' : 'msg-customer') + ' ' + cls;
  const ts = new Date(msg.timestamp * 1000).toLocaleTimeString();
  // For human senders, derive a unique color from their name
  const senderStyle = agent ? '' : ' style="color:' + hashColor(msg.sender) + '"';
  // Build avatar element
  let avatarHtml = '';
  const pa = PERSONA_AVATARS[msg.sender];
  if (pa) {
    if (pa.avatar) {
      avatarHtml = '<div class="msg-avatar"><img src="' + escapeHtml(pa.avatar) + '" alt=""></div>';
    } else {
      avatarHtml = '<div class="msg-avatar" style="background:' + pa.color + '">' + pa.initial + '</div>';
    }
  } else if (msg.sender === 'System') {
    avatarHtml = '<div class="msg-avatar" style="background:#666">S</div>';
  } else {
    // Human sender fallback
    const hc = hashColor(msg.sender);
    const hi = msg.sender.charAt(0).toUpperCase();
    avatarHtml = '<div class="msg-avatar" style="background:' + hc + '">' + hi + '</div>';
  }
  div.innerHTML = '<div class="msg-row">' + avatarHtml
    + '<div class="msg-body">'
    + '<div class="sender"' + senderStyle + '>' + escapeHtml(msg.sender) + '</div>'
    + '<div class="content">' + renderMarkdown(msg.content) + '</div>'
    + '<div class="ts">' + ts + '</div>'
    + '</div></div>';
  messagesPanel.appendChild(div);
  messagesPanel.scrollTop = messagesPanel.scrollHeight;
}

function renderMessages() {
  messagesPanel.innerHTML = '';
  const msgs = messagesByChannel[currentChannel] || [];
  msgs.forEach(appendMessageEl);
  renderTypingIndicators();
}

// -- Typing indicators --
const _typingState = {};  // channel -> {sender -> timestamp}

function handleTypingIndicator(data) {
  const ch = data.channel || '#general';
  if (!_typingState[ch]) _typingState[ch] = {};
  if (data.active) {
    _typingState[ch][data.sender] = Date.now();
  } else {
    delete _typingState[ch][data.sender];
  }
  if (ch === currentChannel) renderTypingIndicators();
}

function renderTypingIndicators() {
  let el = document.getElementById('typing-indicator');
  if (!el) {
    el = document.createElement('div');
    el.id = 'typing-indicator';
    el.style.cssText = 'padding:4px 20px;font-size:12px;color:var(--text-dim);font-style:italic;min-height:18px;';
    messagesPanel.parentNode.insertBefore(el, messagesPanel.nextSibling);
  }
  const typers = _typingState[currentChannel] || {};
  // Clean stale entries (older than 60s)
  const now = Date.now();
  for (const [sender, ts] of Object.entries(typers)) {
    if (now - ts > 60000) delete typers[sender];
  }
  const names = Object.keys(typers);
  if (names.length === 0) {
    el.textContent = '';
  } else if (names.length === 1) {
    el.textContent = names[0] + ' is thinking...';
  } else if (names.length === 2) {
    el.textContent = names[0] + ' and ' + names[1] + ' are thinking...';
  } else {
    el.textContent = names.slice(0, -1).join(', ') + ', and ' + names[names.length-1] + ' are thinking...';
  }
}

// Clean stale typing indicators every 10s
setInterval(() => { if (currentTab === 'chat') renderTypingIndicators(); }, 10000);

async function loadMessages(channel) {
  let url = '/api/messages';
  if (channel) url += '?channels=' + encodeURIComponent(channel);
  const resp = await fetch(url);
  const msgs = await resp.json();
  msgs.forEach(addMessage);
}

function connectSSE() {
  const es = new EventSource('/api/messages/stream');
  es.addEventListener('message', (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'channel_update') {
      if (channelsData[data.channel]) {
        channelsData[data.channel].members = data.members;
        if (data.channel === currentChannel) updateChannelHeader();
      }
    } else if (data.type === 'doc_event') {
      if (currentTab === 'docs') loadDocs();
    } else if (data.type === 'gitlab_event') {
      if (currentTab === 'gitlab') loadRepos();
    } else if (data.type === 'tickets_event') {
      if (currentTab === 'tickets') {
        loadTickets();
        if (tkCurrentViewId) viewTicket(tkCurrentViewId);
      }
    } else if (data.type === 'typing') {
      handleTypingIndicator(data);
    } else {
      addMessage(data);
    }
  });
  es.onopen = () => { loadMessages(); };
  es.onerror = () => { setTimeout(connectSSE, 2000); es.close(); };
}

async function send() {
  const content = input.value.trim();
  if (!content) return;
  const ch = channelsData[currentChannel];
  const sender = (ch && ch.is_director) ? 'Scenario Director' : getSenderLabel();
  input.value = '';
  await fetch('/api/messages', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sender, content, channel: currentChannel}),
  });
}

async function clearChat() {
  if (!confirm('Clear all messages?')) return;
  await fetch('/api/messages/clear', {method: 'POST'});
  messagesByChannel = {};
  Object.keys(channelsData).forEach(ch => messagesByChannel[ch] = []);
  seenIds.clear();
  unreadByChannel = {};
  renderSidebar();
  renderMessages();
}

sendBtn.addEventListener('click', send);
// clear-btn removed — now in Advanced tab
input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send(); });

// -- Docs tab --
const docsList = document.getElementById('docs-list');
const docsEmpty = document.getElementById('docs-empty');
const docsSearch = document.getElementById('docs-search');
const docViewer = document.getElementById('doc-viewer');
const docViewerTitle = document.getElementById('doc-viewer-title');
const docViewerContent = document.getElementById('doc-viewer-content');
const docBackBtn = document.getElementById('doc-back-btn');

let currentFolder = '';  // '' means all folders
let foldersData = [];

async function loadFolders() {
  const resp = await fetch('/api/folders');
  foldersData = await resp.json();
  renderFolderSidebar();
}

function renderFolderSidebar() {
  const sharedC = document.getElementById('shared-folders');
  const deptC = document.getElementById('dept-folders');
  const persC = document.getElementById('personal-folders');
  sharedC.innerHTML = ''; deptC.innerHTML = ''; persC.innerHTML = '';

  foldersData.forEach(f => {
    const btn = document.createElement('button');
    btn.className = 'folder-btn' + (currentFolder === f.name ? ' active' : '');
    btn.dataset.folder = f.name;
    btn.textContent = f.name;
    btn.addEventListener('click', () => switchFolder(f.name));
    if (f.type === 'shared' || f.type === 'public') sharedC.appendChild(btn);
    else if (f.type === 'department') deptC.appendChild(btn);
    else if (f.type === 'personal') persC.appendChild(btn);
  });

  // Update "All" button
  const allBtn = document.getElementById('folder-all');
  allBtn.className = 'folder-btn' + (currentFolder === '' ? ' active' : '');
}

function switchFolder(folderName) {
  currentFolder = folderName;
  renderFolderSidebar();
  loadDocs();
}

document.getElementById('folder-all').addEventListener('click', () => switchFolder(''));

async function loadDocs(query) {
  let url = '/api/docs';
  const params = [];
  if (query) {
    url = '/api/docs/search';
    params.push('q=' + encodeURIComponent(query));
    if (currentFolder) params.push('folders=' + encodeURIComponent(currentFolder));
  } else if (currentFolder) {
    params.push('folder=' + encodeURIComponent(currentFolder));
  }
  if (params.length) url += '?' + params.join('&');
  const resp = await fetch(url);
  const docs = await resp.json();
  renderDocList(docs);
}

function renderDocList(docs) {
  docsList.querySelectorAll('.doc-card').forEach(el => el.remove());
  docsEmpty.style.display = docs.length ? 'none' : 'block';
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  docs.forEach(doc => {
    const card = document.createElement('div');
    card.className = 'doc-card';
    const folder = doc.folder || 'shared';
    const author = doc.created_by || '';
    const created = doc.created_at ? new Date(doc.created_at * 1000).toLocaleString() : '';
    const updated = doc.updated_at && doc.updated_at !== doc.created_at ? new Date(doc.updated_at * 1000).toLocaleString() : '';
    const editedBy = doc.updated_by || '';
    let dateLine = created ? 'Created ' + created : '';
    if (updated) dateLine += (dateLine ? ' | ' : '') + 'Edited ' + updated + (editedBy ? ' by ' + escapeHtml(editedBy) : '');
    card.innerHTML = '<div class="doc-card-meta">'
      + '<span class="doc-card-folder">' + escapeHtml(folder) + '</span>'
      + (author ? '<span style="font-size:11px;color:var(--text-dim)">' + escapeHtml(author) + '</span>' : '')
      + '</div>'
      + '<div class="doc-card-title">' + escapeHtml(doc.title || doc.slug) + '</div>'
      + (dateLine ? '<div style="font-size:10px;color:var(--text-dimmer);margin-bottom:4px">' + dateLine + '</div>' : '')
      + '<div class="doc-card-preview">' + escapeHtml(doc.preview || '') + '</div>';
    card.addEventListener('click', () => viewDoc(folder, doc.slug));
    docsList.appendChild(card);
  });
}

let _currentDoc = null; // {folder, slug, content, ...}

async function viewDoc(folder, slug) {
  const resp = await fetch('/api/docs/' + encodeURIComponent(folder) + '/' + encodeURIComponent(slug));
  if (!resp.ok) return;
  _currentDoc = await resp.json();
  _currentDoc.folder = folder;
  _currentDoc.slug = slug;
  const createdBy = _currentDoc.created_by || '';
  const updatedBy = _currentDoc.updated_by || '';
  const createdAt = _currentDoc.created_at ? new Date(_currentDoc.created_at * 1000).toLocaleString() : '';
  const updatedAt = _currentDoc.updated_at ? new Date(_currentDoc.updated_at * 1000).toLocaleString() : '';
  let meta = createdBy ? 'Created by ' + createdBy : '';
  if (createdAt) meta += (meta ? ' on ' : '') + createdAt;
  if (updatedBy && updatedBy !== createdBy) meta += ' | Edited by ' + updatedBy + ' on ' + updatedAt;
  else if (updatedAt && updatedAt !== createdAt) meta += ' | Updated ' + updatedAt;
  docViewerTitle.innerHTML = escapeHtml(_currentDoc.title || _currentDoc.slug) +
    (meta ? '<div style="font-size:11px;color:var(--text-dim);font-weight:400;margin-top:2px">' + escapeHtml(meta) + '</div>' : '');
  document.getElementById('doc-viewer-content').innerHTML = renderMarkdown(_currentDoc.content || '');
  document.getElementById('doc-viewer-body').style.display = 'flex';
  document.getElementById('doc-edit-area').style.display = 'none';
  docViewer.classList.add('open');
  docsList.style.display = 'none';
  document.getElementById('docs-toolbar').style.display = 'none';
  // Auto-show history panel
  loadDocHistory();
}

docBackBtn.addEventListener('click', () => {
  docViewer.classList.remove('open');
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  _currentDoc = null;
});

// Edit button
document.getElementById('doc-edit-btn').addEventListener('click', () => {
  if (!_currentDoc) return;
  document.getElementById('doc-edit-textarea').value = _currentDoc.content || '';
  document.getElementById('doc-edit-author-name').value = senderName.value;
  document.getElementById('doc-edit-author-role').value = senderRole.value || '';
  document.getElementById('doc-viewer-body').style.display = 'none';
  document.getElementById('doc-edit-area').style.display = 'flex';
});

document.getElementById('doc-edit-cancel').addEventListener('click', () => {
  document.getElementById('doc-edit-area').style.display = 'none';
  document.getElementById('doc-viewer-body').style.display = 'flex';
});

document.getElementById('doc-edit-save').addEventListener('click', async () => {
  if (!_currentDoc) return;
  const content = document.getElementById('doc-edit-textarea').value;
  const editName = document.getElementById('doc-edit-author-name').value.trim() || 'Anonymous';
  const editRole = document.getElementById('doc-edit-author-role').value;
  const author = editRole ? editName + ' (' + editRole + ')' : editName;
  const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({content, author}),
  });
  if (resp.ok) {
    // Reload the doc
    await viewDoc(_currentDoc.folder, _currentDoc.slug);
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

// History panel
async function loadDocHistory() {
  if (!_currentDoc) return;
  const panel = document.getElementById('doc-history-panel');
  const list = document.getElementById('doc-history-list');
  list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">Loading...</div>';
  panel.style.display = '';
  const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug) + '/history');
  const history = await resp.json();
  list.innerHTML = '';
  if (!history.length) {
    list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">No version history</div>';
    return;
  }
  history.forEach((v, i) => {
    const item = document.createElement('div');
    item.className = 'thought-item' + (i === 0 ? ' active' : '');
    const ts = new Date(v.updated_at * 1000);
    const label = v.is_current ? 'Current' : 'v' + (history.length - i);
    item.innerHTML = '<div class="thought-item-time">' + escapeHtml(label) + ' - ' + ts.toLocaleString() + '</div>' +
      '<div class="thought-item-preview">' + escapeHtml(v.updated_by || 'unknown') + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('#doc-history-list .thought-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('doc-viewer-content').innerHTML = renderMarkdown(v.content || '');
    });
    if (!v.is_current) {
      const restoreBtn = document.createElement('button');
      restoreBtn.className = 'session-btn';
      restoreBtn.style.cssText = 'font-size:10px;padding:2px 8px;margin-top:4px;width:100%';
      restoreBtn.textContent = 'Restore this version';
      restoreBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const resp = await fetch('/api/docs/' + encodeURIComponent(_currentDoc.folder) + '/' + encodeURIComponent(_currentDoc.slug), {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({content: v.content, author: 'Restored by Scenario Director'}),
        });
        if (resp.ok) {
          await viewDoc(_currentDoc.folder, _currentDoc.slug);
        }
      });
      item.appendChild(restoreBtn);
    }
    list.appendChild(item);
  });
}

document.getElementById('doc-download-btn').addEventListener('click', () => {
  if (!_currentDoc) return;
  const blob = new Blob([_currentDoc.content || ''], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (_currentDoc.slug || 'document') + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
});

document.getElementById('doc-history-btn').addEventListener('click', () => {
  const panel = document.getElementById('doc-history-panel');
  if (panel.style.display !== 'none') {
    panel.style.display = 'none';
  } else {
    loadDocHistory();
  }
});

let docsSearchTimer = null;
docsSearch.addEventListener('input', () => {
  clearTimeout(docsSearchTimer);
  docsSearchTimer = setTimeout(() => {
    const q = docsSearch.value.trim();
    loadDocs(q || undefined);
  }, 300);
});

// -- Doc editor --
const docEditor = document.getElementById('doc-editor');
const docEditorTitle = document.getElementById('doc-editor-title');
const docEditorFolder = document.getElementById('doc-editor-folder');
const docEditorContent = document.getElementById('doc-editor-content');

document.getElementById('new-doc-btn').addEventListener('click', () => {
  // Populate folder dropdown from sidebar folders
  docEditorFolder.innerHTML = '';
  const allFolders = foldersData.map(f => f.name).sort();
  allFolders.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f;
    opt.textContent = f;
    if (f === currentFolder || (!currentFolder && f === 'shared')) opt.selected = true;
    docEditorFolder.appendChild(opt);
  });
  docEditorTitle.value = '';
  docEditorContent.value = '';
  // Pre-fill author from persona bar
  document.getElementById('doc-author-name').value = senderName.value;
  document.getElementById('doc-author-role').value = senderRole.value || '';
  docEditor.style.display = 'flex';
  docsList.style.display = 'none';
  document.getElementById('docs-toolbar').style.display = 'none';
  docViewer.classList.remove('open');
  docEditorTitle.focus();
});

document.getElementById('doc-editor-cancel').addEventListener('click', () => {
  docEditor.style.display = 'none';
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
});

document.getElementById('doc-editor-save').addEventListener('click', async () => {
  const title = docEditorTitle.value.trim();
  const content = docEditorContent.value;
  const folder = docEditorFolder.value;
  if (!title) { alert('Title is required'); return; }
  const docName = document.getElementById('doc-author-name').value.trim() || 'Anonymous';
  const docRole = document.getElementById('doc-author-role').value;
  const author = docRole ? docName + ' (' + docRole + ')' : docName;
  const resp = await fetch('/api/docs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, content, folder, author}),
  });
  if (!resp.ok) {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
    return;
  }
  docEditor.style.display = 'none';
  docsList.style.display = '';
  document.getElementById('docs-toolbar').style.display = '';
  loadDocs();
});

// -- GitLab tab --
let glRepos = [];
let glCurrentRepo = null;
let glCurrentView = 'tree';
let glCurrentPath = '';

async function loadRepos() {
  const resp = await fetch('/api/gitlab/repos');
  glRepos = await resp.json();
  renderRepoSidebar();
  if (glCurrentRepo) {
    const exists = glRepos.find(r => r.name === glCurrentRepo);
    if (!exists) { glCurrentRepo = null; glCurrentPath = ''; }
  }
  if (glCurrentRepo) {
    if (glCurrentView === 'tree') loadTree(glCurrentRepo, glCurrentPath);
    else loadCommits(glCurrentRepo);
  } else {
    document.getElementById('gitlab-content').innerHTML =
      '<div id="gitlab-empty">No repositories yet.</div>';
    document.getElementById('gitlab-repo-title').textContent = 'Select a repository';
    document.getElementById('gitlab-repo-desc').textContent = '';
  }
}

function renderRepoSidebar() {
  const container = document.getElementById('gitlab-repo-list');
  container.innerHTML = '';
  glRepos.forEach(repo => {
    const btn = document.createElement('button');
    btn.className = 'repo-btn' + (glCurrentRepo === repo.name ? ' active' : '');
    btn.textContent = repo.name;
    btn.addEventListener('click', () => switchRepo(repo.name));
    container.appendChild(btn);
  });
}

function switchRepo(name) {
  glCurrentRepo = name;
  glCurrentPath = '';
  glCurrentView = 'tree';
  renderRepoSidebar();
  updateGlToggles();
  const repo = glRepos.find(r => r.name === name);
  document.getElementById('gitlab-repo-title').textContent = name;
  document.getElementById('gitlab-repo-desc').textContent = repo ? (repo.description || '') : '';
  loadTree(name, '');
}

function updateGlToggles() {
  document.getElementById('gl-toggle-tree').className =
    'gitlab-toggle-btn' + (glCurrentView === 'tree' ? ' active' : '');
  document.getElementById('gl-toggle-commits').className =
    'gitlab-toggle-btn' + (glCurrentView === 'commits' ? ' active' : '');
}

document.getElementById('gl-toggle-tree').addEventListener('click', () => {
  if (!glCurrentRepo) return;
  glCurrentView = 'tree'; glCurrentPath = ''; updateGlToggles();
  loadTree(glCurrentRepo, '');
});
document.getElementById('gl-toggle-commits').addEventListener('click', () => {
  if (!glCurrentRepo) return;
  glCurrentView = 'commits'; updateGlToggles();
  loadCommits(glCurrentRepo);
});

async function loadTree(project, path) {
  let url = '/api/gitlab/repos/' + encodeURIComponent(project) + '/tree';
  if (path) url += '?path=' + encodeURIComponent(path);
  const resp = await fetch(url);
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error loading tree.</div>'; return; }
  const data = await resp.json();
  renderTree(data, path);
}

function renderTree(entries, path) {
  const content = document.getElementById('gitlab-content');
  let html = '';
  // Breadcrumbs
  const parts = path ? path.split('/') : [];
  html += '<div class="gitlab-breadcrumbs">';
  html += '<a onclick="glNavTree(\\'\\')">root</a>';
  let acc = '';
  parts.forEach((p, i) => {
    acc += (i > 0 ? '/' : '') + p;
    html += ' / <a onclick="glNavTree(\\'' + acc + '\\')">' + escapeHtml(p) + '</a>';
  });
  html += '</div>';
  // Entries
  if (entries.length === 0) {
    html += '<div id="gitlab-empty">Empty directory.</div>';
  }
  entries.forEach(e => {
    const isDir = e.type === 'dir';
    const icon = isDir ? '&#128193;' : '&#128196;';
    html += '<div class="tree-item" onclick="glClickEntry(\\'' + escapeHtml(e.path) + '\\', \\'' + e.type + '\\')">'
      + '<span class="tree-item-icon">' + icon + '</span>'
      + '<span class="tree-item-name">' + escapeHtml(e.name) + '</span></div>';
  });
  content.innerHTML = html;
}

function glNavTree(path) { glCurrentPath = path; loadTree(glCurrentRepo, path); }
function glClickEntry(path, type) {
  if (type === 'dir') { glCurrentPath = path; loadTree(glCurrentRepo, path); }
  else { loadFileContent(glCurrentRepo, path); }
}

async function loadFileContent(project, path) {
  const resp = await fetch('/api/gitlab/repos/' + encodeURIComponent(project) + '/file?path=' + encodeURIComponent(path));
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error reading file.</div>'; return; }
  const data = await resp.json();
  const content = document.getElementById('gitlab-content');
  const parts = path.split('/');
  let html = '<div class="gitlab-breadcrumbs">';
  html += '<a onclick="glNavTree(\\'\\')">root</a>';
  let acc = '';
  parts.forEach((p, i) => {
    acc += (i > 0 ? '/' : '') + p;
    if (i < parts.length - 1) {
      html += ' / <a onclick="glNavTree(\\'' + acc + '\\')">' + escapeHtml(p) + '</a>';
    } else {
      html += ' / ' + escapeHtml(p);
    }
  });
  html += '</div>';
  html += '<div class="gitlab-file-viewer">' + escapeHtml(data.content || '') + '</div>';
  content.innerHTML = html;
}

async function loadCommits(project) {
  const resp = await fetch('/api/gitlab/repos/' + encodeURIComponent(project) + '/log');
  if (!resp.ok) { document.getElementById('gitlab-content').innerHTML = '<div id="gitlab-empty">Error loading commits.</div>'; return; }
  const commits = await resp.json();
  renderCommits(commits);
}

function renderCommits(commits) {
  const content = document.getElementById('gitlab-content');
  if (commits.length === 0) {
    content.innerHTML = '<div id="gitlab-empty">No commits yet.</div>';
    return;
  }
  let html = '';
  commits.forEach(c => {
    const ts = new Date(c.timestamp * 1000).toLocaleString();
    html += '<div class="commit-item">'
      + '<span class="commit-item-id">' + escapeHtml(c.id) + '</span>'
      + '<span class="commit-item-msg">' + escapeHtml(c.message) + '</span>'
      + '<div class="commit-item-meta">' + escapeHtml(c.author) + ' - ' + ts
      + ' - ' + (c.files ? c.files.length : 0) + ' file(s)</div></div>';
  });
  content.innerHTML = html;
}

// -- GitLab new repo --
document.getElementById('gl-new-repo-btn').addEventListener('click', () => {
  document.getElementById('gl-new-repo-form').style.display = '';
  document.getElementById('gl-new-repo-btn').style.display = 'none';
  document.getElementById('gl-new-repo-name').value = '';
  document.getElementById('gl-new-repo-desc').value = '';
  document.getElementById('gl-new-repo-name').focus();
});

document.getElementById('gl-new-repo-cancel').addEventListener('click', () => {
  document.getElementById('gl-new-repo-form').style.display = 'none';
  document.getElementById('gl-new-repo-btn').style.display = '';
});

document.getElementById('gl-new-repo-save').addEventListener('click', async () => {
  const name = document.getElementById('gl-new-repo-name').value.trim();
  if (!name) return;
  const desc = document.getElementById('gl-new-repo-desc').value.trim();
  const author = getSenderLabel();
  const resp = await fetch('/api/gitlab/repos', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, description: desc, author}),
  });
  if (resp.ok) {
    document.getElementById('gl-new-repo-form').style.display = 'none';
    document.getElementById('gl-new-repo-btn').style.display = '';
    loadRepos();
    switchRepo(name);
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

document.getElementById('gl-new-repo-name').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') document.getElementById('gl-new-repo-save').click();
  if (e.key === 'Escape') document.getElementById('gl-new-repo-cancel').click();
});

// -- Tickets tab --
let tkAllTickets = [];
let tkStatusFilter = '';

document.querySelectorAll('.tickets-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    tkStatusFilter = btn.dataset.status;
    document.querySelectorAll('.tickets-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    renderTicketList();
  });
});

async function loadTickets() {
  const resp = await fetch('/api/tickets');
  tkAllTickets = await resp.json();
  updateTicketCounts();
  renderTicketList();
}

function updateTicketCounts() {
  const counts = { all: tkAllTickets.length, open: 0, in_progress: 0, resolved: 0, closed: 0 };
  tkAllTickets.forEach(t => { if (counts[t.status] !== undefined) counts[t.status]++; });
  ['all', 'open', 'in_progress', 'resolved', 'closed'].forEach(s => {
    const el = document.getElementById('tk-count-' + s);
    if (el) el.textContent = counts[s] > 0 ? '(' + counts[s] + ')' : '';
  });
}

function renderTicketList() {
  const list = document.getElementById('tickets-list');
  list.querySelectorAll('.ticket-card').forEach(el => el.remove());
  const empty = document.getElementById('tickets-empty');
  const detail = document.getElementById('ticket-detail');
  detail.classList.remove('open');
  list.style.display = '';

  let filtered = tkAllTickets;
  if (tkStatusFilter) filtered = filtered.filter(t => t.status === tkStatusFilter);

  // Sort: critical > high > medium > low, then by updated_at desc
  const priOrder = { critical: 0, high: 1, medium: 2, low: 3 };
  filtered.sort((a, b) => (priOrder[a.priority] || 3) - (priOrder[b.priority] || 3) || b.updated_at - a.updated_at);

  empty.style.display = filtered.length ? 'none' : 'block';
  filtered.forEach(t => {
    const card = document.createElement('div');
    card.className = 'ticket-card';
    const assignee = t.assignee ? t.assignee : 'Unassigned';
    card.innerHTML = '<div class="ticket-card-top">'
      + '<span class="ticket-card-id">' + escapeHtml(t.id) + '</span>'
      + '<span class="ticket-card-title">' + escapeHtml(t.title) + '</span>'
      + '</div>'
      + '<div class="ticket-card-bottom">'
      + '<span class="tk-badge tk-status-' + t.status + '">' + escapeHtml(t.status) + '</span>'
      + '<span class="tk-badge tk-priority-' + t.priority + '">' + escapeHtml(t.priority) + '</span>'
      + '<span class="tk-assignee">' + escapeHtml(assignee) + '</span>'
      + '</div>';
    card.addEventListener('click', () => viewTicket(t.id));
    list.appendChild(card);
  });
}

let tkCurrentViewId = null;

// Built dynamically from PERSONA_DISPLAY after loadPersonas()
let TK_ASSIGNEE_OPTIONS = [''];

let HUMAN_ROLES = [];
let JOB_TITLES = [];

async function loadRoles() {
  try {
    const resp = await fetch('/api/roles');
    const data = await resp.json();
    HUMAN_ROLES = data.human_roles || [];
    JOB_TITLES = data.job_titles || [];
  } catch(e) {
    HUMAN_ROLES = ['Scenario Director', 'Consultant', 'Customer'];
    JOB_TITLES = ['PM', 'Senior Eng'];
  }
  populateAllRoleDropdowns();
}

function populateRoleSelect(sel, roles, opts) {
  if (!sel) return;
  const defaultVal = opts?.default || '';
  const includeEmpty = opts?.empty;
  const emptyLabel = opts?.emptyLabel || '';
  sel.innerHTML = '';
  if (includeEmpty) {
    sel.innerHTML += '<option value="">' + escapeHtml(emptyLabel) + '</option>';
  }
  roles.forEach(role => {
    const selected = role === defaultVal ? ' selected' : '';
    sel.innerHTML += '<option value="' + escapeHtml(role) + '"' + selected + '>' + escapeHtml(role) + '</option>';
  });
}

function populateAllRoleDropdowns() {
  const agentNames = Object.values(PERSONA_DISPLAY);

  // Build assignee options
  TK_ASSIGNEE_OPTIONS = [''];
  agentNames.forEach(name => TK_ASSIGNEE_OPTIONS.push(name));

  // Chat persona bar role
  const senderRoleSel = document.getElementById('sender-role');
  if (senderRoleSel) {
    const customOpt = '<option value="custom">Custom...</option>';
    populateRoleSelect(senderRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});
    senderRoleSel.innerHTML += customOpt;
  }

  // Doc creation author role
  populateRoleSelect(document.getElementById('doc-author-role'), HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});
  // Doc edit author role
  populateRoleSelect(document.getElementById('doc-edit-author-role'), HUMAN_ROLES, {empty: true, emptyLabel: 'No role'});

  // Ticket creation - assignee (agents only)
  const assigneeSel = document.getElementById('tk-form-assignee');
  if (assigneeSel) {
    assigneeSel.innerHTML = '<option value="">Unassigned</option>';
    agentNames.forEach(name => {
      assigneeSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Ticket creation - author (human roles + agents)
  const authorSel = document.getElementById('tk-form-author');
  if (authorSel) {
    populateRoleSelect(authorSel, HUMAN_ROLES, {default: 'Scenario Director'});
    agentNames.forEach(name => {
      authorSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Ticket detail - acting as (human roles + agents)
  const actingSel = document.getElementById('tk-acting-as');
  if (actingSel) {
    populateRoleSelect(actingSel, HUMAN_ROLES, {default: 'Scenario Director'});
    agentNames.forEach(name => {
      actingSel.innerHTML += '<option value="' + escapeHtml(name) + '">' + escapeHtml(name) + '</option>';
    });
  }

  // Hire modal - role (job titles)
  const hireSel = document.getElementById('hire-role-preset');
  if (hireSel) {
    hireSel.innerHTML = '<option value="">Role...</option>';
    JOB_TITLES.forEach(title => {
      hireSel.innerHTML += '<option value="' + escapeHtml(title) + '">' + escapeHtml(title) + '</option>';
    });
    hireSel.innerHTML += '<option value="other">Other...</option>';
  }

  // Email compose role
  const emailRoleSel = document.getElementById('email-compose-role');
  if (emailRoleSel) {
    populateRoleSelect(emailRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    emailRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Memo reply role
  const memoRoleSel = document.getElementById('memo-reply-role');
  if (memoRoleSel) {
    populateRoleSelect(memoRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    memoRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Memo create role
  const memoCreateRoleSel = document.getElementById('memo-create-role');
  if (memoCreateRoleSel) {
    populateRoleSelect(memoCreateRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    memoCreateRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Blog reply role
  const blogReplySel = document.getElementById('blog-reply-role');
  if (blogReplySel) {
    populateRoleSelect(blogReplySel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    blogReplySel.innerHTML += '<option value="custom">Custom...</option>';
  }

  // Blog create role
  const blogCreateRoleSel = document.getElementById('blog-create-role');
  if (blogCreateRoleSel) {
    populateRoleSelect(blogCreateRoleSel, HUMAN_ROLES, {empty: true, emptyLabel: 'No role', default: 'Scenario Director'});
    blogCreateRoleSel.innerHTML += '<option value="custom">Custom...</option>';
  }
}

function toggleCreateForm() {
  const form = document.getElementById('tk-create-form');
  form.classList.toggle('open');
  if (form.classList.contains('open')) {
    // Populate notify channel dropdown
    const notify = document.getElementById('tk-form-notify');
    notify.innerHTML = '<option value="">Don\\'t notify</option>';
    Object.keys(channelsData).sort().forEach(ch => {
      if (!channelsData[ch].is_system && !channelsData[ch].is_director) {
        notify.innerHTML += '<option value="' + escapeHtml(ch) + '">' + escapeHtml(ch) + '</option>';
      }
    });
    document.getElementById('tk-form-title').focus();
  }
}

async function submitCreateTicket() {
  const title = document.getElementById('tk-form-title').value.trim();
  if (!title) { document.getElementById('tk-form-title').focus(); return; }
  const priority = document.getElementById('tk-form-priority').value;
  const assignee = document.getElementById('tk-form-assignee').value;
  const description = document.getElementById('tk-form-desc').value.trim();
  const author = document.getElementById('tk-form-author').value;
  const notifyChannel = document.getElementById('tk-form-notify').value;
  const resp = await fetch('/api/tickets', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ title, description, priority, assignee, author }),
  });
  // Post notification to selected channel
  if (notifyChannel && resp.ok) {
    const ticket = await resp.json();
    let msg = 'New ticket **' + ticket.id + '**: ' + title;
    if (assignee) msg += ' (assigned to ' + assignee + ')';
    if (priority && priority !== 'medium') msg += ' [' + priority + ']';
    await fetch('/api/messages', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ sender: author || 'System', content: msg, channel: notifyChannel }),
    });
  }
  document.getElementById('tk-form-title').value = '';
  document.getElementById('tk-form-desc').value = '';
  document.getElementById('tk-form-priority').value = 'medium';
  document.getElementById('tk-form-assignee').value = '';
  document.getElementById('tk-form-notify').value = '';
  document.getElementById('tk-create-form').classList.remove('open');
  loadTickets();
}

function tkActingAs() {
  const sel = document.getElementById('tk-acting-as');
  return sel ? sel.value : 'Consultant';
}

async function tkUpdateStatus(ticketId, newStatus) {
  await fetch('/api/tickets/' + encodeURIComponent(ticketId), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ status: newStatus, author: tkActingAs() }),
  });
  loadTickets();
  viewTicket(ticketId);
}

async function tkAssign(ticketId) {
  const sel = document.getElementById('tk-assign-select');
  if (!sel) return;
  await fetch('/api/tickets/' + encodeURIComponent(ticketId), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ assignee: sel.value, author: tkActingAs() }),
  });
  loadTickets();
  viewTicket(ticketId);
}

async function tkAddComment(ticketId) {
  const input = document.getElementById('tk-comment-new');
  if (!input) return;
  const text = input.value.trim();
  if (!text) { input.focus(); return; }
  await fetch('/api/tickets/' + encodeURIComponent(ticketId) + '/comment', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ text, author: tkActingAs() }),
  });
  input.value = '';
  viewTicket(ticketId);
}

async function viewTicket(ticketId) {
  const resp = await fetch('/api/tickets/' + encodeURIComponent(ticketId));
  if (!resp.ok) return;
  const t = await resp.json();
  tkCurrentViewId = t.id;
  document.getElementById('ticket-detail-title').textContent = t.title;
  document.getElementById('ticket-detail-id').textContent = t.id;

  let html = '';

  // -- Action buttons --
  html += '<div class="tk-detail-actions">';
  if (t.status === 'open') {
    html += '<button class="tk-action-btn primary" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'in_progress\\')">Start Work</button>';
    html += '<button class="tk-action-btn success" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'resolved\\')">Resolve</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'in_progress') {
    html += '<button class="tk-action-btn success" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'resolved\\')">Resolve</button>';
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'resolved') {
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
    html += '<button class="tk-action-btn danger" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'closed\\')">Close</button>';
  } else if (t.status === 'closed') {
    html += '<button class="tk-action-btn" onclick="tkUpdateStatus(\\'' + t.id + '\\', \\'open\\')">Reopen</button>';
  }
  // Assign dropdown
  html += '<div class="tk-assign-row" style="margin-left:auto;">';
  html += '<select class="tk-assign-select" id="tk-assign-select">';
  TK_ASSIGNEE_OPTIONS.forEach(name => {
    const label = name || 'Unassigned';
    const sel = name === (t.assignee || '') ? ' selected' : '';
    html += '<option value="' + escapeHtml(name) + '"' + sel + '>' + escapeHtml(label) + '</option>';
  });
  html += '</select>';
  html += '<button class="tk-action-btn" onclick="tkAssign(\\'' + t.id + '\\')">Assign</button>';
  html += '</div>';
  html += '</div>';

  // -- Meta info --
  html += '<div class="tk-detail-meta">';
  html += '<span class="tk-detail-field"><strong>Status:</strong> <span class="tk-badge tk-status-' + t.status + '">' + escapeHtml(t.status) + '</span></span>';
  html += '<span class="tk-detail-field"><strong>Priority:</strong> <span class="tk-badge tk-priority-' + t.priority + '">' + escapeHtml(t.priority) + '</span></span>';
  html += '<span class="tk-detail-field"><strong>Assignee:</strong> ' + escapeHtml(t.assignee || 'Unassigned') + '</span>';
  html += '<span class="tk-detail-field"><strong>Created by:</strong> ' + escapeHtml(t.created_by) + '</span>';
  const created = new Date(t.created_at * 1000).toLocaleString();
  const updated = new Date(t.updated_at * 1000).toLocaleString();
  html += '<span class="tk-detail-field"><strong>Created:</strong> ' + created + '</span>';
  html += '<span class="tk-detail-field"><strong>Updated:</strong> ' + updated + '</span>';
  html += '</div>';

  if (t.description) {
    html += '<div class="tk-detail-desc">' + escapeHtml(t.description) + '</div>';
  }

  if (t.blocked_by && t.blocked_by.length > 0) {
    html += '<div class="tk-detail-deps"><strong>Blocked by:</strong> ';
    html += t.blocked_by.map(id => '<span onclick="viewTicket(\\'' + escapeHtml(id) + '\\')">' + escapeHtml(id) + '</span>').join(', ');
    html += '</div>';
  }
  if (t.blocks && t.blocks.length > 0) {
    html += '<div class="tk-detail-deps"><strong>Blocks:</strong> ';
    html += t.blocks.map(id => '<span onclick="viewTicket(\\'' + escapeHtml(id) + '\\')">' + escapeHtml(id) + '</span>').join(', ');
    html += '</div>';
  }

  // -- Comments --
  const comments = t.comments || [];
  html += '<div class="tk-comments-header">Comments (' + comments.length + ')</div>';
  comments.forEach(c => {
    const ctime = new Date(c.timestamp * 1000).toLocaleString();
    html += '<div class="tk-comment">'
      + '<span class="tk-comment-author">' + escapeHtml(c.author) + '</span>'
      + '<span class="tk-comment-time">' + ctime + '</span>'
      + '<div class="tk-comment-text">' + escapeHtml(c.text) + '</div></div>';
  });

  // -- Comment input --
  html += '<div class="tk-comment-input-area">';
  html += '<textarea class="tk-comment-input" id="tk-comment-new" placeholder="Add a comment..."></textarea>';
  html += '<button class="tk-comment-submit" onclick="tkAddComment(\\'' + t.id + '\\')">Comment</button>';
  html += '</div>';

  document.getElementById('ticket-detail-content').innerHTML = html;
  document.getElementById('ticket-detail').classList.add('open');
  document.getElementById('tickets-list').style.display = 'none';
}

document.getElementById('ticket-back-btn').addEventListener('click', () => {
  document.getElementById('ticket-detail').classList.remove('open');
  document.getElementById('tickets-list').style.display = '';
  tkCurrentViewId = null;
});

// -- Init --
loadPersonas().then(() => {
  loadRoles().then(() => {
    loadChannels().then(() => {
      updateChannelHeader();
      updateSenderDropdown();
      loadMessages();
      connectSSE();
    });
  });
});
loadFolders();
loadRepos();
loadTickets();

// -- NPCs tab --

const TIER_LABELS = {1: 'Tier 1 — ICs', 2: 'Tier 2 — Managers', 3: 'Tier 3 — Executives'};

async function loadNPCs() {
  const container = document.getElementById('npcs-content');
  const empty = document.getElementById('npcs-empty');
  const resp = await fetch('/api/npcs');
  const npcs = await resp.json();
  // Update sidebar summary
  const summaryEl = document.getElementById('npcs-summary');
  const scenarioEl = document.getElementById('npcs-scenario-info');
  if (npcs.length === 0) {
    container.innerHTML = '';
    container.appendChild(empty);
    empty.style.display = 'block';
    summaryEl.textContent = '';
    scenarioEl.textContent = 'No scenario loaded';
    return;
  }
  const readyCount = npcs.filter(n => n.live_state === 'ready').length;
  const startingCount = npcs.filter(n => n.live_state === 'starting').length;
  const respondingCount = npcs.filter(n => n.live_state === 'responding').length;
  const oooCount = npcs.filter(n => !n.online).length;
  const disconnectedCount = npcs.filter(n => n.online && n.live_state === 'disconnected').length;
  scenarioEl.innerHTML = '<strong>' + npcs.length + ' agents</strong>';
  let summaryHtml = '';
  if (readyCount > 0) summaryHtml += '<div style="color:#2ecc71">Ready: ' + readyCount + '</div>';
  if (respondingCount > 0) summaryHtml += '<div style="color:#3498db">Responding: ' + respondingCount + '</div>';
  if (startingCount > 0) summaryHtml += '<div style="color:#f39c12">Starting: ' + startingCount + '</div>';
  if (oooCount > 0) summaryHtml += '<div style="color:var(--text-dim)">Out of office: ' + oooCount + '</div>';
  if (disconnectedCount > 0) summaryHtml += '<div style="color:var(--text-dimmer)">Disconnected: ' + disconnectedCount + '</div>';
  if (!summaryHtml) summaryHtml = '<div style="color:var(--text-dimmer)">No agents active</div>';
  summaryEl.innerHTML = summaryHtml;
  // Group by tier
  const tiers = {};
  npcs.forEach(npc => {
    const t = npc.tier || 0;
    if (!tiers[t]) tiers[t] = [];
    tiers[t].push(npc);
  });
  container.innerHTML = '';
  Object.keys(tiers).sort().forEach(tierNum => {
    const section = document.createElement('div');
    section.className = 'npc-tier-section';
    const header = document.createElement('div');
    header.className = 'npc-tier-header';
    header.textContent = TIER_LABELS[tierNum] || ('Tier ' + tierNum);
    section.appendChild(header);
    const grid = document.createElement('div');
    grid.className = 'npc-tier-grid';
    tiers[tierNum].forEach(npc => {
      grid.appendChild(createNPCCard(npc));
    });
    section.appendChild(grid);
    container.appendChild(section);
  });
}

const LIVE_STATE_LABELS = {
  ready: 'Ready', starting: 'Starting...', responding: 'Thinking...',
  'writing docs': 'Writing docs...', 'committing code': 'Committing code...',
  'managing tickets': 'Managing tickets...', 'processing commands': 'Processing...',
  'posting': 'Posting...', firing: 'Being Fired...', offline: 'Out of Office', disconnected: 'Disconnected',
  unknown: 'Unknown',
};

function createNPCCard(npc) {
  const card = document.createElement('div');
  const ls = npc.live_state || 'unknown';
  const lsCss = ls.replace(/ /g, '-');
  card.className = 'npc-card' + (npc.online ? '' : ' offline');
  // Build NPC avatar for card header
  let npcAvatarHtml = '';
  const npa = PERSONA_AVATARS[npc.display_name];
  if (npa) {
    if (npa.avatar) {
      npcAvatarHtml = '<div class="msg-avatar" style="width:24px;height:24px;font-size:11px"><img src="' + escapeHtml(npa.avatar) + '" alt="" style="width:24px;height:24px;border-radius:6px"></div>';
    } else {
      npcAvatarHtml = '<div class="msg-avatar" style="width:24px;height:24px;font-size:11px;background:' + npa.color + '">' + npa.initial + '</div>';
    }
  }
  card.innerHTML =
    '<div class="npc-card-header">' +
      npcAvatarHtml +
      '<span class="npc-status-dot ' + lsCss + '"></span>' +
      '<span class="npc-card-name">' + escapeHtml(npc.display_name) + '</span>' +
      '<span class="npc-card-state">' + (LIVE_STATE_LABELS[ls] || ls) + '</span>' +
    '</div>' +
    '<div class="npc-card-desc">' + escapeHtml(npc.team_description) + '</div>' +
    '<div class="npc-card-section-label">Channels</div>' +
    '<div class="npc-card-tags">' +
      npc.channels.map(ch => '<span class="npc-tag">' + escapeHtml(ch) + '</span>').join('') +
    '</div>' +
    '<div class="npc-card-section-label">Doc Folders</div>' +
    '<div class="npc-card-tags">' +
      (npc.folders || []).map(f => '<span class="npc-tag npc-tag-folder">' + escapeHtml(f) + '</span>').join('') +
    '</div>' +
    ((npc.repos || []).length ? '<div class="npc-card-section-label">GitLab Repos</div>' +
    '<div class="npc-card-tags">' +
      npc.repos.map(r => '<span class="npc-tag" style="border-left:2px solid #e67e22">' + escapeHtml(r) + '</span>').join('') +
    '</div>' : '');
  const btn = document.createElement('button');
  btn.className = 'npc-toggle-btn' + (npc.online ? ' is-online' : '');
  btn.textContent = npc.online ? 'Set Out of Office' : 'Bring Online';
  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    await fetch('/api/npcs/' + encodeURIComponent(npc.key) + '/toggle', {method: 'POST'});
    loadNPCs();
  });
  card.appendChild(btn);
  const fireBtn = document.createElement('button');
  fireBtn.className = 'npc-toggle-btn';
  fireBtn.style.cssText = 'margin-top:4px;font-size:10px;color:var(--text-dimmer)';
  fireBtn.textContent = 'Fire';
  fireBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm('Fire ' + npc.display_name + '? Their session will be closed and they will stop responding. Documents and tickets are preserved.')) return;
    await fetch('/api/npcs/' + encodeURIComponent(npc.key) + '/fire', {method: 'POST'});
    await loadPersonas();
    await loadChannels();
    loadNPCs();
  });
  card.appendChild(fireBtn);
  card.style.cursor = 'pointer';
  card.addEventListener('click', (e) => {
    if (e.target === btn || e.target === fireBtn) return;
    openNPCDetail(npc.key, npc.display_name);
  });
  return card;
}

// -- NPC detail modal --

let _npcDetailKey = null;
let _npcDetailTab = 'thoughts';
let _npcThoughtsData = [];

async function openNPCDetail(key, displayName) {
  _npcDetailKey = key;
  _npcDetailTab = 'thoughts';
  document.getElementById('npc-detail-title').textContent = displayName;
  document.querySelectorAll('.npc-detail-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.npcTab === 'thoughts');
  });
  switchNPCDetailTab('thoughts');
  await loadNPCThoughts();
  openModal('npc-detail-modal');
}

function switchNPCDetailTab(tab) {
  _npcDetailTab = tab;
  document.getElementById('npc-detail-thoughts').style.display = tab === 'thoughts' ? 'flex' : 'none';
  document.getElementById('npc-detail-character').style.display = tab === 'character' ? '' : 'none';
  document.getElementById('npc-detail-prompt').style.display = tab === 'prompt' ? '' : 'none';
  document.getElementById('npc-detail-config').style.display = tab === 'config' ? '' : 'none';
}

async function loadNPCThoughts() {
  const content = document.getElementById('npc-thoughts-content');
  content.textContent = 'Loading...';
  document.getElementById('npc-thoughts-search').value = '';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/thoughts');
  _npcThoughtsData = await resp.json();
  if (!_npcThoughtsData.length) {
    document.getElementById('npc-thoughts-list').innerHTML = '';
    content.textContent = 'No thoughts recorded yet. This agent has not responded to any messages.';
    return;
  }
  renderThoughtsList();
}

function renderThoughtsList(filter) {
  const list = document.getElementById('npc-thoughts-list');
  const content = document.getElementById('npc-thoughts-content');
  list.innerHTML = '';
  const filterLower = (filter || '').toLowerCase();
  const reversed = [..._npcThoughtsData].reverse();
  let firstIdx = null;
  reversed.forEach((t, i) => {
    const idx = _npcThoughtsData.length - 1 - i;
    // Filter by search term
    if (filterLower) {
      const text = ((t.thinking || '') + ' ' + (t.response || '')).toLowerCase();
      if (!text.includes(filterLower)) return;
    }
    if (firstIdx === null) firstIdx = idx;
    const item = document.createElement('div');
    item.className = 'thought-item';
    item.dataset.idx = idx;
    const ts = new Date(t.timestamp * 1000);
    const timeStr = ts.toLocaleTimeString();
    const dateStr = ts.toLocaleDateString();
    const preview = (t.thinking || t.response || '').substring(0, 60).replace(/[\\n\\r]+/g, ' ');
    item.innerHTML = '<div class="thought-item-time">' + dateStr + ' ' + timeStr + '</div>' +
      '<div class="thought-item-preview">' + escapeHtml(preview) + '</div>';
    item.addEventListener('click', () => selectThought(idx));
    list.appendChild(item);
  });
  if (firstIdx !== null) {
    selectThought(firstIdx);
  } else {
    content.textContent = filter ? 'No thoughts matching "' + filter + '"' : 'No thoughts recorded yet.';
  }
}

document.getElementById('npc-thoughts-search').addEventListener('input', (e) => {
  renderThoughtsList(e.target.value.trim());
});

function selectThought(idx) {
  const content = document.getElementById('npc-thoughts-content');
  const t = _npcThoughtsData[idx];
  if (!t) return;
  // Update active state in list
  document.querySelectorAll('.thought-item').forEach(el => {
    el.classList.toggle('active', parseInt(el.dataset.idx) === idx);
  });
  const ts = new Date(t.timestamp * 1000).toLocaleString();
  let text = '=== Internal Thinking ===  ' + ts + '\\n\\n';
  text += t.thinking || '(no thinking captured)';
  text += '\\n\\n=== Response ===\\n\\n';
  text += t.response || '(no response)';
  content.textContent = text;
}

async function loadNPCPrompt() {
  const body = document.getElementById('npc-detail-prompt');
  body.innerHTML = '<span style="color:var(--text-dimmer)">Loading...</span>';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/prompt');
  const data = await resp.json();
  if (data.error) { body.textContent = data.error; return; }
  let html = '';
  if (data.context) {
    html += '<div style="margin-bottom:12px"><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--highlight);margin-bottom:8px">Character Context</div>';
    html += '<div style="white-space:pre-wrap">' + escapeHtml(data.context) + '</div></div>';
    html += '<div style="border-top:2px solid var(--accent);margin:16px 0;position:relative"><span style="position:absolute;top:-10px;left:12px;background:var(--input-bg);padding:0 8px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:var(--accent)">Simulation Directives</span></div>';
  }
  html += '<div style="white-space:pre-wrap;margin-top:' + (data.context ? '16px' : '0') + '">' + escapeHtml(data.prompt) + '</div>';
  body.innerHTML = html;
}

async function loadNPCCharacter() {
  const meta = document.getElementById('npc-cs-meta');
  const sections = document.getElementById('npc-cs-sections');
  meta.innerHTML = 'Loading...';
  sections.innerHTML = '';
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/character-sheet');
  const data = await resp.json();
  if (data.error) { meta.textContent = data.error; return; }

  // Render YAML frontmatter metadata
  const fm = data.frontmatter || {};
  let metaHtml = '';
  if (fm.Name) metaHtml += '<div style="font-size:20px;font-weight:700;color:var(--text);margin-bottom:4px">' + escapeHtml(fm.Name) + '</div>';
  const badges = [];
  if (fm.Type) badges.push('<span style="background:var(--border-dark);color:var(--text-dim);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.Type) + '</span>');
  if (fm.Status) badges.push('<span style="background:#2ecc71;color:var(--text-bright);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.Status) + '</span>');
  if (fm.System) badges.push('<span style="background:var(--border);color:var(--highlight);padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600">' + escapeHtml(fm.System) + '</span>');
  if (badges.length) metaHtml += '<div style="display:flex;gap:6px;margin-bottom:8px">' + badges.join('') + '</div>';
  if (fm.Tags && fm.Tags.length) {
    metaHtml += '<div style="margin-bottom:8px">';
    fm.Tags.forEach(function(tag) {
      metaHtml += '<span style="background:var(--input-bg);color:var(--text-dim);padding:1px 6px;border-radius:4px;font-size:11px;margin-right:4px;border:1px solid var(--border-dark)">' + escapeHtml(tag) + '</span>';
    });
    metaHtml += '</div>';
  }
  meta.innerHTML = metaHtml || '<div style="color:var(--text-dimmer)">No NRSP metadata (legacy format)</div>';

  // Render sections (exclude ## Prompt — that's on the Prompt tab)
  let sectionsHtml = '';
  (data.sections || []).forEach(function(sec) {
    if (sec.title.toLowerCase() === 'prompt') return;
    sectionsHtml += '<div style="margin-bottom:16px">';
    sectionsHtml += '<h3 style="color:var(--highlight);font-size:14px;margin-bottom:6px;border-bottom:1px solid var(--border-dark);padding-bottom:4px">' + escapeHtml(sec.title) + '</h3>';
    sectionsHtml += '<div style="white-space:pre-wrap;color:var(--text);font-size:13px;line-height:1.5">' + escapeHtml(sec.content) + '</div>';
    sectionsHtml += '</div>';
  });
  sections.innerHTML = sectionsHtml || '<div style="color:var(--text-dimmer)">No structured character sections found.</div>';
}

document.querySelectorAll('.npc-detail-tab').forEach(tab => {
  tab.addEventListener('click', async () => {
    _npcDetailTab = tab.dataset.npcTab;
    document.querySelectorAll('.npc-detail-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.npcTab === _npcDetailTab);
    });
    switchNPCDetailTab(_npcDetailTab);
    if (_npcDetailTab === 'thoughts') await loadNPCThoughts();
    else if (_npcDetailTab === 'character') await loadNPCCharacter();
    else if (_npcDetailTab === 'prompt') await loadNPCPrompt();
    else if (_npcDetailTab === 'config') await loadNPCConfig();
  });
});

document.getElementById('npc-detail-close').addEventListener('click', () => {
  closeModal('npc-detail-modal');
});

// -- Hire modal --

document.getElementById('hire-role-preset').addEventListener('change', (e) => {
  const custom = document.getElementById('hire-role-custom');
  if (e.target.value === 'other') {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = '';
  }
});

function getHireDisplayName() {
  const name = document.getElementById('hire-name').value.trim();
  const rolePreset = document.getElementById('hire-role-preset').value;
  const roleCustom = document.getElementById('hire-role-custom').value.trim();
  const role = rolePreset === 'other' ? roleCustom : rolePreset;
  if (!name) return '';
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('hire-template').addEventListener('change', async (e) => {
  const key = e.target.value;
  if (!key) return;
  const resp = await fetch('/api/templates/' + encodeURIComponent(key));
  if (resp.ok) {
    const data = await resp.json();
    const name = document.getElementById('hire-name').value.trim() || 'NAME';
    document.getElementById('hire-prompt').value = data.content.replace(/{NAME}/g, name);
  }
});

// Re-apply name to template when name changes
document.getElementById('hire-name').addEventListener('input', () => {
  const templateKey = document.getElementById('hire-template').value;
  if (templateKey && document.getElementById('hire-prompt').value.includes('{NAME}')) {
    // Template hasn't been manually edited yet, nothing to do
  }
});

document.getElementById('npc-hire-btn').addEventListener('click', async () => {
  document.getElementById('hire-name').value = '';
  document.getElementById('hire-role-preset').value = '';
  document.getElementById('hire-role-custom').value = '';
  document.getElementById('hire-role-custom').style.display = 'none';
  document.getElementById('hire-key').value = '';
  document.getElementById('hire-key').dataset.manual = '';
  document.getElementById('hire-team-desc').value = '';
  document.getElementById('hire-tier').value = '1';
  document.getElementById('hire-verbosity').value = 'normal';
  document.getElementById('hire-prompt').value = '';

  // Populate template dropdown
  const templateSel = document.getElementById('hire-template');
  templateSel.innerHTML = '<option value="">Start from scratch</option>';
  const resp = await fetch('/api/templates');
  const templates = await resp.json();
  templates.forEach(t => {
    const opt = document.createElement('option');
    opt.value = t.key;
    opt.textContent = t.name;
    templateSel.appendChild(opt);
  });

  // Populate channel checkboxes
  const chContainer = document.getElementById('hire-channels');
  chContainer.innerHTML = '';
  Object.keys(channelsData).sort().forEach(ch => {
    if (channelsData[ch].is_system || channelsData[ch].is_director) return;
    const checked = ch === '#general';
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(ch) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(ch);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    chContainer.appendChild(label);
  });

  // Populate folder checkboxes
  const flContainer = document.getElementById('hire-folders');
  flContainer.innerHTML = '';
  foldersData.forEach(f => {
    const checked = f.name === 'shared' || f.name === 'public';
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(f.name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(f.name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    flContainer.appendChild(label);
  });

  openModal('hire-modal');
  document.getElementById('hire-name').focus();
});

// Auto-generate key from name
document.getElementById('hire-name').addEventListener('input', () => {
  const keyField = document.getElementById('hire-key');
  if (!keyField.dataset.manual) {
    keyField.value = document.getElementById('hire-name').value.trim().toLowerCase().replace(/[^a-z0-9]/g, '');
  }
});
document.getElementById('hire-key').addEventListener('input', () => {
  document.getElementById('hire-key').dataset.manual = '1';
});

document.getElementById('hire-modal-close').addEventListener('click', () => closeModal('hire-modal'));

document.getElementById('hire-confirm').addEventListener('click', async () => {
  const display_name = getHireDisplayName();
  const key = document.getElementById('hire-key').value.trim();
  const team_description = document.getElementById('hire-team-desc').value.trim();
  const tier = parseInt(document.getElementById('hire-tier').value);
  const prompt = document.getElementById('hire-prompt').value;

  if (!display_name) { alert('Display name is required'); return; }

  const channels = [];
  document.querySelectorAll('#hire-channels input:checked').forEach(cb => channels.push(cb.value));
  const folders = [];
  document.querySelectorAll('#hire-folders input:checked').forEach(cb => folders.push(cb.value));

  const resp = await fetch('/api/npcs/hire', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({display_name, key: key || undefined, team_description, tier, channels, folders, prompt, verbosity: document.getElementById('hire-verbosity').value}),
  });
  if (resp.ok) {
    closeModal('hire-modal');
    await loadPersonas();
    await loadChannels();
    loadFolders();
    loadNPCs();
  } else {
    const err = await resp.json();
    alert('Error: ' + (err.error || 'unknown'));
  }
});

// -- NPC Config tab --

async function loadNPCConfig() {
  if (!_npcDetailKey) return;
  // Get current NPC data
  const resp = await fetch('/api/npcs');
  const npcs = await resp.json();
  const npc = npcs.find(n => n.key === _npcDetailKey);
  if (!npc) return;

  // Tier and verbosity dropdowns
  document.getElementById('npc-config-tier').value = npc.tier || 1;
  document.getElementById('npc-config-verbosity').value = npc.verbosity || 'normal';

  // Channel checkboxes
  const chContainer = document.getElementById('npc-config-channels');
  chContainer.innerHTML = '';
  const currentChannels = new Set(npc.channels || []);
  Object.keys(channelsData).sort().forEach(ch => {
    if (channelsData[ch].is_system || channelsData[ch].is_director) return;
    const checked = currentChannels.has(ch);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(ch) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(ch);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    chContainer.appendChild(label);
  });

  // Folder checkboxes
  const flContainer = document.getElementById('npc-config-folders');
  flContainer.innerHTML = '';
  const currentFolders = new Set(npc.folders || []);
  foldersData.forEach(f => {
    const checked = currentFolders.has(f.name);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(f.name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(f.name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    flContainer.appendChild(label);
  });

  // Repo checkboxes
  const repoContainer = document.getElementById('npc-config-repos');
  repoContainer.innerHTML = '';
  const currentRepos = new Set(npc.repos || []);
  const allRepos = Object.keys(glRepos || {}).length ? glRepos.map(r => r.name).sort() : [];
  if (allRepos.length === 0) {
    repoContainer.innerHTML = '<span style="font-size:11px;color:var(--text-dimmer)">No repositories yet</span>';
  }
  allRepos.forEach(name => {
    const checked = currentRepos.has(name);
    const label = document.createElement('label');
    label.className = 'npc-config-check' + (checked ? ' checked' : '');
    label.innerHTML = '<input type="checkbox" value="' + escapeHtml(name) + '"' + (checked ? ' checked' : '') + '> ' + escapeHtml(name);
    label.querySelector('input').addEventListener('change', (e) => {
      label.classList.toggle('checked', e.target.checked);
    });
    repoContainer.appendChild(label);
  });
}

document.getElementById('npc-config-save').addEventListener('click', async () => {
  if (!_npcDetailKey) return;
  const tier = parseInt(document.getElementById('npc-config-tier').value);
  const channels = [];
  document.querySelectorAll('#npc-config-channels input:checked').forEach(cb => {
    channels.push(cb.value);
  });
  const folders = [];
  document.querySelectorAll('#npc-config-folders input:checked').forEach(cb => {
    folders.push(cb.value);
  });
  const repos = [];
  document.querySelectorAll('#npc-config-repos input:checked').forEach(cb => {
    repos.push(cb.value);
  });
  const resp = await fetch('/api/npcs/' + encodeURIComponent(_npcDetailKey) + '/config', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({tier, channels, folders, repos, verbosity: document.getElementById('npc-config-verbosity').value}),
  });
  if (resp.ok) {
    loadNPCs();
    loadChannels();
  }
});

// -- Usage tab --

function formatTokenCount(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toString();
}

function formatCost(usd) {
  if (usd >= 1) return '$' + usd.toFixed(2);
  if (usd >= 0.01) return '$' + usd.toFixed(3);
  if (usd > 0) return '$' + usd.toFixed(4);
  return '$0.00';
}

let _lastUsageData = null;
async function loadUsage() {
  try {
    const resp = await fetch('/api/usage');
    if (!resp.ok) return;  // keep previous data on error
    const data = await resp.json();
    if (!data || !data.totals) return;  // keep previous data on bad response

    // Merge with previous data — keep highest values per agent to avoid flicker
    // from partial log reads during active writes
    if (_lastUsageData && data.agents) {
      const prevByName = {};
      (_lastUsageData.agents || []).forEach(a => { prevByName[a.name] = a; });
      data.agents.forEach(a => {
        const prev = prevByName[a.name];
        if (prev) {
          a.api_calls = Math.max(a.api_calls, prev.api_calls);
          a.input_tokens = Math.max(a.input_tokens, prev.input_tokens);
          a.output_tokens = Math.max(a.output_tokens, prev.output_tokens);
          a.total_cost_usd = Math.max(a.total_cost_usd, prev.total_cost_usd);
          delete prevByName[a.name];
        }
      });
      // Keep agents that were in previous data but missing from current parse
      Object.values(prevByName).forEach(a => { data.agents.push(a); });
      data.agents.sort((a, b) => b.total_cost_usd - a.total_cost_usd);
      // Recompute totals from merged agents
      data.totals.api_calls = data.agents.reduce((s, a) => s + a.api_calls, 0);
      data.totals.input_tokens = data.agents.reduce((s, a) => s + a.input_tokens, 0);
      data.totals.output_tokens = data.agents.reduce((s, a) => s + a.output_tokens, 0);
      data.totals.total_cost_usd = data.agents.reduce((s, a) => s + a.total_cost_usd, 0);
    }
    _lastUsageData = data;

    const totals = data.totals;
    const agents = data.agents;

    // Update sidebar totals
    const totalsEl = document.getElementById('usage-totals');
    totalsEl.innerHTML =
      '<div class="usage-stat"><span class="label">API Calls:</span> <strong>' + totals.api_calls + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Input:</span> <strong>' + formatTokenCount(totals.input_tokens) + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Output:</span> <strong>' + formatTokenCount(totals.output_tokens) + '</strong></div>' +
      '<div class="usage-stat"><span class="label">Cost:</span> <strong style="color:#2ecc71">' + formatCost(totals.total_cost_usd) + '</strong></div>';

    // Update main content
    const container = document.getElementById('usage-content');
    const emptyEl = document.getElementById('usage-empty');

    if (!agents || agents.length === 0) {
      container.innerHTML = '';
      container.appendChild(emptyEl);
      emptyEl.style.display = 'block';
      return;
    }

    container.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'usage-grid';

    agents.forEach(agent => {
      const card = document.createElement('div');
      card.className = 'usage-card';
      card.innerHTML =
        '<div class="usage-card-name">' + escapeHtml(agent.name) + '</div>' +
        '<div class="usage-card-row"><span class="label">Input tokens</span><span class="value">' + formatTokenCount(agent.input_tokens) + '</span></div>' +
        '<div class="usage-card-row"><span class="label">Output tokens</span><span class="value">' + formatTokenCount(agent.output_tokens) + '</span></div>' +
        '<div class="usage-card-row"><span class="label">API calls</span><span class="value">' + agent.api_calls + '</span></div>' +
        '<div class="usage-card-row"><span class="label">Cost</span><span class="value cost">' + formatCost(agent.total_cost_usd) + '</span></div>';
      grid.appendChild(card);
    });

    container.appendChild(grid);
  } catch(e) {
    // keep previous data on fetch errors
  }
}

// -- Advanced tab --

document.getElementById('clear-chat-btn').addEventListener('click', async () => {
  if (!confirm('Clear all chat messages? This cannot be undone.')) return;
  await fetch('/api/messages/clear', {method: 'POST'});
  messagesByChannel = {};
  seenIds.clear();
  unreadByChannel = {};
  renderSidebar();
  renderMessages();
  showNotice('Chat history cleared.');
});

document.getElementById('clear-all-btn').addEventListener('click', async () => {
  if (!confirm('Clear EVERYTHING? Messages, docs, repos, tickets, events, emails, recaps — all gone. This cannot be undone.')) return;
  if (!confirm('Are you REALLY sure? Save first if you need anything.')) return;
  const scenario = (await (await fetch('/api/session/current')).json()).scenario;
  if (scenario) {
    await fetch('/api/session/new', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scenario}),
    });
    await reloadAllState();
    showNotice('Everything cleared. Fresh start.');
  }
});

// -- Session Manager (Advanced tab) --

function _fmtSessionDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const mon = d.toLocaleString('en-US', {month: 'short'});
  const day = d.getDate();
  const h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, '0');
  const ampm = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 || 12;
  return `${mon} ${day}, ${h12}:${m} ${ampm}`;
}

let _smSessions = [];
let _smSortCol = 'saved_at';
let _smSortAsc = false;

function _smUpdateSortArrows() {
  document.querySelectorAll('#session-manager-table th[data-sm-sort]').forEach(th => {
    const arrow = th.querySelector('.sm-sort-arrow');
    if (th.dataset.smSort === _smSortCol) {
      arrow.textContent = _smSortAsc ? ' \\u25B2' : ' \\u25BC';
      th.style.color = 'var(--text)';
    } else {
      arrow.textContent = '';
      th.style.color = 'var(--text-dim)';
    }
  });
}

function _smRenderRows() {
  const tbody = document.getElementById('session-manager-body');
  if (_smSessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">No saved sessions</td></tr>';
    return;
  }
  const sorted = [..._smSessions];
  sorted.sort((a, b) => {
    let va, vb;
    if (_smSortCol === 'name') {
      va = (a.name || a.instance_dir).toLowerCase();
      vb = (b.name || b.instance_dir).toLowerCase();
    } else if (_smSortCol === 'scenario') {
      va = (a.scenario || '').toLowerCase();
      vb = (b.scenario || '').toLowerCase();
    } else {
      va = a[_smSortCol] || 0;
      vb = b[_smSortCol] || 0;
    }
    if (va < vb) return _smSortAsc ? -1 : 1;
    if (va > vb) return _smSortAsc ? 1 : -1;
    return 0;
  });
  tbody.innerHTML = '';
  sorted.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.borderBottom = '1px solid var(--border-dark)';
    tr.dataset.instance = s.instance_dir;
    const nameTd = document.createElement('td');
    nameTd.style.cssText = 'padding:8px 10px;color:var(--text)';
    nameTd.innerHTML = '<span class="sm-name-display">' + escapeHtml(s.name || s.instance_dir) + '</span>'
      + '<input class="sm-name-input" type="text" style="display:none;width:100%;background:var(--input-bg);color:var(--text);border:1px solid var(--accent);padding:4px 6px;border-radius:4px;font-size:13px" />';
    const scenarioTd = document.createElement('td');
    scenarioTd.style.cssText = 'padding:8px 10px;color:var(--text-dim)';
    scenarioTd.textContent = s.scenario || '—';
    const createdTd = document.createElement('td');
    createdTd.style.cssText = 'padding:8px 10px;color:var(--text-dim);white-space:nowrap';
    createdTd.textContent = _fmtSessionDate(s.created_at);
    const savedTd = document.createElement('td');
    savedTd.style.cssText = 'padding:8px 10px;color:var(--text-dim);white-space:nowrap';
    savedTd.textContent = _fmtSessionDate(s.saved_at);
    const actionsTd = document.createElement('td');
    actionsTd.style.cssText = 'padding:8px 10px;text-align:right;white-space:nowrap';
    const loadBtn = document.createElement('button');
    loadBtn.className = 'session-btn';
    loadBtn.textContent = 'Load';
    loadBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px';
    loadBtn.addEventListener('click', () => _smLoad(s.instance_dir));
    const renameBtn = document.createElement('button');
    renameBtn.className = 'session-btn';
    renameBtn.textContent = 'Rename';
    renameBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px';
    renameBtn.addEventListener('click', () => _smStartRename(tr, s));
    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'session-btn';
    deleteBtn.textContent = 'Delete';
    deleteBtn.style.cssText = 'font-size:11px;padding:3px 10px;margin-left:4px;border-color:var(--accent);color:var(--accent)';
    deleteBtn.addEventListener('click', () => _smDelete(s.instance_dir, s.name || s.instance_dir));
    actionsTd.appendChild(loadBtn);
    actionsTd.appendChild(renameBtn);
    actionsTd.appendChild(deleteBtn);
    tr.appendChild(nameTd);
    tr.appendChild(scenarioTd);
    tr.appendChild(createdTd);
    tr.appendChild(savedTd);
    tr.appendChild(actionsTd);
    tbody.appendChild(tr);
  });
  _smUpdateSortArrows();
}

document.querySelectorAll('#session-manager-table th[data-sm-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.smSort;
    if (_smSortCol === col) {
      _smSortAsc = !_smSortAsc;
    } else {
      _smSortCol = col;
      _smSortAsc = (col === 'name' || col === 'scenario');
    }
    _smRenderRows();
  });
});

async function loadSessionManagerTable() {
  const tbody = document.getElementById('session-manager-body');
  tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>';
  try {
    const resp = await fetch('/api/session/list');
    _smSessions = await resp.json();
    _smRenderRows();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" style="padding:16px 10px;color:var(--accent);text-align:center">Failed to load sessions</td></tr>';
  }
}

async function _smLoad(instance) {
  showLoading('Loading session...');
  try {
    const resp = await fetch('/api/session/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instance}),
    });
    if (resp.ok) {
      await reloadAllState();
      showNotice('Session loaded.');
    } else {
      const err = await resp.json();
      hideLoading();
      showNotice('Error: ' + (err.error || 'unknown'));
    }
  } finally {
    hideLoading();
  }
}

function _smStartRename(tr, session) {
  const display = tr.querySelector('.sm-name-display');
  const input = tr.querySelector('.sm-name-input');
  display.style.display = 'none';
  input.style.display = '';
  input.value = session.name || session.instance_dir;
  input.focus();
  input.select();
  const finish = async () => {
    input.removeEventListener('blur', finish);
    input.removeEventListener('keydown', onKey);
    const newName = input.value.trim();
    if (!newName || newName === (session.name || session.instance_dir)) {
      display.style.display = '';
      input.style.display = 'none';
      return;
    }
    try {
      const resp = await fetch('/api/session/' + encodeURIComponent(session.instance_dir), {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: newName}),
      });
      if (resp.ok) {
        session.name = newName;
        display.textContent = newName;
      }
    } catch(e) {}
    display.style.display = '';
    input.style.display = 'none';
  };
  const onKey = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); finish(); }
    if (e.key === 'Escape') { input.value = session.name || session.instance_dir; finish(); }
  };
  input.addEventListener('blur', finish);
  input.addEventListener('keydown', onKey);
}

async function _smDelete(instance, displayName) {
  if (!confirm('Delete session "' + displayName + '"? This cannot be undone.')) return;
  try {
    const resp = await fetch('/api/session/' + encodeURIComponent(instance), {method: 'DELETE'});
    if (resp.ok) {
      _smSessions = _smSessions.filter(s => s.instance_dir !== instance);
      _smRenderRows();
      showNotice('Session deleted.');
    } else {
      const err = await resp.json();
      showNotice('Error: ' + (err.error || 'unknown'));
    }
  } catch(e) {
    showNotice('Delete failed.');
  }
}

// -- Recap tab --

const STYLE_LABELS = {
  normal: 'Normal', 'ye-olde-english': 'Ye Olde English', tolkien: 'Tolkien Fantasy',
  'star-wars': 'Star Wars', 'star-trek': 'Star Trek', 'dr-who': 'Doctor Who',
  'morse-code': 'Telegraph', 'dr-seuss': 'Dr. Seuss', shakespeare: 'Shakespeare',
  '80s-rock-ballad': '80s Rock Ballad', '90s-alternative': '90s Alternative',
  'heavy-metal': 'Heavy Metal', dystopian: 'Dystopian', matrix: 'The Matrix',
  pharaoh: "Pharaoh's Decree", tombstone: 'Tombstone Western',
  survivor: 'Survivor Tribal Council', hackernews: 'HackerNews Blog',
};

document.getElementById('recap-generate-btn').addEventListener('click', async () => {
  const style = document.getElementById('recap-style').value;
  const content = document.getElementById('recap-content');
  const btn = document.getElementById('recap-generate-btn');
  content.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:60px"><div class="spinner" style="margin:0 auto 12px"></div>Generating ' + (STYLE_LABELS[style] || style) + ' recap...</div>';
  btn.disabled = true;
  btn.textContent = 'Generating...';
  try {
    const resp = await fetch('/api/recap', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({style}),
    });
    const data = await resp.json();
    if (data.recap) {
      content.textContent = data.recap;
      renderRecapList();
    } else {
      content.textContent = 'Error: ' + (data.error || 'unknown');
    }
  } catch(e) {
    content.textContent = 'Error: ' + e.message;
  }
  btn.disabled = false;
  btn.textContent = 'Generate Recap';
});

async function renderRecapList() {
  const list = document.getElementById('recap-list');
  list.innerHTML = '';
  const resp = await fetch('/api/recaps');
  const recaps = await resp.json();
  [...recaps].reverse().forEach((r) => {
    const item = document.createElement('div');
    item.className = 'recap-item';
    const ts = new Date(r.timestamp * 1000);
    item.innerHTML = '<div class="recap-item-style">' + escapeHtml(STYLE_LABELS[r.style] || r.style) + '</div>' +
      '<div class="recap-item-time">' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('.recap-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('recap-content').textContent = r.recap;
    });
    list.appendChild(item);
  });
}

// -- Email tab --

async function loadEmails() {
  const list = document.getElementById('email-list');
  const empty = document.getElementById('email-list-empty');
  const resp = await fetch('/api/emails');
  const emails = await resp.json();
  list.innerHTML = '';
  empty.style.display = emails.length ? 'none' : 'block';
  [...emails].reverse().forEach(e => {
    const item = document.createElement('div');
    item.className = 'email-item';
    item.dataset.id = e.id;
    const ts = new Date(e.timestamp * 1000);
    item.innerHTML =
      '<div class="email-item-from">' + escapeHtml(e.sender) + '</div>' +
      '<div class="email-item-subject">' + escapeHtml(e.subject) + '</div>' +
      '<div class="email-item-date">' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => viewEmail(e));
    list.appendChild(item);
  });
}

function viewEmail(e) {
  document.querySelectorAll('.email-item').forEach(el => el.classList.remove('active'));
  const active = document.querySelector('.email-item[data-id="' + e.id + '"]');
  if (active) active.classList.add('active');
  document.getElementById('email-viewer-from').textContent = e.sender;
  document.getElementById('email-viewer-subject').textContent = e.subject;
  document.getElementById('email-viewer-date').textContent = new Date(e.timestamp * 1000).toLocaleString();
  document.getElementById('email-viewer-body').textContent = e.body;
  document.getElementById('email-viewer').style.display = '';
  document.getElementById('email-compose').style.display = 'none';
  document.getElementById('email-empty-state').style.display = 'none';
}

document.getElementById('compose-email-btn').addEventListener('click', () => {
  document.getElementById('email-compose-name').value = '';
  document.getElementById('email-compose-role').value = 'Scenario Director';
  document.getElementById('email-compose-role-custom').style.display = 'none';
  document.getElementById('email-compose-subject').value = '';
  document.getElementById('email-compose-body').value = '';
  document.getElementById('email-viewer').style.display = 'none';
  document.getElementById('email-compose').style.display = '';
  document.getElementById('email-empty-state').style.display = 'none';
  document.getElementById('email-compose-subject').focus();
});

document.getElementById('email-compose-role').addEventListener('change', (e) => {
  const custom = document.getElementById('email-compose-role-custom');
  custom.style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('email-compose-cancel').addEventListener('click', () => {
  document.getElementById('email-compose').style.display = 'none';
  document.getElementById('email-empty-state').style.display = '';
});

document.getElementById('email-compose-send').addEventListener('click', async () => {
  const name = document.getElementById('email-compose-name').value.trim() || 'Anonymous';
  let role = document.getElementById('email-compose-role').value;
  if (role === 'custom') role = document.getElementById('email-compose-role-custom').value.trim();
  const sender = role ? name + ' (' + role + ')' : name;
  const subject = document.getElementById('email-compose-subject').value.trim();
  const body = document.getElementById('email-compose-body').value.trim();
  if (!subject) { document.getElementById('email-compose-subject').focus(); return; }
  const resp = await fetch('/api/emails', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sender, subject, body}),
  });
  if (resp.ok) {
    document.getElementById('email-compose').style.display = 'none';
    document.getElementById('email-empty-state').style.display = '';
    loadEmails();
    showNotice('Email sent: ' + subject);
  }
});

// -- Memos tab --

let _currentMemoThread = null;

async function loadMemoThreads() {
  const list = document.getElementById('memo-threads-list');
  const empty = document.getElementById('memo-threads-empty');
  const resp = await fetch('/api/memos/threads');
  const threads = await resp.json();
  list.innerHTML = '';
  empty.style.display = threads.length ? 'none' : 'block';
  threads.forEach(t => {
    const item = document.createElement('div');
    item.className = 'memo-thread-item' + (t.id === _currentMemoThread ? ' active' : '');
    item.dataset.id = t.id;
    const preview = t.last_post_text || t.description || 'No posts yet';
    const postInfo = t.post_count + ' post' + (t.post_count !== 1 ? 's' : '');
    const age = _memoTimeAgo(t.last_post_at);
    item.innerHTML =
      '<div class="memo-thread-title">' + escapeHtml(t.title) + '</div>' +
      '<div class="memo-thread-preview">' + escapeHtml(preview.substring(0, 60)) + '</div>' +
      '<div class="memo-thread-meta">' + escapeHtml(t.creator) + ' &middot; ' + postInfo + ' &middot; ' + age + '</div>';
    item.addEventListener('click', () => viewMemoThread(t.id));
    list.appendChild(item);
  });
}

function _memoTimeAgo(ts) {
  const seconds = Math.floor(Date.now() / 1000 - ts);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return Math.floor(seconds / 60) + 'm ago';
  if (seconds < 86400) return Math.floor(seconds / 3600) + 'h ago';
  return Math.floor(seconds / 86400) + 'd ago';
}

async function viewMemoThread(threadId) {
  _currentMemoThread = threadId;
  document.querySelectorAll('.memo-thread-item').forEach(el =>
    el.classList.toggle('active', el.dataset.id === threadId));

  const resp = await fetch('/api/memos/threads/' + threadId);
  if (!resp.ok) { showNotice('Thread not found'); return; }
  const thread = await resp.json();

  document.getElementById('memo-thread-title').textContent = thread.title;
  document.getElementById('memo-thread-meta').textContent =
    'Started by ' + thread.creator + ' &middot; ' + _memoTimeAgo(thread.created_at);
  document.getElementById('memo-thread-meta').innerHTML =
    'Started by ' + escapeHtml(thread.creator) + ' &middot; ' + _memoTimeAgo(thread.created_at);
  const descEl = document.getElementById('memo-thread-description');
  descEl.innerHTML = thread.description ? renderMarkdown(thread.description) : '';
  descEl.style.display = thread.description ? '' : 'none';

  const postsList = document.getElementById('memo-posts-list');
  const posts = thread.posts || [];
  postsList.innerHTML = '';
  posts.forEach(p => {
    const div = document.createElement('div');
    div.className = 'memo-post';
    const ts = new Date(p.timestamp * 1000).toLocaleString();
    div.innerHTML =
      '<div style="display:flex;align-items:baseline">' +
        '<span class="memo-post-author">' + escapeHtml(p.author) + '</span>' +
        '<span class="memo-post-date">' + ts + '</span>' +
      '</div>' +
      '<div class="memo-post-text">' + renderMarkdown(p.text) + '</div>';
    postsList.appendChild(div);
  });

  document.getElementById('memo-thread-viewer').style.display = '';
  document.getElementById('memo-empty-state').style.display = 'none';
  document.getElementById('memo-reply-text').value = '';
}

function _getMemoSender(nameId, roleId, customId) {
  const name = document.getElementById(nameId).value.trim() || 'Anonymous';
  let role = document.getElementById(roleId).value;
  if (role === 'custom') role = document.getElementById(customId).value.trim();
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('memo-reply-role').addEventListener('change', (e) => {
  document.getElementById('memo-reply-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('memo-create-role').addEventListener('change', (e) => {
  document.getElementById('memo-create-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('create-memo-thread-btn').addEventListener('click', () => {
  const modal = document.getElementById('memo-create-modal');
  document.getElementById('memo-create-title').value = '';
  document.getElementById('memo-create-description').value = '';
  document.getElementById('memo-create-name').value = '';
  document.getElementById('memo-create-role').value = 'Scenario Director';
  document.getElementById('memo-create-role-custom').style.display = 'none';
  document.getElementById('memo-create-role-custom').value = '';
  openModal('memo-create-modal');
  document.getElementById('memo-create-title').focus();
});

document.getElementById('memo-create-cancel').addEventListener('click', () => {
  closeModal('memo-create-modal');
});

document.getElementById('memo-create-submit').addEventListener('click', async () => {
  const title = document.getElementById('memo-create-title').value.trim();
  if (!title) { document.getElementById('memo-create-title').focus(); return; }
  const description = document.getElementById('memo-create-description').value.trim();
  const creator = _getMemoSender('memo-create-name', 'memo-create-role', 'memo-create-role-custom');
  const resp = await fetch('/api/memos/threads', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, creator, description})
  });
  if (resp.ok) {
    const thread = await resp.json();
    closeModal('memo-create-modal');
    loadMemoThreads();
    viewMemoThread(thread.id);
    showNotice('Thread created: ' + title);
  }
});

document.getElementById('memo-reply-send').addEventListener('click', async () => {
  if (!_currentMemoThread) return;
  const textarea = document.getElementById('memo-reply-text');
  const text = textarea.value.trim();
  if (!text) return;
  const author = _getMemoSender('memo-reply-name', 'memo-reply-role', 'memo-reply-role-custom');
  const resp = await fetch('/api/memos/threads/' + _currentMemoThread + '/posts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, author})
  });
  if (resp.ok) {
    textarea.value = '';
    viewMemoThread(_currentMemoThread);
    loadMemoThreads();
  }
});

document.getElementById('memo-delete-btn').addEventListener('click', async () => {
  if (!_currentMemoThread) return;
  if (!confirm('Delete this discussion thread and all its posts?')) return;
  const resp = await fetch('/api/memos/threads/' + _currentMemoThread, {method: 'DELETE'});
  if (resp.ok) {
    _currentMemoThread = null;
    document.getElementById('memo-thread-viewer').style.display = 'none';
    document.getElementById('memo-empty-state').style.display = '';
    loadMemoThreads();
    showNotice('Thread deleted');
  }
});

// Load memos when tab is selected — handled by tab click handler below

// -- Blog tab --

let _currentBlogPost = null;
let _blogFilter = 'all';

async function loadBlogPosts() {
  const list = document.getElementById('blog-posts-list');
  const empty = document.getElementById('blog-posts-empty');
  let url = '/api/blog/posts';
  if (_blogFilter !== 'all') url += '?filter=' + _blogFilter;
  const resp = await fetch(url);
  const posts = await resp.json();
  list.innerHTML = '';
  empty.style.display = posts.length ? 'none' : 'block';
  posts.forEach(p => {
    const item = document.createElement('div');
    item.className = 'blog-post-item' + (p.slug === _currentBlogPost ? ' active' : '');
    item.dataset.slug = p.slug;
    let badge = p.is_external
      ? '<span class="blog-external-badge">External</span>'
      : '<span class="blog-internal-badge">Internal</span>';
    const pStatus = p.status || 'published';
    if (pStatus === 'draft') badge += ' <span class="blog-internal-badge" style="background:#f39c12;color:var(--text-bright)">Draft</span>';
    if (pStatus === 'unpublished') badge += ' <span class="blog-internal-badge" style="background:var(--accent);color:var(--text-bright)">Unpub</span>';
    const preview = (p.body || '').substring(0, 60);
    const replyInfo = p.reply_count + ' repl' + (p.reply_count !== 1 ? 'ies' : 'y');
    const age = _memoTimeAgo(p.created_at);
    item.innerHTML =
      '<div style="display:flex;align-items:center"><span class="blog-post-title">' + escapeHtml(p.title) + '</span>' + badge + '</div>' +
      '<div class="blog-post-preview">' + escapeHtml(preview) + '</div>' +
      '<div class="blog-post-meta">' + escapeHtml(p.author) + ' &middot; ' + replyInfo + ' &middot; ' + age + '</div>';
    item.addEventListener('click', () => viewBlogPost(p.slug));
    list.appendChild(item);
  });
}

document.querySelectorAll('.blog-filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    _blogFilter = btn.dataset.blogFilter;
    document.querySelectorAll('.blog-filter-btn').forEach(b => b.classList.toggle('active', b === btn));
    loadBlogPosts();
  });
});

async function viewBlogPost(slug) {
  _currentBlogPost = slug;
  document.querySelectorAll('.blog-post-item').forEach(el =>
    el.classList.toggle('active', el.dataset.slug === slug));

  const resp = await fetch('/api/blog/posts/' + slug);
  if (!resp.ok) { showNotice('Post not found'); return; }
  const post = await resp.json();

  document.getElementById('blog-post-title').textContent = post.title;
  let badgeHtml = post.is_external
    ? '<span class="blog-external-badge">External</span>'
    : '<span class="blog-internal-badge">Internal</span>';
  const status = post.status || 'published';
  if (status === 'draft') badgeHtml += ' <span class="blog-internal-badge" style="background:#f39c12;color:var(--text-bright)">Draft</span>';
  if (status === 'unpublished') badgeHtml += ' <span class="blog-internal-badge" style="background:var(--accent);color:var(--text-bright)">Unpublished</span>';
  document.getElementById('blog-post-badge').innerHTML = badgeHtml;
  document.getElementById('blog-post-author').textContent = post.author;
  document.getElementById('blog-post-date').textContent = new Date(post.created_at * 1000).toLocaleString();

  // Show/hide publish/unpublish buttons based on status
  document.getElementById('blog-publish-btn').style.display = (status !== 'published') ? '' : 'none';
  document.getElementById('blog-unpublish-btn').style.display = (status === 'published') ? '' : 'none';

  const tagsEl = document.getElementById('blog-post-tags');
  tagsEl.innerHTML = '';
  (post.tags || []).forEach(tag => {
    const span = document.createElement('span');
    span.className = 'blog-tag';
    span.textContent = tag;
    tagsEl.appendChild(span);
  });

  document.getElementById('blog-post-body').innerHTML = renderMarkdown(post.body || '');

  const replies = post.replies || [];
  document.getElementById('blog-replies-header').textContent = replies.length + ' Repl' + (replies.length !== 1 ? 'ies' : 'y');
  const repliesList = document.getElementById('blog-replies-list');
  repliesList.innerHTML = '';
  replies.forEach(r => {
    const div = document.createElement('div');
    div.className = 'blog-reply';
    const ts = new Date(r.timestamp * 1000).toLocaleString();
    div.innerHTML =
      '<div style="display:flex;align-items:baseline">' +
        '<span class="blog-reply-author">' + escapeHtml(r.author) + '</span>' +
        '<span class="blog-reply-date">' + ts + '</span>' +
      '</div>' +
      '<div class="blog-reply-text">' + renderMarkdown(r.text) + '</div>';
    repliesList.appendChild(div);
  });

  document.getElementById('blog-post-viewer').style.display = '';
  document.getElementById('blog-empty-state').style.display = 'none';
  document.getElementById('blog-reply-text').value = '';
}

function _getBlogSender(nameId, roleId, customId) {
  const name = document.getElementById(nameId).value.trim() || 'Anonymous';
  let role = document.getElementById(roleId).value;
  if (role === 'custom') role = document.getElementById(customId).value.trim();
  return role ? name + ' (' + role + ')' : name;
}

document.getElementById('blog-reply-role').addEventListener('change', (e) => {
  document.getElementById('blog-reply-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('blog-create-role').addEventListener('change', (e) => {
  document.getElementById('blog-create-role-custom').style.display = e.target.value === 'custom' ? '' : 'none';
});

document.getElementById('create-blog-post-btn').addEventListener('click', () => {
  document.getElementById('blog-create-title').value = '';
  document.getElementById('blog-create-body').value = '';
  document.getElementById('blog-create-tags').value = '';
  document.getElementById('blog-create-name').value = '';
  document.getElementById('blog-create-role').value = 'Scenario Director';
  document.getElementById('blog-create-role-custom').style.display = 'none';
  document.getElementById('blog-create-role-custom').value = '';
  document.getElementById('blog-create-external').checked = false;
  openModal('blog-create-modal');
  document.getElementById('blog-create-title').focus();
});

document.getElementById('blog-create-cancel').addEventListener('click', () => {
  closeModal('blog-create-modal');
});

async function _submitBlogPost(status) {
  const title = document.getElementById('blog-create-title').value.trim();
  if (!title) { document.getElementById('blog-create-title').focus(); return; }
  const body = document.getElementById('blog-create-body').value.trim();
  const author = _getBlogSender('blog-create-name', 'blog-create-role', 'blog-create-role-custom');
  const is_external = document.getElementById('blog-create-external').checked;
  const tagsRaw = document.getElementById('blog-create-tags').value.trim();
  const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(t => t) : [];
  const resp = await fetch('/api/blog/posts', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title, body, author, is_external, tags, status})
  });
  if (resp.ok) {
    const post = await resp.json();
    closeModal('blog-create-modal');
    loadBlogPosts();
    viewBlogPost(post.slug);
    showNotice((status === 'draft' ? 'Draft saved: ' : 'Published: ') + title);
  }
}
document.getElementById('blog-create-submit').addEventListener('click', () => _submitBlogPost('published'));
document.getElementById('blog-create-draft').addEventListener('click', () => _submitBlogPost('draft'));

document.getElementById('blog-reply-send').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const textarea = document.getElementById('blog-reply-text');
  const text = textarea.value.trim();
  if (!text) return;
  const author = _getBlogSender('blog-reply-name', 'blog-reply-role', 'blog-reply-role-custom');
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost + '/replies', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text, author})
  });
  if (resp.ok) {
    textarea.value = '';
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
  }
});

document.getElementById('blog-publish-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'published'})
  });
  if (resp.ok) {
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
    showNotice('Post published');
  }
});

document.getElementById('blog-unpublish-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: 'unpublished'})
  });
  if (resp.ok) {
    viewBlogPost(_currentBlogPost);
    loadBlogPosts();
    showNotice('Post unpublished');
  }
});

document.getElementById('blog-download-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost);
  if (!resp.ok) return;
  const post = await resp.json();
  const blob = new Blob([post.body || ''], {type: 'text/plain'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (_currentBlogPost || 'blog-post') + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
});

document.getElementById('blog-delete-btn').addEventListener('click', async () => {
  if (!_currentBlogPost) return;
  if (!confirm('Delete this blog post and all its replies?')) return;
  const resp = await fetch('/api/blog/posts/' + _currentBlogPost, {method: 'DELETE'});
  if (resp.ok) {
    _currentBlogPost = null;
    document.getElementById('blog-post-viewer').style.display = 'none';
    document.getElementById('blog-empty-state').style.display = '';
    loadBlogPosts();
    showNotice('Post deleted');
  }
});

// -- Events tab --

let _eventsSubTab = 'pool';

document.querySelectorAll('.events-sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    _eventsSubTab = tab.dataset.eventsTab;
    document.querySelectorAll('.events-sub-tab').forEach(t => t.classList.toggle('active', t === tab));
    document.getElementById('events-pool-view').style.display = _eventsSubTab === 'pool' ? '' : 'none';
    document.getElementById('events-log-view').style.display = _eventsSubTab === 'log' ? '' : 'none';
    if (_eventsSubTab === 'pool') loadEventPool();
    if (_eventsSubTab === 'log') loadEventLog();
  });
});

async function loadEventPool() {
  const grid = document.getElementById('events-pool-grid');
  const empty = document.getElementById('events-pool-empty');
  const resp = await fetch('/api/events/pool');
  const pool = await resp.json();
  grid.innerHTML = '';
  empty.style.display = pool.length ? 'none' : 'block';
  pool.forEach((evt, i) => {
    const actions = evt.actions || [];
    const actionTypes = [...new Set(actions.map(a => a.type))].join(', ');
    const preview = actions.find(a => a.type === 'message');
    const card = document.createElement('div');
    card.className = 'event-card';
    card.style.cursor = 'pointer';
    card.innerHTML =
      '<div class="event-card-header">' +
        '<span class="event-card-severity event-sev-' + (evt.severity || 'medium') + '">' + escapeHtml(evt.severity || 'medium') + '</span>' +
        '<span class="event-card-name">' + escapeHtml(evt.name || 'Unnamed') + '</span>' +
      '</div>' +
      '<div class="event-card-actions">' + escapeHtml(actions.length + ' action(s): ' + actionTypes) + '</div>' +
      (preview ? '<div class="event-card-preview">' + escapeHtml(preview.content || '').substring(0, 80) + '</div>' : '');
    const trigBtn = document.createElement('button');
    trigBtn.className = 'event-trigger-btn';
    trigBtn.style.cssText = 'width:100%;margin-top:8px';
    trigBtn.textContent = 'Trigger';
    trigBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      triggerEvent(evt);
    });
    card.appendChild(trigBtn);
    card.addEventListener('click', (e) => {
      if (e.target === trigBtn) return;
      openEventEditor(i, evt);
    });
    grid.appendChild(card);
  });
}

async function triggerEvent(evt) {
  const resp = await fetch('/api/events/trigger', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(evt),
  });
  if (resp.ok) {
    showNotice('Event triggered: ' + (evt.name || 'Custom Event') + ' (' + (evt.actions || []).length + ' actions fired)');
  }
  loadEventLog();
}

async function loadEventLog() {
  const list = document.getElementById('events-log-list');
  const empty = document.getElementById('events-log-empty');
  const resp = await fetch('/api/events/log');
  const log = await resp.json();
  list.innerHTML = '';
  empty.style.display = log.length ? 'none' : 'block';
  [...log].reverse().forEach(entry => {
    const row = document.createElement('div');
    row.className = 'event-log-row';
    row.style.cssText = 'cursor:pointer;flex-wrap:wrap';
    const ts = new Date(entry.timestamp * 1000).toLocaleString();
    const actionCount = (entry.actions || []).length;
    row.innerHTML =
      '<span class="event-log-time">' + ts + '</span>' +
      '<span class="event-card-severity event-sev-' + (entry.severity || 'medium') + '">' + escapeHtml(entry.severity || 'medium') + '</span>' +
      '<span class="event-log-name">' + escapeHtml(entry.name || 'Custom') + '</span>' +
      '<span class="event-log-actions">' + actionCount + ' action(s)</span>';
    const retrigger = document.createElement('button');
    retrigger.className = 'session-btn';
    retrigger.style.cssText = 'font-size:10px';
    retrigger.textContent = 'Re-trigger';
    retrigger.addEventListener('click', (e) => {
      e.stopPropagation();
      triggerEvent(entry);
    });
    row.appendChild(retrigger);
    // Expandable YAML detail
    const detail = document.createElement('div');
    detail.style.cssText = 'display:none;width:100%;margin-top:8px;background:var(--input-bg);border-radius:6px;padding:10px;font-family:monospace;font-size:12px;color:var(--text);white-space:pre-wrap;max-height:300px;overflow-y:auto';
    const clean = Object.assign({}, entry);
    delete clean._history;
    detail.textContent = eventToYaml(clean);
    row.appendChild(detail);
    row.addEventListener('click', () => {
      detail.style.display = detail.style.display === 'none' ? '' : 'none';
    });
    list.appendChild(row);
  });
}

let _eventEditIndex = -1; // -1 = new event
let _eventEditHistory = []; // version history for current event

function eventToYaml(evt) {
  if (typeof jsyaml !== 'undefined') return jsyaml.dump(evt, {lineWidth: -1});
  return JSON.stringify(evt, null, 2);
}

function yamlToEvent(text) {
  if (typeof jsyaml !== 'undefined') return jsyaml.load(text);
  return JSON.parse(text);
}

function openEventEditor(index, evt) {
  _eventEditIndex = index;
  _eventEditHistory = evt._history || [];
  const clean = Object.assign({}, evt);
  delete clean._history;
  document.getElementById('event-edit-title').textContent = index >= 0 ? 'Edit Event' : 'New Event';
  document.getElementById('event-edit-yaml').value = eventToYaml(clean);
  document.getElementById('event-edit-delete').style.display = index >= 0 ? '' : 'none';
  document.getElementById('event-edit-history').style.display = 'none';
  renderEventHistory();
  openModal('event-edit-modal');
}

function renderEventHistory() {
  const list = document.getElementById('event-edit-history-list');
  list.innerHTML = '';
  if (!_eventEditHistory.length) {
    list.innerHTML = '<div style="padding:8px 12px;font-size:11px;color:var(--text-dim)">No previous versions</div>';
    return;
  }
  [..._eventEditHistory].reverse().forEach((v, i) => {
    const item = document.createElement('div');
    item.className = 'thought-item';
    const ts = new Date(v.saved_at * 1000);
    item.innerHTML = '<div class="thought-item-time">v' + (_eventEditHistory.length - i) + ' - ' + ts.toLocaleString() + '</div>';
    item.addEventListener('click', () => {
      document.querySelectorAll('#event-edit-history-list .thought-item').forEach(el => el.classList.remove('active'));
      item.classList.add('active');
      document.getElementById('event-edit-yaml').value = eventToYaml(v.event);
    });
    list.appendChild(item);

    const restoreBtn = document.createElement('button');
    restoreBtn.className = 'session-btn';
    restoreBtn.style.cssText = 'font-size:10px;padding:2px 8px;margin-top:4px;width:100%';
    restoreBtn.textContent = 'Restore';
    restoreBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      document.getElementById('event-edit-yaml').value = eventToYaml(v.event);
    });
    item.appendChild(restoreBtn);
    list.appendChild(item);
  });
}

document.getElementById('event-edit-history-btn').addEventListener('click', () => {
  const panel = document.getElementById('event-edit-history');
  panel.style.display = panel.style.display === 'none' ? '' : 'none';
});

document.getElementById('event-edit-close').addEventListener('click', () => closeModal('event-edit-modal'));

document.getElementById('event-edit-save').addEventListener('click', async () => {
  let evt;
  try {
    evt = yamlToEvent(document.getElementById('event-edit-yaml').value);
  } catch(e) {
    showNotice('Invalid YAML: ' + e.message);
    return;
  }
  // Save version history
  if (_eventEditIndex >= 0) {
    const oldResp = await fetch('/api/events/pool');
    const oldPool = await oldResp.json();
    const oldEvt = oldPool[_eventEditIndex];
    if (oldEvt) {
      if (!evt._history) evt._history = oldEvt._history || [];
      const clean = Object.assign({}, oldEvt);
      delete clean._history;
      evt._history.push({event: clean, saved_at: Date.now() / 1000});
    }
    await fetch('/api/events/pool/' + _eventEditIndex, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(evt),
    });
  } else {
    evt._history = [];
    await fetch('/api/events/pool', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(evt),
    });
  }
  closeModal('event-edit-modal');
  loadEventPool();
});

document.getElementById('event-edit-delete').addEventListener('click', async () => {
  if (_eventEditIndex < 0) return;
  if (!confirm('Delete this event?')) return;
  await fetch('/api/events/pool/' + _eventEditIndex, {method: 'DELETE'});
  closeModal('event-edit-modal');
  loadEventPool();
});

document.getElementById('events-add-btn').addEventListener('click', () => {
  const template = {
    name: 'New Event',
    severity: 'medium',
    actions: [
      {type: 'message', channel: '#general', sender: 'System', content: 'Something happened!'}
    ]
  };
  openEventEditor(-1, template);
});

// Add Events tab loading to tab switch
// (handled in the existing tab switch handler below)

// -- Orchestrator status polling --

const orchDot = document.getElementById('orch-dot');
const orchLabel = document.getElementById('orch-label');
const STATUS_LABELS = {
  disconnected: 'Disconnected',
  connecting: 'Connecting...',
  waiting: 'Waiting for session',
  starting: 'Starting agents...',
  ready: 'Ready',
  responding: 'Responding...',
  stopping: 'Stopping agents...',
  restarting: 'Restarting...',
};

async function pollStatus() {
  try {
    const resp = await fetch('/api/status');
    const status = await resp.json();
    const state = status.orchestrator.state || 'disconnected';
    orchDot.className = 'status-dot ' + state;
    const msg = status.orchestrator.message;
    orchLabel.textContent = msg || STATUS_LABELS[state] || state;
    // Auto-refresh NPC and Usage tabs if visible
    if (currentTab === 'npcs') loadNPCs();
    if (currentTab === 'usage') loadUsage();
  } catch(e) {
    orchDot.className = 'status-dot disconnected';
    orchLabel.textContent = 'Server error';
  }
}

setInterval(pollStatus, 3000);
pollStatus();

// -- Session controls --

function showLoading(text) {
  document.getElementById('loading-text').textContent = text || 'Loading...';
  document.getElementById('loading-overlay').classList.add('open');
}
function hideLoading() {
  document.getElementById('loading-overlay').classList.remove('open');
}
function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function showNotice(text) {
  // Show a non-blocking notice bar at the top of the page
  let bar = document.getElementById('notice-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'notice-bar';
    bar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:999;background:var(--accent);color:var(--text-bright);padding:10px 20px;font-size:13px;display:flex;align-items:center;justify-content:space-between;';
    const dismiss = document.createElement('button');
    dismiss.textContent = 'Dismiss';
    dismiss.style.cssText = 'background:rgba(0,0,0,0.3);color:var(--text-bright);border:none;padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px;margin-left:16px;';
    dismiss.addEventListener('click', () => bar.remove());
    bar.appendChild(document.createElement('span'));
    bar.appendChild(dismiss);
    document.body.prepend(bar);
  }
  bar.querySelector('span').textContent = text;
}

async function reloadAllState() {
  messagesByChannel = {};
  seenIds.clear();
  unreadByChannel = {};
  _lastUsageData = null;
  currentChannel = '#general';
  await loadPersonas();
  await loadRoles();
  await loadChannels();
  await loadMessages();
  renderSidebar();
  renderMessages();
  loadFolders();
  loadDocs();
  loadRepos();
  loadTickets();
  loadNPCs();
  if (_eventsSubTab === 'pool') loadEventPool();
  else loadEventLog();
}

// -- New Session Modal --

document.getElementById('session-new-btn').addEventListener('click', async () => {
  const sel = document.getElementById('new-session-scenario');
  sel.innerHTML = '';
  document.getElementById('new-session-status').textContent = '';
  document.getElementById('new-session-name').value = '';
  const resp = await fetch('/api/session/scenarios');
  const scenarios = await resp.json();
  scenarios.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.key;
    opt.textContent = s.name + ' (' + s.characters + ' characters)';
    opt.dataset.desc = s.description || '';
    sel.appendChild(opt);
  });
  // Default to tech-startup if available, otherwise first
  const preferred = scenarios.find(s => s.key === 'tech-startup');
  if (preferred) sel.value = preferred.key;
  const selected = scenarios.find(s => s.key === sel.value) || scenarios[0];
  document.getElementById('new-session-scenario-desc').textContent = selected ? selected.description : '';
  openModal('new-session-modal');
});

document.getElementById('new-session-scenario').addEventListener('change', (e) => {
  const opt = e.target.selectedOptions[0];
  document.getElementById('new-session-scenario-desc').textContent = opt ? opt.dataset.desc : '';
});

document.getElementById('new-session-cancel').addEventListener('click', () => closeModal('new-session-modal'));

document.getElementById('new-session-confirm').addEventListener('click', async () => {
  const scenario = document.getElementById('new-session-scenario').value;
  if (!scenario) return;
  const status = document.getElementById('new-session-status');
  status.textContent = 'Creating session...';
  document.getElementById('new-session-confirm').disabled = true;
  closeModal('new-session-modal');
  showLoading('Creating new session...');
  try {
    await fetch('/api/session/new', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({scenario}),
    });
    await reloadAllState();
    pollStatus();
  } finally {
    hideLoading();
    document.getElementById('new-session-confirm').disabled = false;
  }
});

// -- Save Session Modal --

async function _populateSaveSessionList() {
  const listEl = document.getElementById('save-session-list');
  listEl.innerHTML = '<div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">Loading...</div>';
  try {
    const resp = await fetch('/api/session/list');
    const sessions = await resp.json();
    if (sessions.length === 0) {
      listEl.innerHTML = '<div style="padding:12px;color:var(--text-dim);text-align:center;font-size:12px">No existing saves</div>';
      return;
    }
    sessions.sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0));
    listEl.innerHTML = '';
    sessions.forEach(s => {
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--border-dark)';
      row.addEventListener('mouseenter', () => row.style.background = 'var(--bg-hover, rgba(255,255,255,0.04))');
      row.addEventListener('mouseleave', () => row.style.background = '');
      const nameSpan = document.createElement('span');
      nameSpan.style.cssText = 'flex:1;color:var(--text);font-size:13px';
      nameSpan.textContent = s.name || s.instance_dir;
      const dateSpan = document.createElement('span');
      dateSpan.style.cssText = 'color:var(--text-dim);font-size:11px;margin-left:12px;white-space:nowrap';
      dateSpan.textContent = _fmtSessionDate(s.saved_at);
      row.appendChild(nameSpan);
      row.appendChild(dateSpan);
      row.addEventListener('click', () => {
        // Pre-fill with name + fresh timestamp for easy branching
        const now = new Date();
        const ts = now.getFullYear()
          + String(now.getMonth() + 1).padStart(2, '0')
          + String(now.getDate()).padStart(2, '0')
          + '-' + String(now.getHours()).padStart(2, '0')
          + String(now.getMinutes()).padStart(2, '0');
        const baseName = (s.name || s.instance_dir).replace(/--?[0-9]{4}-?[0-9]{2}-?[0-9]{2}-?[0-9]{4}$/, '').replace(/-[0-9]{8}-[0-9]{4}$/, '');
        document.getElementById('save-session-name').value = baseName + '-' + ts;
        document.getElementById('save-session-name').focus();
        // Highlight selected row
        listEl.querySelectorAll('div').forEach(r => r.style.borderLeft = '');
        row.style.borderLeft = '3px solid var(--accent)';
      });
      listEl.appendChild(row);
    });
  } catch (e) {
    listEl.innerHTML = '<div style="padding:12px;color:var(--accent);text-align:center;font-size:12px">Failed to load sessions</div>';
  }
}

document.getElementById('session-save-btn').addEventListener('click', async () => {
  document.getElementById('save-session-name').value = '';
  document.getElementById('save-session-status').textContent = '';
  openModal('save-session-modal');
  await _populateSaveSessionList();
  document.getElementById('save-session-name').focus();
});

document.getElementById('save-session-cancel').addEventListener('click', () => closeModal('save-session-modal'));

document.getElementById('save-session-confirm').addEventListener('click', async () => {
  const name = document.getElementById('save-session-name').value.trim();
  const status = document.getElementById('save-session-status');
  status.textContent = 'Saving...';
  document.getElementById('save-session-confirm').disabled = true;
  try {
    const resp = await fetch('/api/session/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name || undefined}),
    });
    if (resp.ok) {
      const meta = await resp.json();
      status.textContent = 'Saved: ' + (meta.name || meta.instance_dir);
      status.style.color = '#2ecc71';
      setTimeout(() => {
        closeModal('save-session-modal');
        status.style.color = '';
      }, 1500);
    } else {
      const err = await resp.json();
      status.textContent = 'Error: ' + (err.error || 'unknown');
      status.style.color = 'var(--accent)';
    }
  } finally {
    document.getElementById('save-session-confirm').disabled = false;
  }
});

// -- Load Session Modal --

let _lmSessions = [];
let _lmSortCol = 'saved_at';
let _lmSortAsc = false;
let _lmSelected = null;

function _lmUpdateSortArrows() {
  document.querySelectorAll('#load-session-table th[data-lm-sort]').forEach(th => {
    const arrow = th.querySelector('.lm-sort-arrow');
    if (th.dataset.lmSort === _lmSortCol) {
      arrow.textContent = _lmSortAsc ? ' \\u25B2' : ' \\u25BC';
      th.style.color = 'var(--text)';
    } else {
      arrow.textContent = '';
      th.style.color = 'var(--text-dim)';
    }
  });
}

function _lmRenderRows() {
  const tbody = document.getElementById('load-session-body');
  if (_lmSessions.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">No saved sessions</td></tr>';
    document.getElementById('load-session-confirm').disabled = true;
    return;
  }
  const sorted = [..._lmSessions];
  sorted.sort((a, b) => {
    let va, vb;
    if (_lmSortCol === 'name') {
      va = (a.name || a.instance_dir).toLowerCase();
      vb = (b.name || b.instance_dir).toLowerCase();
    } else if (_lmSortCol === 'scenario') {
      va = (a.scenario || '').toLowerCase();
      vb = (b.scenario || '').toLowerCase();
    } else {
      va = a[_lmSortCol] || 0;
      vb = b[_lmSortCol] || 0;
    }
    if (va < vb) return _lmSortAsc ? -1 : 1;
    if (va > vb) return _lmSortAsc ? 1 : -1;
    return 0;
  });
  tbody.innerHTML = '';
  sorted.forEach(s => {
    const tr = document.createElement('tr');
    tr.style.cssText = 'border-bottom:1px solid var(--border-dark);cursor:pointer';
    if (_lmSelected === s.instance_dir) {
      tr.style.background = 'rgba(231,76,60,0.12)';
    }
    tr.addEventListener('mouseenter', () => { if (_lmSelected !== s.instance_dir) tr.style.background = 'var(--bg-hover, rgba(255,255,255,0.04))'; });
    tr.addEventListener('mouseleave', () => { if (_lmSelected !== s.instance_dir) tr.style.background = ''; });
    tr.addEventListener('click', () => {
      _lmSelected = s.instance_dir;
      document.getElementById('load-session-confirm').disabled = false;
      _lmRenderRows();
    });
    tr.addEventListener('dblclick', () => {
      _lmSelected = s.instance_dir;
      document.getElementById('load-session-confirm').click();
    });
    const nameTd = document.createElement('td');
    nameTd.style.cssText = 'padding:7px 10px;color:var(--text)';
    nameTd.textContent = s.name || s.instance_dir;
    const scenarioTd = document.createElement('td');
    scenarioTd.style.cssText = 'padding:7px 10px;color:var(--text-dim)';
    scenarioTd.textContent = s.scenario || '—';
    const createdTd = document.createElement('td');
    createdTd.style.cssText = 'padding:7px 10px;color:var(--text-dim);white-space:nowrap';
    createdTd.textContent = _fmtSessionDate(s.created_at);
    const savedTd = document.createElement('td');
    savedTd.style.cssText = 'padding:7px 10px;color:var(--text-dim);white-space:nowrap';
    savedTd.textContent = _fmtSessionDate(s.saved_at);
    tr.appendChild(nameTd);
    tr.appendChild(scenarioTd);
    tr.appendChild(createdTd);
    tr.appendChild(savedTd);
    tbody.appendChild(tr);
  });
  _lmUpdateSortArrows();
}

document.querySelectorAll('#load-session-table th[data-lm-sort]').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.lmSort;
    if (_lmSortCol === col) {
      _lmSortAsc = !_lmSortAsc;
    } else {
      _lmSortCol = col;
      _lmSortAsc = (col === 'name' || col === 'scenario');
    }
    _lmRenderRows();
  });
});

async function refreshSessionsList() {
  const tbody = document.getElementById('load-session-body');
  tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--text-dim);text-align:center">Loading...</td></tr>';
  try {
    const resp = await fetch('/api/session/list');
    _lmSessions = await resp.json();
    _lmSelected = null;
    _lmRenderRows();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="4" style="padding:16px 10px;color:var(--accent);text-align:center">Failed to load sessions</td></tr>';
  }
}

// Replace header load dropdown with a button
{
  const headerSelect = document.getElementById('session-load-select');
  if (headerSelect) headerSelect.remove();
  const loadBtn = document.createElement('button');
  loadBtn.id = 'session-load-btn';
  loadBtn.className = 'session-btn';
  loadBtn.title = 'Load session';
  loadBtn.textContent = 'Load';
  document.getElementById('session-controls').appendChild(loadBtn);

  loadBtn.addEventListener('click', async () => {
    document.getElementById('load-session-status').textContent = '';
    document.getElementById('load-session-confirm').disabled = true;
    await refreshSessionsList();
    openModal('load-session-modal');
  });
}

document.getElementById('load-session-cancel').addEventListener('click', () => closeModal('load-session-modal'));

document.getElementById('load-session-confirm').addEventListener('click', async () => {
  if (!_lmSelected) return;
  const status = document.getElementById('load-session-status');
  status.textContent = 'Loading session...';
  document.getElementById('load-session-confirm').disabled = true;
  closeModal('load-session-modal');
  showLoading('Loading session...');
  try {
    const resp = await fetch('/api/session/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({instance: _lmSelected}),
    });
    if (resp.ok) {
      await reloadAllState();
    } else {
      const err = await resp.json();
      hideLoading();
      openModal('load-session-modal');
      status.textContent = 'Error: ' + (err.error || 'unknown');
      status.style.color = 'var(--accent)';
      return;
    }
  } finally {
    hideLoading();
    document.getElementById('load-session-confirm').disabled = false;
  }
});
</script>
</body>
</html>"""
