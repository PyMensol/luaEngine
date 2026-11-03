import json
import os
import sys
import time
import queue
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LuaLite"
CACHE = APP_DIR / "web-cache"
CACHE.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://cdnjs.cloudflare.com/ajax/libs"

ASSETS = {
    "codemirror.css": f"{BASE_URL}/codemirror/5.65.16/codemirror.min.css",
    "codemirror.js": f"{BASE_URL}/codemirror/5.65.16/codemirror.min.js",
    "lua-mode.js": f"{BASE_URL}/codemirror/5.65.16/mode/lua/lua.min.js",
    "searchcursor.js": f"{BASE_URL}/codemirror/5.65.16/addon/search/searchcursor.min.js",
    "search.js": f"{BASE_URL}/codemirror/5.65.16/addon/search/search.min.js",
    "dialog.js": f"{BASE_URL}/codemirror/5.65.16/addon/dialog/dialog.min.js",
    "dialog.css": f"{BASE_URL}/codemirror/5.65.16/addon/dialog/dialog.min.css",
    "show-hint.js": f"{BASE_URL}/codemirror/5.65.16/addon/hint/show-hint.min.js",
    "show-hint.css": f"{BASE_URL}/codemirror/5.65.16/addon/hint/show-hint.min.css",
    "matchbrackets.js": f"{BASE_URL}/codemirror/5.65.16/addon/edit/matchbrackets.min.js",
    "closebrackets.js": f"{BASE_URL}/codemirror/5.65.16/addon/edit/closebrackets.min.js",
    "active-line.js": f"{BASE_URL}/codemirror/5.65.16/addon/selection/active-line.min.js",
    "comment.js": f"{BASE_URL}/codemirror/5.65.16/addon/comment/comment.min.js",
    "jump-to-line.js": f"{BASE_URL}/codemirror/5.65.16/addon/search/jump-to-line.min.js",
    "fengari.js": "https://cdn.jsdelivr.net/npm/fengari-web@0.1.4/dist/fengari-web.min.js",
}

HELLO = '''-- hello from lualite
-- this is real lua 5.3 running on fengari

local function greet(name)
    print("hello", name)
end

greet("world")
print("lua says:", 2 ^ 10, string.format("%.3f", math.pi))
'''

SAMPLES = {
    "metatables.lua": '''local vec = {}
vec.__index = vec
vec.__add = function(a, b) return vec.new(a.x + b.x, a.y + b.y) end
vec.__tostring = function(v) return "(" .. v.x .. ", " .. v.y .. ")" end

function vec.new(x, y)
    return setmetatable({x = x, y = y}, vec)
end

local a = vec.new(1, 2)
local b = vec.new(3, 4)
print(a + b)
''',
    "coroutines.lua": '''local gen = coroutine.wrap(function()
    for i = 1, 5 do
        coroutine.yield(i * i)
    end
end)

for _ = 1, 5 do
    print("next square:", gen())
end
''',
    "game_api.lua": '''-- sandboxed game api demo (fake but familiar)
local players = game:GetService("Players")
local ws = workspace

local part = Instance.new("Part")
part.Name = "SpawnPad"
part.Position = Vector3.new(0, 10, 0)
part.Parent = ws

print("made:", part:GetFullName())
print("players online:", #players:GetPlayers())
wait(0.01)
print("tick:", math.floor(tick()))
''',
    "sorting.lua": '''local nums = {}
math.randomseed(42)
for i = 1, 12 do nums[i] = math.random(100) end
print("before:", table.concat(nums, " "))

for i = 1, #nums do
    for j = 1, #nums - i do
        if nums[j] > nums[j + 1] then
            nums[j], nums[j + 1] = nums[j + 1], nums[j]
        end
    end
end
print("after: ", table.concat(nums, " "))
''',
}

PRELUDE = r'''
local _printed = 0
local function emit(kind, ...)
    local parts = {}
    for i = 1, select("#", ...) do
        parts[i] = tostring(select(i, ...))
    end
    js.global.__emit(nil, kind, table.concat(parts, "\t"))
end

print = function(...) emit("out", ...) end
warn = function(...) emit("warn", ...) end

local _inst = {}
_inst.__index = function(self, k)
    local kids = rawget(self, "__children")
    if kids and kids[k] then return kids[k] end
    local cls = rawget(self, "__class")
    if _services[k] then return _services[k] end
    error(k .. " is not a valid member of " .. tostring(rawget(self, "__class") or "?"), 2)
end
_inst.__newindex = function(self, k, v)
    if k == "Parent" then
        rawset(self, "__parent", v)
        if v ~= nil then
            local kids = rawget(v, "__children") or {}
            rawset(v, "__children", kids)
            kids[rawget(self, "Name")] = self
        end
        return
    end
    if k == "Name" then
        local p = rawget(self, "__parent")
        if p and rawget(p, "__children") then
            rawget(p, "__children")[rawget(self, "Name")] = nil
            rawget(p, "__children")[v] = self
        end
        rawset(self, "Name", v)
        return
    end
    rawset(self, k, v)
end
_inst.__tostring = function(self) return rawget(self, "Name") or "Instance" end

local function makeinst(class, name)
    local o = setmetatable({__class = class, Name = name or class, __children = {}}, _inst)
    o.GetFullName = function(self)
        local chain, cur = {}, self
        while cur do
            table.insert(chain, 1, rawget(cur, "Name") or "?")
            cur = rawget(cur, "__parent")
        end
        return table.concat(chain, ".")
    end
    o.FindFirstChild = function(self, n) return rawget(self, "__children")[n] end
    o.GetChildren = function(self)
        local t = {}
        for _, v in pairs(rawget(self, "__children")) do t[#t + 1] = v end
        return t
    end
    o.Destroy = function(self)
        local p = rawget(self, "__parent")
        if p and rawget(p, "__children") then
            rawget(p, "__children")[rawget(self, "Name")] = nil
        end
        rawset(self, "__parent", nil)
    end
    return o
end

_services = {}
game = makeinst("DataModel", "game")
workspace = makeinst("Workspace", "Workspace")
script = makeinst("Script", "Script")
rawset(game, "__children", {Workspace = workspace})

_services.Workspace = workspace
_services.Players = makeinst("Players", "Players")
_services.Lighting = makeinst("Lighting", "Lighting")
_services.ReplicatedStorage = makeinst("ReplicatedStorage", "ReplicatedStorage")
local lp = makeinst("Player", "LocalPlayer")
rawget(_services.Players, "__children").LocalPlayer = lp
_services.Players.LocalPlayer = lp
_services.Players.GetPlayers = function(self)
    local t = {}
    for _, v in pairs(rawget(self, "__children")) do t[#t + 1] = v end
    return t
end
for k, v in pairs(_services) do rawget(game, "__children")[k] = v end

rawget(game, "__mt_getsvc") -- noop
game.GetService = function(self, name)
    return _services[name] or error("service " .. name .. " not found", 2)
end

Instance = {}
Instance.new = function(class, parent)
    local o = makeinst(class)
    if parent ~= nil then o.Parent = parent end
    return o
end

Vector3 = {}
Vector3.__index = Vector3
Vector3.__add = function(a, b) return Vector3.new(a.X + b.X, a.Y + b.Y, a.Z + b.Z) end
Vector3.__sub = function(a, b) return Vector3.new(a.X - b.X, a.Y - b.Y, a.Z - b.Z) end
Vector3.__mul = function(a, n) return Vector3.new(a.X * n, a.Y * n, a.Z * n) end
Vector3.__tostring = function(v) return v.X .. ", " .. v.Y .. ", " .. v.Z end
Vector3.new = function(x, y, z)
    return setmetatable({X = x or 0, Y = y or 0, Z = z or 0}, Vector3)
end

local _t0 = os.clock()
tick = function() return os.time() + os.clock() - _t0 end
wait = function(t) return t or 0.03 end
task = {wait = wait, spawn = function(f) f() end, defer = function(f) f() end}

Enum = setmetatable({}, {__index = function(_, cat)
    return setmetatable({}, {__index = function(_, item)
        return cat .. "." .. item
    end})
end})
'''

PLUGIN_LUA = r'''-- lualite studio bridge plugin
-- put this file in Roblox Studio's Plugins folder, restart studio,
-- then it polls the app automatically.

local HttpService = game:GetService("HttpService")
local BASE = "http://127.0.0.1:PORT_HERE"
local SID = HttpService:GenerateGUID():sub(1, 8)

local function post(path, data)
    return HttpService:PostAsync(BASE .. path,
        HttpService:JSONEncode(data), Enum.HttpContentType.ApplicationJson)
end

local function get(path)
    return HttpService:GetAsync(BASE .. path)
end

local function tree(node, depth)
    local kids = {}
    for _, c in ipairs(node:GetChildren()) do
        local e = {name = c.Name, class = c.ClassName}
        if c:IsA("LuaSourceContainer") or c:IsA("Script") or c:IsA("LocalScript") or c:IsA("ModuleScript") then
            local ok, src = pcall(function() return c.Source end)
            if ok then e.source = src end
        end
        if depth < 8 then e.children = tree(c, depth + 1) end
        kids[#kids + 1] = e
    end
    return kids
end

local function send_tree()
    local roots = {"Workspace", "ServerScriptService", "ReplicatedStorage",
                   "StarterPlayerScripts", "StarterGui", "ServerStorage"}
    local t = {}
    for _, name in ipairs(roots) do
        local ok, svc = pcall(function() return game:GetService(name) end)
        if ok and svc then
            t[#t + 1] = {name = svc.Name, class = svc.ClassName,
                         children = tree(svc, 0)}
        end
    end
    post("/studio/tree", {id = SID, tree = t})
end

post("/studio/hello", {id = SID,
    place = game.Name ~= "" and game.Name or "untitled place"})

while true do
    local ok, body = pcall(get, "/studio/poll?id=" .. SID)
    if ok and body and body ~= "" then
        local cmd = HttpService:JSONDecode(body)
        if cmd.type == "tree" then
            send_tree()
        elseif cmd.type == "execute" then
            local fn, err = loadstring(cmd.code)
            local out
            if fn then
                local ok2, res = pcall(fn)
                out = ok2 and tostring(res or "done") or ("err: " .. tostring(res))
            else
                out = "load err: " .. tostring(err)
            end
            post("/studio/result", {id = SID, text = out})
        end
    end
    task.wait(0.5)
end
'''

PAGE = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="/asset/codemirror.css">
<link rel="stylesheet" href="/asset/dialog.css">
<link rel="stylesheet" href="/asset/show-hint.css">
<style>
html, body { margin: 0; height: 100%; background: #1e1e1e; }
.CodeMirror {
    height: 100%; font: 13px Consolas, monospace;
    background: #1e1e1e; color: #d4d4d4;
}
.CodeMirror-gutters { background: #1e1e1e; border-right: 1px solid #333; }
.CodeMirror-linenumber { color: #858585; }
.CodeMirror-cursor { border-left: 1px solid #aeafad; }
.CodeMirror-selected { background: #264f78 !important; }
.CodeMirror-activeline-background { background: #0f0f0f; }
.CodeMirror-matchingbracket { background: #3b514d; color: #fff !important; }
.cm-keyword { color: #569cd6; }
.cm-string { color: #ce9178; }
.cm-number { color: #b5cea8; }
.cm-comment { color: #6a9955; font-style: italic; }
.cm-def { color: #dcdcaa; }
.cm-variable { color: #9cdcfe; }
.cm-variable-2 { color: #9cdcfe; }
.cm-builtin { color: #4ec9b0; }
.cm-operator { color: #d4d4d4; }
.cm-bracket { color: #ffd700; }
.cm-meta { color: #c586c0; }
.cm-attribute { color: #4ec9b0; }
.cm-tag { color: #569cd6; }
.cm-error-line { background: rgba(200, 60, 60, 0.18); }
.CodeMirror-widget-errline {
    border-bottom: 2px wavy #f14c4c;
}
.dialogdlg { position: absolute; left: 0; right: 0; background: #252526; z-index: 15;
    padding: 5px 10px; border-bottom: 1px solid #454545; font: 12px Segoe UI, sans-serif; }
.dialogdlg input { background: #3c3c3c; border: 1px solid #555; color: #eee;
    font: 12px Consolas, monospace; width: 260px; padding: 2px 6px; }
.CodeMirror-hints { background: #252526; border: 1px solid #454545; font: 12px Consolas, monospace; }
.CodeMirror-hint { color: #d4d4d4; }
li.CodeMirror-hint-active { background: #094771; color: #fff; }
</style>
</head>
<body>
<div id="editor"></div>
<script src="/asset/qwebchannel.js"></script>
<script src="/asset/codemirror.js"></script>
<script src="/asset/lua-mode.js"></script>
<script src="/asset/searchcursor.js"></script>
<script src="/asset/search.js"></script>
<script src="/asset/dialog.js"></script>
<script src="/asset/jump-to-line.js"></script>
<script src="/asset/show-hint.js"></script>
<script src="/asset/matchbrackets.js"></script>
<script src="/asset/closebrackets.js"></script>
<script src="/asset/active-line.js"></script>
<script src="/asset/comment.js"></script>
<script src="/asset/fengari.js"></script>
<script>
var bridge = null;
var running = false;
var stopFlag = false;
var WORDS = ("and break do else elseif end false for function goto if in local nil not or repeat return then true until while " +
    "print warn pairs ipairs next tostring tonumber type select setmetatable getmetatable rawget rawset rawequal rawlen " +
    "pcall xpcall error assert require collectgarbage string table math io os coroutine utf8 debug " +
    "game workspace script Instance Vector3 Enum tick wait task _G _VERSION").split(" ");

var suppressDirty = false;
var cm = CodeMirror(document.getElementById("editor"), {
    mode: "lua",
    theme: "default",
    lineNumbers: true,
    matchBrackets: true,
    autoCloseBrackets: true,
    styleActiveLine: true,
    indentUnit: 4,
    tabSize: 4,
    indentWithTabs: false,
    extraKeys: {
        "Ctrl-Space": "autocomplete",
        "Ctrl-F": "findPersistent",
        "Alt-F": "findPersistent",
        "Ctrl-G": "jumpToLine",
        "Ctrl-/": "toggleComment",
        "F5": function() { if (bridge) bridge.requestRun(); },
        "Tab": function(ed) {
            if (ed.somethingSelected()) ed.indentSelection("add");
            else ed.replaceSelection("    ", "end");
        }
    },
    hintOptions: { hint: function() {
        var cur = cm.getCursor(), tok = cm.getTokenAt(cur);
        var start = tok.start, end = cur.ch;
        var word = (tok.string || "").slice(0, end - start);
        var list = [];
        if (word.length > 0) {
            for (var i = 0; i < WORDS.length; i++)
                if (WORDS[i].indexOf(word) === 0) list.push(WORDS[i]);
        }
        return { list: list, from: CodeMirror.Pos(cur.line, start), to: CodeMirror.Pos(cur.line, end) };
    }}
});

cm.on("cursorActivity", function() {
    if (bridge) {
        var c = cm.getCursor();
        bridge.cursorMoved(c.line + 1, c.ch + 1);
    }
});
cm.on("change", function() { if (bridge && !suppressDirty) bridge.docChanged(); });
cm.on("inputRead", function(ed, ev) {
    if (ev.text && /[a-zA-Z_]/.test(ev.text[0])) {
        var cur = ed.getCursor(), tok = ed.getTokenAt(cur);
        if ((tok.string || "").length >= 2) {
            CodeMirror.commands.autocomplete(ed, null, { completeSingle: false });
        }
    }
});

var errMarks = [];
var lintTimer = null;
function lintNow() {
    var code = cm.getValue();
    if (!window.fengari || !code.trim()) { clearErr(); return; }
    var lua = fengari.lua, lauxlib = fengari.lauxlib, to_luastring = fengari.to_luastring;
    var L2 = lauxlib.luaL_newstate();
    var st = lauxlib.luaL_loadstring(L2, to_luastring(code));
    clearErr();
    if (st !== lua.LUA_OK) {
        var msg = fengari.to_jsstring(lua.lua_tostring(L2, -1));
        var m = msg.match(/:(\d+):/);
        var line = m ? parseInt(m[1], 10) - 1 : 0;
        errMarks.push(cm.addLineClass(line, "background", "cm-error-line"));
        if (bridge) bridge.lintResult(msg.replace(/^\[string "[^\]"]*"\]:/, "line"));
    } else {
        if (bridge) bridge.lintResult("");
    }
}
function clearErr() {
    for (var i = 0; i < errMarks.length; i++)
        cm.removeLineClass(errMarks[i], "background", "cm-error-line");
    errMarks = [];
}
cm.on("change", function() {
    if (lintTimer) clearTimeout(lintTimer);
    lintTimer = setTimeout(lintNow, 500);
});

function getCode() { return cm.getValue(); }
function setCode(s) {
    suppressDirty = true;
    cm.setValue(s || "");
    cm.clearHistory();
    cm.setCursor(0, 0);
    suppressDirty = false;
}
function focusEditor() { cm.focus(); }

var L = null;
function luaRun(code) {
    if (!window.fengari) { bridge.output("err", "fengari not loaded"); return; }
    var lua = fengari.lua, lauxlib = fengari.lauxlib, lualib = fengari.lualib, to_luastring = fengari.to_luastring;
    L = lauxlib.luaL_newstate();
    lualib.luaL_openlibs(L);
    lauxlib.luaL_requiref(L, to_luastring("js"), fengari.interop.luaopen_js, 1);
    lua.lua_setglobal(L, to_luastring("js"));
    var steps = 0;
    lua.lua_sethook(L, function(L_, ar) {
        steps++;
        if (stopFlag || steps > 200000000) {
            lauxlib.luaL_error(L_, to_luastring("script stopped"));
        }
    }, lua.LUA_MASKCOUNT, 1000);
    var status = lauxlib.luaL_dostring(L, to_luastring(window.__prelude || ""));
    if (status !== lua.LUA_OK) {
        bridge.output("err", "prelude: " + fengari.to_jsstring(lua.lua_tostring(L, -1)));
        return;
    }
    var t0 = performance.now();
    status = lauxlib.luaL_dostring(L, to_luastring(code));
    var ms = performance.now() - t0;
    if (status === lua.LUA_OK) {
        bridge.output("ok", "finished in " + ms.toFixed(1) + " ms");
    } else {
        bridge.output("err", fengari.to_jsstring(lua.lua_tostring(L, -1)));
    }
    stopFlag = false;
}

window.onerror = function(msg, src, line) {
    document.title = "err: " + msg + " @" + line;
    if (bridge) bridge.output("err", "js: " + msg + " (line " + line + ")");
};

try {
new QWebChannel(qt.webChannelTransport, function(ch) {
    bridge = ch.objects.bridge;
    window.__emit = function(kind, text) { bridge.output(kind, text); };
    bridge.setPrelude.connect(function(s) { window.__prelude = s; });
    bridge.runCode.connect(function(code) {
        running = true; stopFlag = false;
        try { luaRun(code); } catch (e) { bridge.output("err", String(e)); }
        running = false;
        bridge.runFinished();
    });
    bridge.stopRun.connect(function() { stopFlag = true; });
    bridge.getCode.connect(function() { bridge.gotCode(getCode()); });
    bridge.setCode.connect(function(s) { setCode(s); });
    bridge.editorReady();
});
} catch (e) {
    document.title = "chan: " + e.message;
}
</script>
</body>
</html>
"""


def fetch_asset(name):
    target = CACHE / name
    if target.exists() and target.stat().st_size > 100:
        return target.read_bytes()
    req = urllib.request.Request(
        ASSETS[name],
        headers={"User-Agent": "Mozilla/5.0 (lualite)"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    target.write_bytes(data)
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            body = PAGE.encode("utf-8")
            ctype = "text/html; charset=utf-8"
        elif path == "/asset/qwebchannel.js":
            from PySide6.QtCore import QFile, QIODevice
            f = QFile(":/qtwebchannel/qwebchannel.js")
            body = bytes(f.readAll().data()) if f.open(QIODevice.ReadOnly) else b""
            ctype = "application/javascript"
        elif path.startswith("/asset/"):
            name = path[len("/asset/"):]
            if name in ASSETS:
                try:
                    body = fetch_asset(name)
                except Exception as e:
                    self.send_error(502, "fetch failed: %s" % e)
                    return
                ctype = ("text/css" if name.endswith(".css") else "application/javascript")
            else:
                self.send_error(404)
                return
        elif path == "/studio/poll":
            sid = ""
            if "?" in self.path and "id=" in self.path:
                sid = self.path.split("id=")[1].split("&")[0]
            sess = self._session(sid)
            try:
                cmd = sess["queue"].get_nowait()
            except queue.Empty:
                self.send_response(204)
                self.end_headers()
                return
            body = json.dumps(cmd).encode()
            ctype = "application/json"
        elif path == "/studio/status":
            st = self.server.studio
            now = time.time()
            body = json.dumps({"sessions": [
                {"id": k, "place": s["place"],
                 "alive": now - s["seen"] < 15}
                for k, s in st["sessions"].items()]}).encode()
            ctype = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            data = {}
        st = self.server.studio

        if path == "/studio/hello":
            self._session(data.get("id", ""))["place"] = data.get("place", "?")
            out = {"ok": True}
        elif path == "/studio/tree":
            self._session(data.get("id", ""))["tree"] = data.get("tree", [])
            out = {"ok": True}
        elif path == "/studio/result":
            st["results"].put(data.get("text", ""))
            self._session(data.get("id", ""))
            out = {"ok": True}
        elif path == "/studio/execute":
            self._broadcast({"type": "execute", "code": data.get("code", "")},
                            data.get("target"))
            out = {"ok": True}
        elif path == "/studio/ask-tree":
            self._broadcast({"type": "tree"}, data.get("target"))
            out = {"ok": True}
        elif path == "/mcp":
            out = self._mcp(data)
        else:
            self.send_error(404)
            return
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _session(self, sid):
        sid = sid or "default"
        st = self.server.studio
        if sid not in st["sessions"]:
            st["sessions"][sid] = {
                "place": "?", "seen": 0.0, "tree": None, "queue": queue.Queue()}
        st["sessions"][sid]["seen"] = time.time()
        return st["sessions"][sid]

    def _broadcast(self, cmd, target=None):
        st = self.server.studio
        for sid, sess in st["sessions"].items():
            if target in (None, "", "*", sid):
                sess["queue"].put(dict(cmd))

    def _mcp(self, req):
        st = self.server.studio
        method = req.get("method", "")
        if method == "tools/list":
            return {"tools": [
                {"name": "studio_tree", "description": "roblox studio file tree (last sync)"},
                {"name": "studio_execute", "description": "queue lua code to run inside studio"},
                {"name": "editor_open", "description": "open a lua file in the editor"},
            ]}
        if method != "tools/call":
            return {"error": "unknown method"}
        name = req.get("params", {}).get("name", "")
        args = req.get("params", {}).get("arguments", {})
        if name == "studio_tree":
            trees = {k: s["tree"] for k, s in st["sessions"].items()
                     if s["tree"] is not None}
            return {"sessions": trees}
        if name == "studio_execute":
            self._broadcast({"type": "execute", "code": args.get("code", "")},
                            args.get("target"))
            return {"queued": True}
        if name == "editor_open":
            st["ui"].put({"type": "open", "name": args.get("name", "ai.lua"),
                          "content": args.get("content", "")})
            return {"queued": True}
        return {"error": "no such tool"}


def start_server():
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", 28350), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.studio = {
        "sessions": {}, "results": queue.Queue(), "ui": queue.Queue(),
    }
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    from PySide6.QtCore import Qt, QUrl, Signal, QObject, Slot, QTimer
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QSplitter, QTabWidget, QTextEdit,
        QFileDialog, QMessageBox, QToolBar, QWidget, QVBoxLayout, QLabel,
        QDockWidget, QTreeWidget, QTreeWidgetItem, QStyle,
    )
    from PySide6.QtGui import QAction, QFont, QKeySequence, QTextCursor, QColor
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebChannel import QWebChannel

    class Bridge(QObject):
        out = Signal(str, str)
        ready = Signal()
        finished = Signal()
        got = Signal(str)
        dirty = Signal()
        moved = Signal(int, int)
        wantRun = Signal()
        runCode = Signal(str)
        stopRun = Signal()
        getCode = Signal()
        setCode = Signal(str)
        setPrelude = Signal(str)

        @Slot(str, str)
        def output(self, kind, text):
            self.out.emit(kind, text)

        @Slot()
        def editorReady(self):
            self.ready.emit()

        @Slot()
        def runFinished(self):
            self.finished.emit()

        @Slot(str)
        def gotCode(self, code):
            self.got.emit(code)

        @Slot()
        def docChanged(self):
            self.dirty.emit()

        @Slot(int, int)
        def cursorMoved(self, l, c):
            self.moved.emit(l, c)

        @Slot()
        def requestRun(self):
            self.wantRun.emit()

        linted = Signal(str)

        @Slot(str)
        def lintResult(self, msg):
            self.linted.emit(msg)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("LuaLite")
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow, QDockWidget { background: #252526; }
        QMenuBar { background: #2d2d30; color: #ddd; border: 0; }
        QMenuBar::item { padding: 4px 10px; background: transparent; }
        QMenuBar::item:selected { background: #3e3e40; }
        QMenu { background: #1b1b1c; color: #ddd; border: 1px solid #454545; }
        QMenu::item:selected { background: #094771; }
        QToolBar { background: #2d2d30; border: 0; spacing: 3px; padding: 2px; }
        QToolButton { color: #ddd; background: transparent;
            padding: 4px 8px; border: 1px solid transparent; }
        QToolButton:hover { background: #3e3e40; border: 1px solid #555; }
        QToolButton:pressed { background: #094771; }
        QStatusBar { background: #007acc; color: #fff; }
        QStatusBar QLabel { color: #fff; padding: 0 8px; }
        QStatusBar::item { border: 0; }
        QDockWidget { color: #ccc; font-weight: bold; }
        QDockWidget::title { background: #252526; padding: 6px;
            border-bottom: 1px solid #1e1e1e; }
        QTreeWidget { background: #252526; color: #ccc; border: 0; }
        QTreeWidget::item:hover { background: #2a2d2e; }
        QTreeWidget::item:selected { background: #094771; color: #fff; }
        QSplitter::handle { background: #1e1e1e; }
    """)

    srv = start_server()
    port = srv.server_address[1]

    class EditorTab:
        def __init__(self, title="untitled.lua", text=HELLO, path=None):
            self.title = title
            self.text = text
            self.path = path
            self.dirty = False

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("LuaLite")
            self.setWindowIcon(self._lua_icon())
            self.resize(1100, 720)
            self.tabs = [EditorTab()]
            self.cur = 0
            self.ready = False
            self.running = False

            self.view = QWebEngineView()
            self.page = self.view.page()
            self.channel = QWebChannel(self.page)
            self.bridge = Bridge()
            self.channel.registerObject("bridge", self.bridge)
            self.page.setWebChannel(self.channel)

            self.output = QTextEdit()
            self.output.setReadOnly(True)
            self.output.setFont(QFont("Consolas", 10))
            self.output.setStyleSheet(
                "QTextEdit { background:#1e1e1e; color:#d4d4d4; border:1px solid #3e3e42; }"
            )

            split = QSplitter(Qt.Vertical)
            split.addWidget(self.view)
            outbox = QWidget()
            outlay = QVBoxLayout(outbox)
            outlay.setContentsMargins(0, 0, 0, 0)
            outlay.setSpacing(0)
            hdr = QLabel("Output")
            hdr.setStyleSheet("background:#2d2d30;color:#ccc;padding:3px 10px;"
                              "font:11px 'Segoe UI';border-bottom:1px solid #1e1e1e;")
            outlay.addWidget(hdr)
            outlay.addWidget(self.output)
            split.addWidget(outbox)
            split.setSizes([520, 160])
            self.setCentralWidget(split)

            self.status = self.statusBar()
            self.pos_lbl = QLabel("Ln 1, Col 1")
            self.vm_lbl = QLabel("Lua 5.3 (fengari)")
            self.lint_lbl = QLabel("")
            self.status.addPermanentWidget(self.vm_lbl)
            self.status.addPermanentWidget(self.lint_lbl)
            self.status.addPermanentWidget(self.pos_lbl)
            self.status.showMessage("loading editor...")

            self._menus()
            self._toolbar()
            self._sidebar()

            self.studio_root = None
            self.studio_lbl = QLabel("studio: not connected")
            self.status.addPermanentWidget(self.studio_lbl)
            self.poll = QTimer(self)
            self.poll.timeout.connect(self._studio_tick)
            self.poll.start(700)

            self.bridge.out.connect(self._on_output)
            self.bridge.ready.connect(self._on_ready)
            self.bridge.finished.connect(self._on_finished)
            self.bridge.got.connect(self._got_code)
            self.bridge.moved.connect(
                lambda l, c: self.pos_lbl.setText("Ln %d, Col %d" % (l, c)))
            self.bridge.dirty.connect(self._on_dirty)
            self.bridge.wantRun.connect(self.run)
            self.bridge.linted.connect(
                lambda m: self.lint_lbl.setText(m[:60] if m else ""))

            self.view.load(QUrl("http://127.0.0.1:%d/index.html" % port))

        def _lua_icon(self):
            from PySide6.QtGui import QPixmap, QPainter, QBrush, QIcon
            px = QPixmap(64, 64)
            px.fill(Qt.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QBrush(QColor("#00007d")))
            p.setPen(Qt.NoPen)
            p.drawEllipse(4, 4, 56, 56)
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawEllipse(34, 16, 20, 20)
            p.setBrush(QBrush(QColor("#00007d")))
            p.drawEllipse(30, 12, 20, 20)
            p.setBrush(QBrush(QColor("#ffffff")))
            p.drawEllipse(44, 40, 8, 8)
            p.end()
            return QIcon(px)

        def _icon(self, kind):
            from PySide6.QtGui import QPixmap, QPainter, QPen, QIcon, QPolygon
            from PySide6.QtCore import QPoint
            px = QPixmap(16, 16)
            px.fill(Qt.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.Antialiasing)
            if kind == "play":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#89d185"))
                p.drawPolygon(QPolygon([QPoint(4, 3), QPoint(13, 8), QPoint(4, 13)]))
            elif kind == "stop":
                p.setPen(Qt.NoPen)
                p.setBrush(QColor("#f48771"))
                p.drawRect(4, 4, 8, 8)
            elif kind == "monitor":
                p.setPen(QPen(QColor("#ddd"), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawRect(2, 3, 12, 8)
                p.drawLine(8, 11, 8, 14)
                p.drawLine(5, 14, 11, 14)
            p.end()
            return QIcon(px)

        def _menus(self):
            mb = self.menuBar()
            fm = mb.addMenu("&File")
            self._act(fm, "New", self.new_tab, "Ctrl+N")
            self._act(fm, "Open...", self.open_file, "Ctrl+O")
            self._act(fm, "Save", self.save, "Ctrl+S")
            self._act(fm, "Save As...", self.save_as, "Ctrl+Shift+S")
            fm.addSeparator()
            for name in SAMPLES:
                fm.addAction("sample: " + name,
                             (lambda n=name: lambda: self.load_sample(n))())
            fm.addSeparator()
            self._act(fm, "Exit", self.close, "Alt+F4")

            rm = mb.addMenu("&Run")
            self._act(rm, "Run (F5)", self.run, "F5")
            self._act(rm, "Stop", self.stop, "Shift+F5")
            self._act(rm, "Clear Output", self.output.clear, "Ctrl+L")

            sm = mb.addMenu("&Studio")
            self._act(sm, "Install Studio plugin...", self.install_plugin)
            self._act(sm, "Refresh Studio tree", self.ask_tree)
            self._act(sm, "Run script in Studio", self.run_in_studio)

            hm = mb.addMenu("&Help")
            self._act(hm, "About", lambda: QMessageBox.information(
                self, "LuaLite",
                "lua editor + real lua 5.3 vm (fengari) + codemirror\n"
                "game api is a sandbox stub, nothing leaves your pc"))

        def _act(self, menu, text, fn, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        def _toolbar(self):
            tb = QToolBar("main")
            tb.setMovable(False)
            self.addToolBar(tb)
            st = self.style()
            for label, fn, tip, icon in [
                ("New", self.new_tab, "new file", QStyle.SP_FileIcon),
                ("Open", self.open_file, "open .lua", QStyle.SP_DialogOpenButton),
                ("Save", self.save, "save", QStyle.SP_DialogSaveButton),
                ("|", None, "", None),
                ("Run", self.run, "run the script (F5)", "play"),
                ("Studio", self.run_in_studio,
                 "send the script to roblox studio", "monitor"),
                ("Stop", self.stop, "stop a running script", "stop"),
                ("Clear", self.output.clear,
                 "clear output", QStyle.SP_TrashIcon),
            ]:
                if label == "|":
                    tb.addSeparator()
                    continue
                ic = (self._icon(icon) if isinstance(icon, str)
                      else self.style().standardIcon(icon or QStyle.SP_FileIcon))
                a = QAction(ic, label, self)
                a.setToolTip(tip)
                a.triggered.connect(fn)
                tb.addAction(a)
            self.tabbar = QTabWidget()
            self.tabbar.setTabsClosable(True)
            self.tabbar.tabCloseRequested.connect(self.close_tab)
            self.tabbar.currentChanged.connect(self.switch_tab)
            self.tabbar.setMaximumHeight(26)
            self.tabbar.setDocumentMode(True)
            self.tabbar.setStyleSheet(
                "QTabBar::tab { padding: 3px 14px; background:#2d2d30; color:#ccc; "
                "border:1px solid #3e3e42; } QTabBar::tab:selected { background:#1e1e1e; "
                "color:#fff; border-bottom-color:#007acc; }"
                "QTabWidget::pane { border: 0; }"
            )
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)
            lay.addWidget(self.tabbar)
            lay.addWidget(self.centralWidget())
            self.setCentralWidget(w)

            self.tabbar.addTab(QLabel(""), "untitled.lua")

        def _sidebar(self):
            self.tree = QTreeWidget()
            self.tree.setHeaderHidden(True)
            self.tree.itemDoubleClicked.connect(self._tree_open)
            self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
            self.tree.customContextMenuRequested.connect(self._tree_menu)
            dock = QDockWidget("Explorer", self)
            dock.setObjectName("explorer")
            dock.setWidget(self.tree)
            self.addDockWidget(Qt.LeftDockWidgetArea, dock)
            self._fill_files()

        def _ws_dir(self):
            d = Path(__file__).parent / "scripts"
            d.mkdir(exist_ok=True)
            return d

        def _tree_menu(self, pos):
            from PySide6.QtWidgets import QMenu, QInputDialog
            item = self.tree.itemAt(pos)
            menu = QMenu(self)
            is_ws = item is None or self._in_workspace(item)
            if is_ws:
                menu.addAction("new file", lambda: self._mk_file())
                menu.addAction("new folder", lambda: self._mk_folder())
            if item and isinstance(item.data(0, Qt.UserRole), str):
                menu.addSeparator()
                menu.addAction("delete", lambda: self._del_file(item))
            menu.addAction("refresh", lambda: self._fill_files())
            menu.exec(self.tree.viewport().mapToGlobal(pos))

        def _in_workspace(self, item):
            while item.parent():
                item = item.parent()
            return item.text(0) == "workspace"

        def _mk_file(self):
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "new file", "name:")
            if not ok or not name.strip():
                return
            name = name.strip()
            if "." not in name:
                name += ".lua"
            p = self._ws_dir() / name
            p.write_text("", encoding="utf-8")
            self._fill_files()
            self._open_tab(p.name, "", str(p))

        def _mk_folder(self):
            from PySide6.QtWidgets import QInputDialog
            name, ok = QInputDialog.getText(self, "new folder", "name:")
            if ok and name.strip():
                (self._ws_dir() / name.strip()).mkdir(exist_ok=True)
                self._fill_files()

        def _del_file(self, item):
            p = Path(item.data(0, Qt.UserRole))
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                import shutil
                shutil.rmtree(p)
            self._fill_files()

        def _fill_files(self):
            scripts = self._ws_dir()
            st = self.style()
            old = self.tree.findItems("workspace", Qt.MatchExactly, 0)
            if old:
                self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(old[0]))
            root = QTreeWidgetItem(self.tree, ["workspace"])
            root.setIcon(0, st.standardIcon(QStyle.SP_DirIcon))
            self.tree.insertTopLevelItem(0, root)
            def walk(parent, d):
                for p in sorted(d.iterdir()):
                    if p.is_dir():
                        it = QTreeWidgetItem(parent, [p.name])
                        it.setIcon(0, st.standardIcon(QStyle.SP_DirIcon))
                        it.setData(0, Qt.UserRole, str(p))
                        walk(it, p)
                    elif p.suffix in (".lua", ".txt"):
                        it = QTreeWidgetItem(parent, [p.name])
                        it.setIcon(0, st.standardIcon(QStyle.SP_FileIcon))
                        it.setData(0, Qt.UserRole, str(p))
            walk(root, scripts)
            root.setExpanded(True)

        def _tree_open(self, item, col):
            path = item.data(0, Qt.UserRole)
            if isinstance(path, str) and Path(path).is_file():
                text = Path(path).read_text(encoding="utf-8", errors="replace")
                self._open_tab(Path(path).name, text, path)
                return
            src = item.data(0, Qt.UserRole + 1)
            if isinstance(src, str):
                self._open_tab(item.text(0) + " [studio]", src)

        def _open_tab(self, title, text, path=None):
            self.tabs.append(EditorTab(title, text, path))
            self.tabbar.addTab(QLabel(""), title)
            self.tabbar.setCurrentIndex(len(self.tabs) - 1)

        def _studio_tick(self):
            st = srv.studio
            while True:
                try:
                    text = st["results"].get_nowait()
                except queue.Empty:
                    break
                self._println("ok", "[studio] " + str(text))
            while True:
                try:
                    act = st["ui"].get_nowait()
                except queue.Empty:
                    break
                if act.get("type") == "open":
                    p = self._ws_dir() / Path(act["name"]).name
                    p.write_text(act.get("content", ""), encoding="utf-8")
                    self._fill_files()
                    self._open_tab(p.name, act.get("content", ""), str(p))
            now = time.time()
            alive = {k: s for k, s in st["sessions"].items()
                     if now - s["seen"] < 15}
            names = [s["place"] for s in alive.values()]
            self.studio_lbl.setText(
                "studio: %s" % ", ".join(names) if names
                else "studio: not connected")
            ver = sum(id(s["tree"]) for s in st["sessions"].values())
            if ver != getattr(self, "_tree_ver", -1):
                self._tree_ver = ver
                self._fill_studio(alive)

        def _fill_studio(self, sessions):
            if self.studio_root is not None:
                self.tree.takeTopLevelItem(
                    self.tree.indexOfTopLevelItem(self.studio_root))
                self.studio_root = None
            if not sessions:
                return
            st = self.style()
            self.studio_root = QTreeWidgetItem(self.tree, ["roblox studio"])
            self.studio_root.setIcon(
                0, st.standardIcon(QStyle.SP_ComputerIcon))
            def fill(node, kids):
                for e in kids:
                    it = QTreeWidgetItem(node, [e.get("name", "?")])
                    if e.get("source") is not None:
                        it.setData(0, Qt.UserRole + 1, e["source"])
                        it.setIcon(0, st.standardIcon(QStyle.SP_FileIcon))
                    elif e.get("children"):
                        it.setIcon(0, st.standardIcon(QStyle.SP_DirIcon))
                    if e.get("children"):
                        fill(it, e["children"])
            for sid, sess in sessions.items():
                place = QTreeWidgetItem(
                    self.studio_root, ["%s (%s)" % (sess["place"], sid)])
                place.setIcon(0, st.standardIcon(QStyle.SP_ComputerIcon))
                if sess["tree"]:
                    fill(place, sess["tree"])
            self.studio_root.setExpanded(True)

        def _to_studio(self, cmd):
            now = time.time()
            n = 0
            for sess in srv.studio["sessions"].values():
                if now - sess["seen"] < 15:
                    sess["queue"].put(dict(cmd))
                    n += 1
            if n == 0:
                self._println("warn", "[studio] nothing connected - "
                                      "open studio with the plugin")

        def ask_tree(self):
            self._to_studio({"type": "tree"})
            self.status.showMessage("asked studio for the file tree...", 3000)

        def run_in_studio(self):
            self._pull_code(
                lambda c: self._to_studio({"type": "execute", "code": c}))
            self._println("warn", "[studio] queued, waiting for plugin...")

        def install_plugin(self):
            src = PLUGIN_LUA.replace("PORT_HERE", str(srv.server_address[1]))
            plugins = Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "Plugins"
            plugins.mkdir(parents=True, exist_ok=True)
            target = plugins / "lualite_bridge.lua"
            target.write_text(src, encoding="utf-8")
            QMessageBox.information(
                self, "studio plugin",
                "wrote %s\n\nrestart roblox studio and it should connect. "
                "note: the port changes each launch, so reinstall the plugin "
                "if studio cant reach the app." % target)

        def _on_ready(self):
            self.ready = True
            self.bridge.setPrelude.emit(PRELUDE)
            self.bridge.setCode.emit(self.tabs[0].text)
            self.status.showMessage("ready - write lua and press F5")

        def _on_dirty(self):
            tab = self.tabs[self.cur]
            if not tab.dirty:
                tab.dirty = True
                self.tabbar.setTabText(self.cur, tab.title + " *")

        def _pull_code(self, then=None):
            self._after_pull = then
            self.bridge.getCode.emit()

        def _got_code(self, code):
            self.tabs[self.cur].text = code
            if self._after_pull:
                fn = self._after_pull
                self._after_pull = None
                fn(code)

        def new_tab(self):
            self.tabs.append(EditorTab(title="untitled%d.lua" % len(self.tabs), text=""))
            self.tabbar.addTab(QLabel(""), self.tabs[-1].title)
            self.tabbar.setCurrentIndex(len(self.tabs) - 1)

        def close_tab(self, i):
            if len(self.tabs) == 1:
                self.tabs[0] = EditorTab()
                self.tabbar.setTabText(0, "untitled.lua")
                self.bridge.setCode.emit(HELLO)
                return
            del self.tabs[i]
            self.tabbar.removeTab(i)
            self.cur = self.tabbar.currentIndex()

        def switch_tab(self, i):
            if i < 0 or i >= len(self.tabs):
                return
            def go(code):
                self.tabs[self.cur].text = code
                self.cur = i
                self.bridge.setCode.emit(self.tabs[i].text)
            if self.ready:
                self._pull_code(go)

        def open_file(self):
            p, _ = QFileDialog.getOpenFileName(
                self, "Open lua file", "", "Lua (*.lua *.txt);;All (*)")
            if not p:
                return
            text = Path(p).read_text(encoding="utf-8", errors="replace")
            self.tabs.append(EditorTab(Path(p).name, text, p))
            self.tabbar.addTab(QLabel(""), Path(p).name)
            self.tabbar.setCurrentIndex(len(self.tabs) - 1)

        def load_sample(self, name):
            self.tabs.append(EditorTab(name, SAMPLES[name]))
            self.tabbar.addTab(QLabel(""), name)
            self.tabbar.setCurrentIndex(len(self.tabs) - 1)

        def save(self):
            tab = self.tabs[self.cur]
            if not tab.path:
                return self.save_as()
            self._pull_code(lambda code: self._write(tab, tab.path, code))

        def save_as(self):
            p, _ = QFileDialog.getSaveFileName(
                self, "Save lua file", self.tabs[self.cur].title, "Lua (*.lua)")
            if not p:
                return
            tab = self.tabs[self.cur]
            tab.path = p
            tab.title = Path(p).name
            self.tabbar.setTabText(self.cur, tab.title)
            self._pull_code(lambda code: self._write(tab, p, code))

        def _write(self, tab, path, code):
            Path(path).write_text(code, encoding="utf-8")
            tab.text = code
            tab.dirty = False
            self.tabbar.setTabText(self.cur, tab.title)
            self.status.showMessage("saved %s" % path, 3000)

        def run(self):
            if self.running:
                return
            self.running = True
            self._sep("run")
            self._pull_code(lambda code: self.bridge.runCode.emit(code))

        def stop(self):
            if self.running:
                self.bridge.stopRun.emit()
                self._println("out", "-- stop requested --")

        def _on_finished(self):
            self.running = False

        def _on_output(self, kind, text):
            self._println(kind, text)

        def _println(self, kind, text):
            colors = {"out": "#d4d4d4", "warn": "#d7ba7d",
                      "err": "#f48771", "ok": "#6a9955"}
            c = QColor(colors.get(kind, "#d4d4d4"))
            cur = self.output.textCursor()
            cur.movePosition(QTextCursor.End)
            fmt = cur.charFormat()
            fmt.setForeground(c)
            cur.setCharFormat(fmt)
            cur.insertText(text + "\n")
            self.output.setTextCursor(cur)
            self.output.ensureCursorVisible()

        def _sep(self, label):
            cur = self.output.textCursor()
            cur.movePosition(QTextCursor.End)
            fmt = cur.charFormat()
            fmt.setForeground(QColor("#555"))
            cur.setCharFormat(fmt)
            cur.insertText("-- %s %s --\n" % (label, time.strftime("%H:%M:%S")))

        def closeEvent(self, e):
            srv.shutdown()
            super().closeEvent(e)

    win = Window()
    win.show()
    if "--shot" in sys.argv:
        def snap():
            win.grab().toImage().save("shot.png")
            app.quit()
        QTimer.singleShot(int(sys.argv[sys.argv.index("--shot") + 1]) * 1000
                          if len(sys.argv) > sys.argv.index("--shot") + 1
                          and sys.argv[sys.argv.index("--shot") + 1].isdigit()
                          else 12000, snap)
    code = app.exec()
    srv.shutdown()
    sys.exit(code)


if __name__ == "__main__":
    main()
