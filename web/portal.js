/* mcp-hub portal: plain-English simulator + sign-in/connect. Standalone, no libraries. */
(() => {
  // ---- Settings: change if Keycloak, the server or your sandbox repo live elsewhere ----
  const ISSUER = "http://localhost:8080/realms/mcp-hub";
  const CLIENT_ID = "mcp-client";
  const MCP_URL = "http://localhost:8000/mcp";
  const SANDBOX = "you/mcp-sandbox";

  // ---- Policy mirror of src/mcp_hub/rbac.py ----
  const TOOLS = {
    github_list_issues: "read GitHub issues", github_get_pull_request: "read a pull request",
    jira_search_issues: "search Jira tickets", slack_list_channels: "list Slack channels",
    github_create_issue: "create a GitHub issue", jira_create_issue: "create a Jira ticket",
    jira_transition_issue: "move a Jira ticket", slack_post_message: "post to Slack",
    bulk_create_issues_from_csv: "bulk-create issues",
  };
  const V = ["github_list_issues", "github_get_pull_request", "jira_search_issues", "slack_list_channels"];
  const D = [...V, "github_create_issue", "jira_create_issue", "jira_transition_issue", "slack_post_message"];
  const ROLES = { viewer: V, developer: D, admin: [...D, "bulk_create_issues_from_csv"] };
  const PEOPLE = [["alice", "viewer"], ["bob", "developer"], ["carol", "admin"]];
  const ASKS = [
    ["List the open issues in our sandbox repo", "github_list_issues", SANDBOX],
    ["Create an issue titled “test”", "github_create_issue", SANDBOX],
    ["Post a message to the team Slack channel", "slack_post_message", null],
    ["Create 200 issues from a spreadsheet", "bulk_create_issues_from_csv", SANDBOX],
    ["List issues in someone else's repo (evil/other)", "github_list_issues", "evil/other"],
  ];
  const $ = (id) => document.getElementById(id);
  if (!$("pt-who")) return;
  let role = "viewer", person = "alice", askIdx = null, session = null, tick;

  function decide(r, tool, target) {
    if (!ROLES[r].includes(tool)) return { ok: false, code: "FORBIDDEN", title: "Blocked: not allowed for this role",
      why: `The ${r} role can't ${TOOLS[tool]}. The server refuses anything a role hasn't been given.` };
    if (target && target !== SANDBOX) return { ok: false, code: "OUT_OF_SCOPE", title: "Blocked: not on the approved list",
      why: `“${target}” isn't an approved place. Even admins can only reach the approved sandbox.` };
    return { ok: true, code: "ALLOWED", title: "Allowed",
      why: `The ${r} role can do this and the target is approved. The action runs and is saved in the audit log.` };
  }
  function show() {
    const box = $("pt-result");
    box.innerHTML = "";
    if (askIdx === null) {
      box.className = "pt-result";
      const p = document.createElement("p"); p.textContent = "Choose a request on the left to see whether it is allowed."; box.appendChild(p);
      return;
    }
    const [q, tool, target] = ASKS[askIdx], d = decide(role, tool, target);
    box.className = "pt-result " + (d.ok ? "is-ok" : "is-no");
    const mk = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; e.textContent = x; box.appendChild(e); };
    mk("div", "pt-verdict", (d.ok ? "✅ " : "⛔ ") + d.title);
    mk("p", "", `${person} asks the AI: “${q}”`);
    mk("p", "", d.why);
    mk("span", "pt-tag", `${d.code} · ${tool}`);
  }
  function render() {
    $("pt-who").innerHTML = "";
    PEOPLE.forEach(([n, r]) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pt-chip"; b.setAttribute("aria-pressed", n === person);
      b.textContent = n[0].toUpperCase() + n.slice(1);
      const s = document.createElement("small"); s.textContent = r; b.appendChild(s);
      b.onclick = () => { person = n; role = r; render(); };
      $("pt-who").appendChild(b);
    });
    $("pt-asks").innerHTML = "";
    ASKS.forEach(([q], i) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "pt-ask"; b.setAttribute("aria-pressed", i === askIdx); b.textContent = q;
      b.onclick = () => { askIdx = i; render(); };
      $("pt-asks").appendChild(b);
    });
    show();
  }

  // ---- Sign-in: browser login (authorization code + PKCE) ----
  const b64 = (b) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const REDIR = location.origin + location.pathname;
  const LOCAL = ["localhost", "127.0.0.1"].includes(location.hostname);
  async function login() {
    if (!LOCAL) return;
    const v = b64(crypto.getRandomValues(new Uint8Array(32))), st = b64(crypto.getRandomValues(new Uint8Array(16)));
    sessionStorage.setItem("pkce", JSON.stringify({ v, st }));
    const ch = b64(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v)));
    location.href = ISSUER + "/protocol/openid-connect/auth?" + new URLSearchParams({
      client_id: CLIENT_ID, response_type: "code", redirect_uri: REDIR, code_challenge: ch, code_challenge_method: "S256", state: st });
  }
  async function finishLogin() {
    const p = new URLSearchParams(location.search);
    if (!p.has("code") && !p.has("error")) return;
    history.replaceState(null, "", REDIR + "#connect");
    $("connect").scrollIntoView();
    const saved = JSON.parse(sessionStorage.getItem("pkce") || "null"); sessionStorage.removeItem("pkce");
    try {
      if (p.has("error")) throw new Error(p.get("error_description") || p.get("error"));
      if (!saved || saved.st !== p.get("state")) throw new Error("Sign-in could not be verified. Please try again.");
      const r = await fetch(ISSUER + "/protocol/openid-connect/token", { method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ grant_type: "authorization_code", client_id: CLIENT_ID, code: p.get("code"), redirect_uri: REDIR, code_verifier: saved.v }) });
      if (!r.ok) throw new Error("The login server refused the sign-in (" + r.status + ").");
      const tok = (await r.json()).access_token;
      const c = JSON.parse(atob(tok.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
      const rl = ((c.realm_access && c.realm_access.roles) || []).filter((x) => ROLES[x]);
      session = { tok, exp: c.exp, name: c.preferred_username || c.name || "you",
        role: rl.includes("admin") ? "admin" : rl.includes("developer") ? "developer" : rl.includes("viewer") ? "viewer" : null };
      if (session.role) { role = session.role; person = session.name; render();
        const sel = $("sim-user"); if (sel) sel.value = { viewer: "alice", developer: "bob", admin: "carol" }[role]; }
      signedIn();
    } catch (e) {
      $("pt-err").textContent = e instanceof TypeError
        ? "Can't reach the login server. Start it with “docker compose up -d”, or use the simulator above."
        : e.message;
      $("pt-err").hidden = false;
    }
  }
  function signedIn() {
    $("pt-out").hidden = true; $("pt-in").hidden = false;
    $("pt-name").textContent = session.name; $("pt-role").textContent = session.role || "no role assigned";
    $("pt-count").textContent = session.role
      ? `Claude can use ${ROLES[session.role].length} of 9 tools on your behalf. See the full list under Access.`
      : "Your account has no role yet, so the server will refuse every request. Ask an admin to assign one.";
    $("pt-cmd").textContent = `claude mcp add --transport http mcp-hub ${MCP_URL} \\\n  --header "Authorization: Bearer ${session.tok.slice(0, 12)}…(hidden)"`;
    const upd = () => { const s = session.exp - Math.floor(Date.now() / 1000);
      $("pt-timer").textContent = s > 0
        ? `This sign-in is valid for another ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}. After that, sign in again for a fresh command.`
        : "This sign-in has expired. Sign out and in again for a fresh command."; };
    upd(); clearInterval(tick); tick = setInterval(upd, 1000);
  }
  $("pt-login").onclick = login;
  $("pt-logout").onclick = () => { session = null; clearInterval(tick); $("pt-in").hidden = true; $("pt-out").hidden = false; };
  $("pt-copy").onclick = async (e) => {
    const full = `claude mcp add --transport http mcp-hub ${MCP_URL} --header "Authorization: Bearer ${session.tok}"`;
    try { await navigator.clipboard.writeText(full); e.target.textContent = "Copied ✓"; } catch { e.target.textContent = "Copy blocked by browser"; }
    setTimeout(() => (e.target.textContent = "Copy command"), 2000);
  };
  if (!LOCAL) {
    $("pt-login").disabled = true;
    $("pt-login").textContent = "Sign in (local setup only)";
    $("pt-err").textContent = "You're viewing the hosted demo. Live sign-in is switched off here because the login server (Keycloak) runs on a developer's own computer. Use the simulator above, or clone the repo and run it locally to try real sign-in.";
    $("pt-err").hidden = false;
  }
  render(); finishLogin();
})();