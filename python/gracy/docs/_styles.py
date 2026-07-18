"""Static CSS/JS assets embedded in the rendered HTML docs page."""

_LIGHT_VARS = """\
  --bg: #f6f7f9;
  --panel: #ffffff;
  --text: #1c2433;
  --muted: #647080;
  --border: #e2e6ec;
  --code-bg: #eef1f5;
  --accent: #4f6df5;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.06);
  --status-2xx-bg: #e3f4ec; --status-2xx-fg: #0b7350;
  --status-3xx-bg: #e7edf7; --status-3xx-fg: #33518f;
  --status-4xx-bg: #fdf0e0; --status-4xx-fg: #a05a08;
  --status-5xx-bg: #fbe8e8; --status-5xx-fg: #b02a2a;
  --status-def-bg: #edeff2; --status-def-fg: #5b6472;\
"""

_DARK_VARS = """\
  --bg: #10141b;
  --panel: #181e28;
  --text: #e5eaf2;
  --muted: #8b96a8;
  --border: #29323f;
  --code-bg: #212936;
  --accent: #7c93ff;
  --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
  --status-2xx-bg: #14352a; --status-2xx-fg: #5fd3a5;
  --status-3xx-bg: #1c2940; --status-3xx-fg: #8fabe8;
  --status-4xx-bg: #3a2b12; --status-4xx-fg: #e8b364;
  --status-5xx-bg: #3d1c1c; --status-5xx-fg: #ef8f8f;
  --status-def-bg: #242b36; --status-def-fg: #98a2b3;\
"""

_CSS = f"""\
:root {{
{_LIGHT_VARS}
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
{_DARK_VARS}
  }}
}}
:root[data-theme="dark"] {{
{_DARK_VARS}
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}}
code, pre, .path, .base-url {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 0.92em;
}}
code {{ background: var(--code-bg); border-radius: 4px; padding: 0.1em 0.35em; }}
pre {{ margin: 0; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
a {{ color: var(--accent); text-decoration: none; }}
.muted {{ color: var(--muted); }}

.layout {{ display: flex; min-height: 100vh; }}

/* ------------------------------------------------------------- sidebar */
.sidebar {{
  position: sticky; top: 0; align-self: flex-start;
  width: 264px; flex: none; height: 100vh; overflow-y: auto;
  padding: 20px 16px; border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 12px;
}}
.brand {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
.brand-title {{ font-size: 17px; font-weight: 700; }}
.version-badge {{
  background: var(--code-bg); color: var(--muted); border: 1px solid var(--border);
  border-radius: 999px; padding: 0 8px; font-size: 12px; white-space: nowrap;
}}
.base-url {{ display: block; color: var(--muted); word-break: break-all; background: none; padding: 0; }}
nav {{ display: flex; flex-direction: column; gap: 2px; }}
.nav-group {{
  margin: 12px 0 4px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--muted);
}}
.nav-link {{
  display: flex; align-items: center; gap: 8px; padding: 4px 8px;
  border-radius: 6px; color: var(--text); font-size: 14px;
}}
.nav-link:hover {{ background: var(--code-bg); }}
.sidebar-foot {{ margin-top: auto; padding-top: 16px; font-size: 12px; color: var(--muted); }}

/* ------------------------------------------------------------- content */
.content {{ flex: 1; min-width: 0; max-width: 1100px; padding: 28px 36px 64px; }}
.page-head {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }}
.page-head h1 {{ margin: 0 0 6px; font-size: 28px; }}
.page-meta {{ margin: 0; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.page-desc {{ color: var(--muted); max-width: 72ch; white-space: pre-line; }}
.theme-toggle {{
  flex: none; background: var(--panel); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 6px 12px; cursor: pointer; font: inherit; font-size: 13px;
}}
.theme-toggle:hover {{ border-color: var(--accent); }}
.group-title {{ margin: 36px 0 12px; font-size: 20px; }}
.group-title .prefix {{ font-size: 14px; color: var(--muted); }}
.page-foot {{ margin-top: 48px; font-size: 13px; color: var(--muted); }}

/* ------------------------------------------------------------- cards */
.card {{
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  box-shadow: var(--shadow); padding: 20px 22px; margin: 16px 0;
}}
.card h2 {{ margin: 0 0 12px; font-size: 17px; }}
.card h4 {{
  margin: 20px 0 8px; font-size: 12px; font-weight: 700;
  letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted);
}}
.policy-rows {{ margin: 0; display: grid; gap: 8px; }}
.policy-rows > div {{ display: flex; gap: 12px; }}
.policy-rows dt {{ flex: none; width: 110px; font-weight: 600; color: var(--muted); }}
.policy-rows dd {{ margin: 0; }}

/* ------------------------------------------------------------- endpoint */
.ep-head {{ display: flex; flex-direction: column; gap: 10px; }}
.ep-name {{ margin: 0; font-size: 17px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
.anchor {{ margin-left: 8px; opacity: 0; font-weight: 400; }}
.endpoint:hover .anchor, .anchor:focus {{ opacity: 1; }}
.ep-route {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
.path {{ background: var(--code-bg); border-radius: 6px; padding: 4px 10px; overflow-x: auto; max-width: 100%; }}
.ph {{ color: var(--accent); font-weight: 600; }}

.chip {{
  display: inline-block; border-radius: 5px; padding: 2px 9px; font-size: 12px;
  font-weight: 700; letter-spacing: 0.04em; color: #fff; line-height: 1.5;
}}
.chip-sm {{ padding: 0 5px; font-size: 10px; min-width: 34px; text-align: center; }}
.method-get {{ background: #0e8a5f; }}
.method-post {{ background: #2563eb; }}
.method-put {{ background: #d97706; }}
.method-patch {{ background: #7c3aed; }}
.method-delete {{ background: #dc2626; }}
.method-head, .method-options {{ background: #647080; }}

.summary {{ margin: 12px 0 0; font-weight: 600; }}
.desc {{ margin: 6px 0 0; color: var(--muted); white-space: pre-line; }}
.notes {{ margin-top: 12px; display: flex; gap: 6px; flex-wrap: wrap; }}
.note {{
  font-size: 12px; background: var(--code-bg); color: var(--muted);
  border: 1px solid var(--border); border-radius: 999px; padding: 1px 9px;
}}

.tablewrap {{ overflow-x: auto; }}
table.params {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
table.params th {{
  text-align: left; font-size: 12px; color: var(--muted); font-weight: 600;
  padding: 6px 12px 6px 0; border-bottom: 1px solid var(--border);
}}
table.params td {{ padding: 7px 12px 7px 0; border-bottom: 1px solid var(--border); vertical-align: top; }}
table.params tr:last-child td {{ border-bottom: none; }}
.kind {{ font-size: 12px; color: var(--muted); }}
.req-dot {{
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--accent);
}}

.responses {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }}
.responses li {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }}
.status {{
  flex: none; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px; font-weight: 700; border-radius: 5px; padding: 1px 8px; min-width: 52px; text-align: center;
}}
.status.s2 {{ background: var(--status-2xx-bg); color: var(--status-2xx-fg); }}
.status.s3 {{ background: var(--status-3xx-bg); color: var(--status-3xx-fg); }}
.status.s4 {{ background: var(--status-4xx-bg); color: var(--status-4xx-fg); }}
.status.s5 {{ background: var(--status-5xx-bg); color: var(--status-5xx-fg); }}
.status.sd {{ background: var(--status-def-bg); color: var(--status-def-fg); }}
.detail {{ color: var(--muted); font-size: 13px; }}
.returns {{ margin: 12px 0 0; }}

details.schema {{ margin-top: 10px; border: 1px solid var(--border); border-radius: 8px; }}
details.schema summary {{ cursor: pointer; padding: 8px 12px; font-size: 14px; color: var(--muted); }}
details.schema[open] summary {{ border-bottom: 1px solid var(--border); }}
details.schema pre {{ padding: 12px; }}

.snippet {{ position: relative; background: var(--code-bg); border-radius: 8px; padding: 12px 14px; }}
.copy {{
  background: var(--panel); color: var(--muted); border: 1px solid var(--border);
  border-radius: 6px; padding: 2px 9px; font: inherit; font-size: 12px; cursor: pointer;
}}
.copy:hover {{ color: var(--text); border-color: var(--accent); }}
.copy.copied {{ color: var(--status-2xx-fg); border-color: var(--status-2xx-fg); }}
.copy.copied::after {{ content: " \\2713"; }}
.snippet .copy {{ position: absolute; top: 8px; right: 8px; }}

/* ------------------------------------------------------------- responsive + print */
@media (max-width: 860px) {{
  .layout {{ flex-direction: column; }}
  .sidebar {{ position: static; width: auto; height: auto; border-right: none; border-bottom: 1px solid var(--border); }}
  .content {{ padding: 20px 16px 48px; }}
}}
@media print {{
  .sidebar, .theme-toggle, .copy {{ display: none; }}
  .card {{ box-shadow: none; break-inside: avoid; }}
  body {{ background: #fff; }}
}}\
"""

_JS = """\
(function () {
  "use strict";
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var root = document.documentElement;
      var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      var current = root.getAttribute("data-theme") || (prefersDark ? "dark" : "light");
      root.setAttribute("data-theme", current === "dark" ? "light" : "dark");
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll(".copy"), function (el) {
    el.addEventListener("click", function () {
      var text;
      if (el.hasAttribute("data-copy")) {
        text = el.getAttribute("data-copy");
      } else {
        var block = el.closest(".snippet");
        var pre = block && block.querySelector("pre");
        text = pre ? pre.textContent : "";
      }
      var done = function () {
        el.classList.add("copied");
        setTimeout(function () { el.classList.remove("copied"); }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, done);
      } else {
        done();
      }
    });
  });
})();\
"""
