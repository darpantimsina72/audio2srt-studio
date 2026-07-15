-- audio_to_srt.lua  —  Audio to SRT  (Workspace -> Scripts -> audio_to_srt)
-- Transcribes the chosen audio track via ElevenLabs and imports subtitles:
-- styled SRT (per-speaker colours) or animated Text+ captions, plus an
-- optional silence-cut pass (tightened copy + matching SRT).
-- Cross-platform: Mac + Windows. All GUI is Python (loader.pyw / dialog.py).

local IS_WINDOWS = package.config:sub(1,1) == "\\"
local SEP = IS_WINDOWS and "\\" or "/"

-- Project folder is written by setup.command / setup.bat into a config file.
-- No manual editing needed — just run the setup script once.
local function get_project_dir()
    local home = os.getenv("HOME") or ""
    local userprofile = os.getenv("USERPROFILE") or home
    local config = IS_WINDOWS
        and (userprofile .. "\\.audio_to_srt_path")
        or  (home .. "/.audio_to_srt_path")
    local f = io.open(config)
    if f then
        local p = f:read("*l"); f:close()
        if p and p:match("%S") then return p:match("^%s*(.-)%s*$") end
    end
    return IS_WINDOWS
        and (userprofile .. "\\DaVinci-Audio2SRT")
        or  (home .. "/DaVinci-Audio2SRT")
end

local PROJECT_DIR = get_project_dir()

local TRANSCRIBE_PY = PROJECT_DIR .. SEP .. "transcribe.py"
local SILENCE_PY    = PROJECT_DIR .. SEP .. "silence.py"
local DIALOG_PY     = PROJECT_DIR .. SEP .. "dialog.py"
local LOADER_PY     = PROJECT_DIR .. SEP .. "loader.pyw"
local LOG_FILE      = PROJECT_DIR .. SEP .. "logs" .. SEP .. "audio_to_srt.log"

-- cmd.exe's /c parser can strip the opening quote of a command line that
-- starts with a quoted path ("C:\Program Files\...") and then split on the
-- space. Wrapping the whole line in one extra pair of quotes disables that
-- (documented in `cmd /?`). io.popen/os.execute both go through cmd /c.
local function run_shell(cmd)
    if IS_WINDOWS then cmd = '"' .. cmd .. '"' end
    return os.execute(cmd)
end

-- os.tmpname() on Windows returns a name at the drive root (e.g. "\s1a2."),
-- which is usually not writable without admin rights; re-root it into %TEMP%.
local function temp_path(suffix)
    local base = os.tmpname()
    if IS_WINDOWS and base:sub(1, 1) == "\\" then
        local tmp = os.getenv("TEMP") or os.getenv("TMP") or "."
        base = tmp .. base
    end
    return base .. suffix
end

-- Run the candidate interpreter for real: on Windows a "python" found on
-- PATH can be the Microsoft Store alias stub, which exists but only prints
-- an install hint and exits non-zero.
local function python_works(cmd)
    local check
    if IS_WINDOWS then
        check = string.format('%s -c "import sys" >nul 2>nul', cmd)
    else
        check = string.format('%s -c "import sys" >/dev/null 2>/dev/null', cmd)
    end
    local ok = os.execute(check)
    return (ok == true) or (type(ok) == "number" and ok == 0)
end

local function find_python()
    -- setup.bat / setup.command save the interpreter they installed
    -- elevenlabs into — always prefer that exact one.
    local home = os.getenv("HOME") or ""
    local userprofile = os.getenv("USERPROFILE") or home
    local cfg = IS_WINDOWS
        and (userprofile .. "\\.audio_to_srt_python")
        or  (home .. "/.audio_to_srt_python")
    local cf = io.open(cfg)
    if cf then
        local p = (cf:read("*l") or ""):match("^%s*(.-)%s*$")
        cf:close()
        if p ~= "" then
            local pf = io.open(p)
            if pf then pf:close(); return p end
        end
    end

    -- IMPORTANT: bare "python" on PATH must remain the LAST Windows fallback
    -- and ideally never be hit. On modern Windows, %LOCALAPPDATA%\Microsoft\
    -- WindowsApps\python.exe is an App Execution Alias stub that satisfies
    -- file-existence checks but prints "Python was not found..." when run.
    -- We list real install locations (including the official `py` launcher)
    -- ahead of it so io.open() picks a working interpreter first.
    local localappdata = os.getenv("LOCALAPPDATA") or ""
    local candidates = IS_WINDOWS and {
        localappdata .. "\\Python\\bin\\python.exe",  -- some 3rd-party installers
        localappdata .. "\\Programs\\Python\\Python314\\python.exe",
        localappdata .. "\\Programs\\Python\\Python313\\python.exe",
        localappdata .. "\\Programs\\Python\\Python312\\python.exe",
        localappdata .. "\\Programs\\Python\\Python311\\python.exe",
        "C:\\Python314\\python.exe",
        "C:\\Python313\\python.exe",
        "C:\\Python312\\python.exe",
        "C:\\Python311\\python.exe",
        (os.getenv("SystemRoot") or "C:\\Windows") .. "\\py.exe",  -- official py launcher
        "python",
    } or {
        "/opt/homebrew/bin/python3",   -- Apple Silicon Homebrew
        "/usr/local/bin/python3",      -- Intel Mac Homebrew / manual
        "/usr/bin/python3",            -- system Python
        "python3",
    }
    for _, p in ipairs(candidates) do
        if p:find("[\\/]") then
            local f = io.open(p)
            if f then f:close(); return p end
        elseif python_works(p) then
            return p
        end
    end
    return candidates[#candidates]
end

local PYTHON3 = find_python()

-- pythonw.exe (windowless) sibling of python.exe — used to launch the
-- loader so no blank CMD window appears in Resolve.
local function find_pythonw()
    if not IS_WINDOWS then return PYTHON3 end
    -- python.exe -> pythonw.exe, py.exe -> pyw.exe
    local cand = PYTHON3:gsub("python%.exe$", "pythonw.exe"):gsub("\\py%.exe$", "\\pyw.exe")
    local f = io.open(cand)
    if f then f:close(); return cand end
    return PYTHON3
end
local PYTHONW = find_pythonw()

local function log(msg)
    if IS_WINDOWS then
        os.execute('if not exist "' .. PROJECT_DIR .. '\\logs" mkdir "' .. PROJECT_DIR .. '\\logs"')
    else
        os.execute('mkdir -p "' .. PROJECT_DIR .. '/logs"')
    end
    local f = io.open(LOG_FILE, "a")
    if f then f:write(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. msg .. "\n"); f:close() end
    print(msg)
end

local function pydialog(...)
    local args = {...}
    local cmd = string.format('%s %q', PYTHON3, DIALOG_PY)
    for _, a in ipairs(args) do
        -- strip newlines: literal \n in cmd.exe splits the command mid-argument
        local clean = tostring(a):gsub("[\r\n]+", " ")
        cmd = cmd .. " " .. string.format("%q", clean)
    end
    cmd = cmd .. (IS_WINDOWS and " 2>nul" or " 2>/dev/null")
    local handle = io.popen(cmd)
    local result = handle:read("*a"); handle:close()
    return result:match("^%s*(.-)%s*$")
end

local function alert(title, msg)
    pydialog("alert", title, msg)
end

local function alert_error(title, msg)
    pydialog("alert_error", title, msg)
end

local function get_resolve()
    if resolve then
        return resolve
    end
    if fusion and fusion.GetResolve then
        return fusion:GetResolve()
    end
    -- Blackmagic's own scripting examples use Resolve() as the entry point.
    -- Keep this fallback so the script still works if the injected globals differ.
    if type(Resolve) == "function" then
        local ok, app = pcall(Resolve)
        if ok and app then
            return app
        end
    end
    return nil
end

local r = get_resolve()
if not r then
    alert_error("Audio to SRT",
        "Cannot connect to DaVinci Resolve.\n\n" ..
        "This script must be run from INSIDE DaVinci Resolve:\n" ..
        "  Workspace  →  Scripts  →  audio_to_srt")
    return
end

log("Script started")
local pm      = r:GetProjectManager()
local project = pm:GetCurrentProject()
if not project then
    alert_error("Audio to SRT", "No project is open.")
    return
end

local timeline = project:GetCurrentTimeline()
if not timeline then
    alert_error("Audio to SRT", "No active timeline. Open a timeline in the Edit page first.")
    return
end

local trackCount = timeline:GetTrackCount("audio")
if trackCount == 0 then
    alert_error("Audio to SRT", "No audio tracks in the current timeline.")
    return
end

local trackItems = {}
for i = 1, trackCount do
    local name = timeline:GetTrackName("audio", i) or ("Audio " .. i)
    trackItems[#trackItems + 1] = "Track " .. i .. ": " .. name
end

-- Video tracks offered for the animated caption styles. "New track" (first,
-- the default) creates a fresh top track; the rest let the user drop captions
-- onto an existing video track.
local videoItems = { "New track" }
for i = 1, timeline:GetTrackCount("video") do
    local name = timeline:GetTrackName("video", i) or ("Video " .. i)
    videoItems[#videoItems + 1] = "Track " .. i .. ": " .. name
end

-- ── Single-window UI: launch loader.pyw, it shows the form first, then
--    transitions in place to the progress view once the user submits.
--    We pass it the track list via prompt.json; it writes selection.json
--    back as soon as the user clicks Generate Subtitles.
local LOGS_DIR    = PROJECT_DIR .. SEP .. "logs"
if IS_WINDOWS then
    os.execute('if not exist "' .. LOGS_DIR .. '" mkdir "' .. LOGS_DIR .. '"')
else
    os.execute('mkdir -p "' .. LOGS_DIR .. '"')
end

local promptPath    = LOGS_DIR .. SEP .. "prompt.json"
local selectionPath = LOGS_DIR .. SEP .. "selection.json"
local loaderLog     = LOG_FILE .. ".transcribe"
local donePath      = LOGS_DIR .. SEP .. "transcribe.done"
local argsPath      = LOGS_DIR .. SEP .. "transcribe_args.txt"
local resultPath    = LOGS_DIR .. SEP .. "transcribe.result"
local ackPath       = LOGS_DIR .. SEP .. "transcribe.ack"
-- Records the track created by the last generation so "Undo" can remove it.
local lastGenPath   = LOGS_DIR .. SEP .. "last_gen.txt"
os.remove(selectionPath); os.remove(donePath); os.remove(argsPath)
os.remove(resultPath);    os.remove(ackPath)

-- Minimal JSON escape for our case (track names, default settings).
local function json_escape(s)
    return (tostring(s)
        :gsub("\\", "\\\\")
        :gsub('"', '\\"')
        :gsub("\n", "\\n")
        :gsub("\r", "\\r")
        :gsub("\t", "\\t"))
end

local jsonParts = {'{"items":['}
for i, it in ipairs(trackItems) do
    jsonParts[#jsonParts + 1] = (i > 1 and ',' or '') .. '"' .. json_escape(it) .. '"'
end
jsonParts[#jsonParts + 1] = '],"video_tracks":['
for i, it in ipairs(videoItems) do
    jsonParts[#jsonParts + 1] = (i > 1 and ',' or '') .. '"' .. json_escape(it) .. '"'
end
jsonParts[#jsonParts + 1] = '],"defaults":{"settings":"25,1,2","punct":0,"lang":"Hindi","diarize":0,"censor":0,"min_secs":"0.4","cps":"25","text_size":"55","outline":1,"shadow":1,"tp_size":"0.18","tp_posx":"0.5","tp_posy":"0.28","font":"Auto (by language)","font_style":"Auto","color":"","outline_color":"","outline_thick":"0.08","shadow_color":"","silence":0,"sil_thr":"-30","sil_gap":"0.5","sil_pad":"0.05"}}'
local pf = io.open(promptPath, "wb")
if not pf then
    alert_error("Audio to SRT", "Cannot write prompt file: " .. promptPath)
    return
end
pf:write(table.concat(jsonParts))
pf:close()

local loaderExists = io.open(LOADER_PY)
if not loaderExists then
    alert_error("Audio to SRT", "loader.pyw not found: " .. LOADER_PY)
    return
end
loaderExists:close()

local launchCmd
if IS_WINDOWS then
    launchCmd = string.format(
        'start "" %q %q --prompt %q --selection %q --args-file %q --done %q --log %q --python %q --script %q --result %q --ack %q',
        PYTHONW, LOADER_PY, promptPath, selectionPath, argsPath, donePath, loaderLog, PYTHON3, TRANSCRIBE_PY, resultPath, ackPath)
else
    launchCmd = string.format(
        '%q %q --prompt %q --selection %q --args-file %q --done %q --log %q --python %q --script %q --result %q --ack %q &',
        PYTHONW, LOADER_PY, promptPath, selectionPath, argsPath, donePath, loaderLog, PYTHON3, TRANSCRIBE_PY, resultPath, ackPath)
end
log("Launching loader: " .. launchCmd)
os.execute(launchCmd)

local function sleep_ms(ms)
    local t = os.clock() + ms / 1000.0
    while os.clock() < t do end
end

-- Wait for either selection (user submitted) or done (user cancelled).
local chosen, settings, include_punct, lang_code
local diarize, censor, min_secs, cps, caption_style, animation, video_track
local text_size, outline, shadow, action, tp_size, tp_posx, tp_posy
local cap_font, cap_color, font_style
local outline_color, outline_thick, shadow_color
local words_per, hl_words, hl_color
local silence, sil_thr, sil_gap, sil_pad
-- Safety net: if the loader crashed before writing either sentinel, stop
-- polling after an hour instead of spinning inside Resolve forever.
local select_deadline = os.time() + 3600
while true do
    if os.time() > select_deadline then
        log("Loader never responded (no selection/done after 60 min) — giving up.")
        return
    end
    local df = io.open(donePath, "r")
    if df then
        local s = df:read("*a") or ""; df:close()
        log("User cancelled (loader closed before submit)")
        os.remove(donePath)
        return
    end
    local sf = io.open(selectionPath, "r")
    if sf then
        local body = sf:read("*a") or ""; sf:close()
        chosen        = body:match('"chosen"%s*:%s*"(.-)"')
        settings      = body:match('"settings"%s*:%s*"(.-)"')
        include_punct = body:match('"punct"%s*:%s*(%d+)')
        lang_code     = body:match('"lang_code"%s*:%s*"(.-)"')
        diarize       = body:match('"diarize"%s*:%s*(%d+)')
        censor        = body:match('"censor"%s*:%s*(%d+)')
        min_secs      = body:match('"min_secs"%s*:%s*"(.-)"')
        cps           = body:match('"cps"%s*:%s*"(.-)"')
        caption_style = body:match('"caption_style"%s*:%s*"(.-)"')
        animation     = body:match('"animation"%s*:%s*"(.-)"')
        video_track   = body:match('"video_track"%s*:%s*"(.-)"')
        text_size     = body:match('"text_size"%s*:%s*"(.-)"')
        outline       = body:match('"outline"%s*:%s*(%d+)')
        shadow        = body:match('"shadow"%s*:%s*(%d+)')
        action        = body:match('"action"%s*:%s*"(.-)"')
        tp_size       = body:match('"tp_size"%s*:%s*"(.-)"')
        tp_posx       = body:match('"tp_posx"%s*:%s*"(.-)"')
        tp_posy       = body:match('"tp_posy"%s*:%s*"(.-)"')
        cap_font      = body:match('"font"%s*:%s*"(.-)"')
        cap_color     = body:match('"color"%s*:%s*"(.-)"')
        font_style    = body:match('"font_style"%s*:%s*"(.-)"')
        outline_color = body:match('"outline_color"%s*:%s*"(.-)"')
        outline_thick = body:match('"outline_thick"%s*:%s*"(.-)"')
        shadow_color  = body:match('"shadow_color"%s*:%s*"(.-)"')
        words_per     = body:match('"words_per"%s*:%s*"(.-)"')
        hl_words      = body:match('"hl_words"%s*:%s*"(.-)"')
        hl_color      = body:match('"hl_color"%s*:%s*"(.-)"')
        silence       = body:match('"silence"%s*:%s*(%d+)')
        sil_thr       = body:match('"sil_thr"%s*:%s*"(.-)"')
        sil_gap       = body:match('"sil_gap"%s*:%s*"(.-)"')
        sil_pad       = body:match('"sil_pad"%s*:%s*"(.-)"')
        if chosen and settings then break end
    end
    sleep_ms(150)
end
if not include_punct or include_punct == "" then include_punct = "0" end
if not lang_code or lang_code == "" then lang_code = "hin" end
if not diarize  or diarize  == "" then diarize  = "0" end
if not censor   or censor   == "" then censor   = "0" end
if not min_secs or min_secs == "" then min_secs = "0" end
if not cps      or cps      == "" then cps      = "0" end
if not caption_style or caption_style == "" then caption_style = "srt" end
if not animation   or animation   == "" then animation   = "karaoke" end
if not video_track or video_track == "" then video_track = "New track" end
if not text_size  or text_size  == "" then text_size  = "" end
if not outline    or outline    == "" then outline    = "1" end
if not shadow     or shadow     == "" then shadow     = "1" end
if not action     or action     == "" then action     = "generate" end
if not tp_size then tp_size = "" end
if not tp_posx then tp_posx = "" end
if not tp_posy then tp_posy = "" end
if not font_style or font_style == "" then font_style = "Auto" end
if not cap_font  then cap_font  = "" end
if not cap_color then cap_color = "" end
if not outline_color then outline_color = "" end
if not outline_thick then outline_thick = "" end
if not shadow_color  then shadow_color  = "" end
if not words_per or words_per == "" then words_per = "0" end
if not hl_words then hl_words = "" end
if not hl_color then hl_color = "" end
if not silence or silence == "" then silence = "0" end
if not sil_thr or sil_thr == "" then sil_thr = "-30" end
if not sil_gap or sil_gap == "" then sil_gap = "0.5" end
if not sil_pad or sil_pad == "" then sil_pad = "0.05" end
-- ffmpeg's silencedetect wants the dB suffix; the form field is just a number.
if not sil_thr:lower():match("db$") then sil_thr = sil_thr .. "dB" end

-- Keyword-highlight set: word -> true, lowercased and stripped of ASCII
-- punctuation so "word," still matches "word". Empty table = feature off.
local hl_set = {}
local hl_enabled = (hl_words ~= "" and hl_color ~= "")
if hl_enabled then
    for token in tostring(hl_words):gmatch("[^%s,]+") do
        local key = token:lower():gsub("%p", "")
        if key ~= "" then hl_set[key] = true end
    end
end
-- "Auto" font means keep the script-aware choice; treat as no override.
local font_override = (cap_font ~= "" and cap_font ~= "Auto (by language)") and cap_font or nil
-- Weight/style override ("Medium", "Bold", ...). "Auto" keeps defaults.
local style_override = (font_style ~= "Auto") and font_style or nil

-- Small hex -> r,g,b (0-1) helper available everywhere (there is also a
-- table-returning hex_to_rgb later, used by the caption styling).
local function hex_rgb3(h)
    local r, g, b = tostring(h):match("^#?(%x%x)(%x%x)(%x%x)$")
    if not r then return nil end
    return tonumber(r, 16) / 255, tonumber(g, 16) / 255, tonumber(b, 16) / 255
end
log("Language code: " .. lang_code .. "  diarize=" .. diarize
    .. " censor=" .. censor .. " min_secs=" .. min_secs .. " cps=" .. cps)

-- Remove the AutoSubs logo/branding banner from a caption comp's Text+
-- inspector. The banner is a LabelControl (an HTML <img>) published as a
-- UserControl; across macro builds it can live on the macro instance or on
-- the inner Template/Follower tools and be named any of the below. We sweep
-- every tool and drop the control if present. Everything is best-effort and
-- pcall-guarded — this is purely cosmetic and must never break generation.
-- Declared here (rather than further down, where it used to live) so both
-- Update mode and the generation path below can call it.
local BANNER_CONTROLS = { "Icon", "IconLabel", "Logo", "Banner",
                          "Header", "Branding", "AutoSubs" }
local function strip_branding_banner(comp)
    pcall(function()
        local tools = comp:GetToolList(false) or {}
        for _, tool in pairs(tools) do
            pcall(function()
                local uc = tool.UserControls
                if not uc then return end
                local changed = false
                for _, key in ipairs(BANNER_CONTROLS) do
                    if uc[key] ~= nil then
                        uc[key] = nil
                        changed = true
                    end
                end
                if changed then
                    tool.UserControls = uc
                    tool:SetAttrs({ TOOLB_Visible = true })
                end
            end)
        end
    end)
end

-- ── Update mode: restyle existing AutoSubs caption clips in place ───────
-- No transcription. Scans every video track for clips whose Fusion comp
-- contains the "AutoSubs" macro tool and applies the new Text+ size,
-- position and outline/shadow to all of them together.
if action == "update" then
    local updated = 0
    local okAll, uerr = pcall(function()
        for t = 1, timeline:GetTrackCount("video") do
            for _, it in ipairs(timeline:GetItemListInTrack("video", t) or {}) do
                pcall(function()
                    local n = it:GetFusionCompCount()
                    if n and n > 0 then
                        local comp = it:GetFusionCompByIndex(1)
                        local tool = comp:FindTool("AutoSubs")
                        if tool then
                            strip_branding_banner(comp)
                            local ts = tonumber(tp_size)
                            if ts and ts > 0 then
                                if ts < 0.02 then ts = 0.02 elseif ts > 0.5 then ts = 0.5 end
                                tool:SetInput("TextSize", ts)
                            end
                            local px, py = tonumber(tp_posx), tonumber(tp_posy)
                            if px and py then
                                tool:SetInput("TextPosition", {px, py})
                            end
                            tool:SetInput("OutlineEnabled", (outline == "1") and 1 or 0)
                            tool:SetInput("ShadowEnabled",  (shadow  == "1") and 1 or 0)
                            if font_override then tool:SetInput("Font", font_override) end
                            if style_override then
                                pcall(function() tool:SetInput("Style", style_override) end)
                            end
                            local ot = tonumber(outline_thick)
                            if ot and ot >= 0 then
                                tool:SetInput("OutlineThickness", ot)
                            end
                            if outline_color ~= "" then
                                local r, g, b = hex_rgb3(outline_color)
                                if r then
                                    tool:SetInput("OutlineColorRed",   r)
                                    tool:SetInput("OutlineColorGreen", g)
                                    tool:SetInput("OutlineColorBlue",  b)
                                end
                            end
                            if shadow_color ~= "" then
                                local r, g, b = hex_rgb3(shadow_color)
                                if r then
                                    tool:SetInput("ShadowColorRed",   r)
                                    tool:SetInput("ShadowColorGreen", g)
                                    tool:SetInput("ShadowColorBlue",  b)
                                end
                            end
                            if cap_color ~= "" then
                                local r, g, b = hex_rgb3(cap_color)
                                if r then
                                    tool:SetInput("FillColorRed",   r)
                                    tool:SetInput("FillColorGreen", g)
                                    tool:SetInput("FillColorBlue",  b)
                                    tool:SetInput("FillEnabled",    1)
                                end
                            end
                            -- The *Color/Thickness inputs are only inspector
                            -- values; the macro pushes them into the rendered
                            -- Text+ when its UpdateStyleColor script runs
                            -- (that's what the "Update ... Color" buttons do).
                            -- Not running it is why the colour never changed.
                            local usc = tool:GetData("UpdateStyleColor")
                            if usc and usc ~= "" then
                                local f = loadstring(usc)()
                                pcall(f, comp, tool, "Fill")
                                pcall(f, comp, tool, "Outline")
                                pcall(f, comp, tool, "Shadow")
                            end
                            updated = updated + 1
                        end
                    end
                end)
            end
        end
    end)
    pm:SaveProject()
    local msg
    if updated > 0 then
        msg = updated .. " caption clip(s) updated"
        local ts = tonumber(tp_size)
        if ts and ts > 0 then msg = msg .. "\nText+ size: " .. tp_size end
        if tonumber(tp_posx) and tonumber(tp_posy) then
            msg = msg .. "\nPosition: " .. tp_posx .. ", " .. tp_posy
        end
        msg = msg .. "\n\nTimeline: " .. timeline:GetName() .. "\nProject saved."
    else
        msg = "No AutoSubs caption clips found on the timeline.\n"
            .. "Generate animated captions first, then use Update."
        if not okAll then msg = msg .. "\nError: " .. tostring(uerr):sub(1, 160) end
    end
    log("Update mode: " .. msg:gsub("\n", " | "))
    local rw = io.open(resultPath, "w")
    if rw then rw:write(msg); rw:close() end
    local deadline = os.time() + 300
    while true do
        local ak = io.open(ackPath, "r")
        if ak then ak:close(); os.remove(ackPath); break end
        if os.time() > deadline then break end
        sleep_ms(250)
    end
    os.remove(resultPath)
    return
end

-- ── Undo mode: remove the track the last generation created ────────────
-- Reads logs/last_gen.txt ("<video|subtitle> <trackIndex> <timelineName>").
-- Only removes the track if it belongs to the current timeline, so we never
-- delete something on a different timeline the user has since switched to.
if action == "undo" then
    local msg
    local lf = io.open(lastGenPath, "r")
    if not lf then
        msg = "Nothing to undo — no caption track has been generated yet."
    else
        local body = lf:read("*a") or ""; lf:close()
        local ttype, tidx, tname = body:match("(%S+)%s+(%d+)%s+(.-)%s*$")
        tidx = tonumber(tidx)
        if not ttype or not tidx then
            msg = "Nothing to undo — the undo record is empty or unreadable."
        elseif tname and tname ~= "" and tname ~= timeline:GetName() then
            msg = "Last captions were made on timeline \"" .. tname
                .. "\".\nSwitch to that timeline, then Undo again."
        else
            local ok = pcall(function()
                if tidx >= 1 and tidx <= timeline:GetTrackCount(ttype) then
                    timeline:DeleteTrack(ttype, tidx)
                end
            end)
            if ok then
                os.remove(lastGenPath)
                pm:SaveProject()
                msg = "Removed the last generated caption track ("
                    .. ttype .. " track " .. tidx .. ").\nProject saved."
            else
                msg = "Could not remove the caption track — it may have been "
                    .. "deleted or moved already."
            end
        end
    end
    log("Undo mode: " .. msg:gsub("\n", " | "))
    local rw = io.open(resultPath, "w")
    if rw then rw:write(msg); rw:close() end
    local deadline = os.time() + 300
    while true do
        local ak = io.open(ackPath, "r")
        if ak then ak:close(); os.remove(ackPath); break end
        if os.time() > deadline then break end
        sleep_ms(250)
    end
    os.remove(resultPath)
    return
end

local trackIndex = tonumber(chosen:match("^Track (%d+)")) or 1
log("Selected track index: " .. trackIndex)

local clips = timeline:GetItemListInTrack("audio", trackIndex)
if not clips or #clips == 0 then
    alert_error("Audio to SRT", "No clips on the selected audio track.")
    return
end

-- Collect clip ranges for timeline remapping (Windows sync fix).
-- Each clip records its own source file path so correction clips (re-takes from
-- a different file dropped on the same track) are transcribed too, not skipped.
local fps     = tonumber(timeline:GetSetting("timelineFrameRate")) or 24
local tlStart = timeline:GetStartFrame()
-- Compute timecode string for SetCurrentTimecode (needed to anchor SRT import)
local tlFr  = tlStart % fps
local tlSec = math.floor(tlStart / fps)
local tc    = string.format("%02d:%02d:%02d:%02d",
                  math.floor(tlSec / 3600), math.floor((tlSec / 60) % 60), tlSec % 60, tlFr)
local rangesPath = PROJECT_DIR .. SEP .. "logs" .. SEP .. "clip_ranges.txt"
local rf = io.open(rangesPath, "w")
local rangeCount = 0
local audioPath = ""
if rf then
    for _, c in ipairs(clips) do
        local cmpi = c:GetMediaPoolItem()
        if not cmpi then
            log("Skipping clip with no media pool item")
        else
            local cpath = (cmpi:GetClipProperty() or {})["File Path"] or ""
            if cpath == "" then
                log("Skipping clip with no source file path")
            else
                local srcStart = c:GetLeftOffset()
                local cTlStart = c:GetStart()
                local cTlEnd   = c:GetEnd()
                local tlFrames = cTlEnd - cTlStart
                local speed    = (c.GetPlayBackSpeed and c:GetPlayBackSpeed()) or 1.0
                if not speed or speed == 0 then speed = 1.0 end
                local srcEnd = srcStart + math.floor(tlFrames * speed + 0.5)
                rf:write(string.format("%d %d %d %g %d %s\n",
                    srcStart, srcEnd, cTlStart, fps, tlStart, cpath))
                rangeCount = rangeCount + 1
                if audioPath == "" then audioPath = cpath end
            end
        end
    end
    rf:close()
end
if rangeCount == 0 then
    alert_error("Audio to SRT", "No clips on the selected track have a usable source file.")
    return
end
log("Audio file (primary): " .. audioPath)
log(string.format("Mapped %d clip range(s); anchor=frame %d", rangeCount, tlStart))

local parts = {}
for p in settings:gmatch("[^,]+") do parts[#parts + 1] = p:match("^%s*(.-)%s*$") end
local maxChars = parts[1] or "15"
local maxLines = parts[2] or "1"
local maxSecs  = parts[3] or "1"

local srtPath   = temp_path(".srt")
local wordsPath = temp_path(".words.json")
log("Transcribing...")

-- Write args as UTF-8 to a file so non-ASCII paths (e.g. curly apostrophes)
-- survive cmd.exe's codepage conversion on Windows. The loader is already
-- running (started above) and polling for this file — once written, it
-- spawns transcribe.py and updates its progress view in place.
local af = io.open(argsPath, "wb")
if not af then
    alert_error("Audio to SRT", "Cannot write args file: " .. argsPath)
    return
end
af:write(audioPath, "\n", srtPath, "\n",
         maxChars, "\n", maxLines, "\n", maxSecs, "\n",
         rangesPath, "\n", include_punct, "\n", lang_code, "\n",
         diarize, "\n", censor, "\n", cps, "\n", min_secs, "\n",
         words_per, "\n", wordsPath, "\n")
af:close()

-- Poll the sentinel file the loader writes on completion.
local code = nil
while true do
    local df = io.open(donePath, "r")
    if df then
        local s = df:read("*a") or ""
        df:close()
        code = tonumber(s:match("(-?%d+)")) or 1
        break
    end
    sleep_ms(250)
end
os.remove(donePath)

-- 130 = user cancelled (closed the window or cancelled at the review step).
-- Not an error: just stop quietly without the transcription-failed alert.
if code == 130 then
    log("User cancelled (code 130) — no captions applied.")
    return
end

local success = (code == 0)

if not success then
    local errFile = io.open(LOG_FILE .. ".transcribe")
    local errMsg  = errFile and errFile:read("*a") or "Unknown error"
    if errFile then errFile:close() end
    alert_error("Audio to SRT - Transcription failed", errMsg:sub(1, 400))
    return
end

local check = io.open(srtPath)
if not check then
    alert_error("Audio to SRT", "Transcription produced no output. Check:\n" .. LOG_FILE .. ".transcribe")
    return
end
check:close()
log("SRT written to: " .. srtPath)

do
    local src = io.open(srtPath, "rb")
    if src then
        local content = src:read("*a"); src:close()
        local dbg = io.open(PROJECT_DIR .. SEP .. "logs" .. SEP .. "last.srt", "wb")
        if dbg then dbg:write(content); dbg:close() end
    end
end

local mp = project:GetMediaPool()

-- Devanagari (Hindi) detection on raw UTF-8 bytes — used by both caption
-- styles to pick the right font.
-- Core block U+0900–U+097F encodes as E0 A4 80 … E0 A5 BF. The danda ।
-- (U+0964 = E0 A5 A4) and double danda ॥ (U+0965 = E0 A5 A5) sit in this
-- block but are shared with Bengali, so they are explicitly skipped.
-- Devanagari Extended U+A8E0–U+A8FF encodes as EA A3 A0 … EA A3 BF.
local function has_devanagari(s)
    if not s or s == "" then return false end
    local i, n = 1, #s
    while i <= n - 2 do
        local b1, b2, b3 = s:byte(i), s:byte(i + 1), s:byte(i + 2)
        if b1 == 0xE0 and (b2 == 0xA4 or b2 == 0xA5) then
            -- skip pan-Indic danda/double-danda (E0 A5 A4 / E0 A5 A5)
            if not (b2 == 0xA5 and (b3 == 0xA4 or b3 == 0xA5)) then
                return true
            end
            i = i + 3
        elseif b1 == 0xEA and b2 == 0xA3 and b3 >= 0xA0 and b3 <= 0xBF then
            return true
        else
            i = i + 1
        end
    end
    return false
end

-- Subtitle styling comes from subtitle_style.json so it can be changed
-- without editing this script. "fontFace" is the default font and
-- "fontFaceDevanagari" is used for Hindi/Devanagari cues (one font per cue).
-- Unknown/nested keys (strokeColor's r/g/b/a) are skipped. Falls back to the
-- historical hardcoded style when the JSON is missing or unreadable.
local function load_style()
    local f = io.open(PROJECT_DIR .. SEP .. "subtitle_style.json")
    if not f then return nil end
    local body = f:read("*a"); f:close()
    if not body or body == "" then return nil end
    local style = {}
    for k, v in body:gmatch('"([%w_]+)"%s*:%s*"(.-)"') do style[k] = v end
    for k, v in body:gmatch('"([%w_]+)"%s*:%s*(-?%d+%.?%d*)') do
        if style[k] == nil then style[k] = tonumber(v) end
    end
    return style
end

-- Fallback fonts are platform-aware: "Nirmala UI" is Windows' bundled
-- Devanagari font; "Noto Serif Devanagari" ships with macOS. Vesper Libre
-- (the old default) is not installed on either by default.
local style = load_style() or {
    fontFace = "Noto Serif Bengali",
    fontFaceDevanagari = IS_WINDOWS and "Nirmala UI" or "Noto Serif Devanagari",
    bold = 1, fontSize = 55, strokeEnabled = 1, strokeOutsideOnly = 1,
    customPosition = 1, posY = 620, shadowEnabled = 1,
    shadowXOffset = 3, shadowYOffset = 3, shadowOpacity = 100,
}
local defFont = style.fontFace or "Noto Serif Bengali"
-- Empty/missing fontFaceDevanagari in the JSON means "auto by platform":
-- Nirmala UI ships with Windows, Noto Serif Devanagari with macOS.
local devFont = style.fontFaceDevanagari
if not devFont or devFont == "" then
    devFont = IS_WINDOWS and "Nirmala UI" or "Noto Serif Devanagari"
end
local skipKeys = { fontFace = true, fontFaceDevanagari = true,
                   strokeColor = true, r = true, g = true, b = true, a = true }

-- ── Caption sidecar (per-word timing + per-speaker colour) ──────────────
-- transcribe.py writes "<srt>.cap" next to the SRT in a simple line format so
-- no JSON parser is needed here:
--   FPS <rate>
--   SPK <idx> <#hex> <style>        (1-based speaker, in appearance order)
--   SEG <start_s> <end_s> <spkIdx>  (times are timeline-relative seconds)
--   WRD <start_s> <end_s> <word>    (words follow their SEG; spkIdx 0 = none)
local capPath = srtPath .. ".cap"
local caption = { fps = fps, speakers = {}, segments = {} }
do
    local cf = io.open(capPath, "rb")
    if cf then
        local curSeg = nil
        for rawline in cf:lines() do
            local ln = rawline:gsub("\r$", "")
            local tag = ln:match("^(%u+)")
            if tag == "FPS" then
                caption.fps = tonumber(ln:match("^FPS%s+([%d%.]+)")) or fps
            elseif tag == "SPK" then
                local idx, hex, sstyle = ln:match("^SPK%s+(%d+)%s+(%S+)%s+(%S+)")
                if idx then
                    caption.speakers[tonumber(idx)] = { hex = hex, style = sstyle or "Fill" }
                end
            elseif tag == "SEG" then
                local s, e, spk = ln:match("^SEG%s+(%-?[%d%.]+)%s+(%-?[%d%.]+)%s+(%d+)")
                if s then
                    curSeg = { start = tonumber(s), endt = tonumber(e),
                               spk = tonumber(spk) or 0, words = {} }
                    table.insert(caption.segments, curSeg)
                end
            elseif tag == "WRD" and curSeg then
                local s, e, w = ln:match("^WRD%s+(%-?[%d%.]+)%s+(%-?[%d%.]+)%s+(.*)$")
                if s then
                    table.insert(curSeg.words,
                        { start = tonumber(s), endt = tonumber(e), word = w })
                end
            end
        end
        cf:close()
    end
end

local function hex_to_rgb(hex)
    local r, g, b = tostring(hex):match("^#?(%x%x)(%x%x)(%x%x)$")
    if not r then return nil end
    return { r = tonumber(r, 16) / 255, g = tonumber(g, 16) / 255, b = tonumber(b, 16) / 255 }
end

-- Speaker index -> subtitle-item colour, as a nested {r,g,b,a} table in 0-1
-- range (same shape as strokeColor in subtitle_style.json). nil when unknown.
local function speaker_color(spk)
    if not spk or spk == 0 then return nil end
    local sp = caption.speakers[spk]
    if not sp then return nil end
    local rgb = hex_to_rgb(sp.hex)
    if not rgb then return nil end
    return { r = rgb.r, g = rgb.g, b = rgb.b, a = 1 }
end

-- Match a subtitle item to its caption segment by nearest start time (the
-- subtitle track carries a dummy blank cue at t=0, so index alignment is not
-- reliable). Times are timeline-relative seconds.
local function segment_for_time(sec)
    local best, bestDelta = nil, 0.30
    for _, seg in ipairs(caption.segments) do
        local d = math.abs(seg.start - sec)
        if d <= bestDelta then best, bestDelta = seg, d end
    end
    return best
end

-- ── Animated Text+ insertion (experimental; reuses the AutoSubs macro) ──
local function utf8len(s)
    local _, c = s:gsub("[^\128-\191]", "")
    return c
end
local function to_word_timing(words, frameRate, segStart)
    -- Character indices must address the macro's Text, which is the words
    -- joined by single spaces. The AutoSubs macro counts the separating space
    -- as part of the following word (wordText = " " .. word for i > 1), so we
    -- mirror that here — otherwise every highlight after the first word drifts
    -- one character to the left of the word it should cover.
    local result, charIndex = {}, 0
    for i, w in ipairs(words) do
        local wtext = (i > 1) and (" " .. w.word) or w.word
        local length = utf8len(wtext)
        table.insert(result, {
            startIndex = charIndex, endIndex = charIndex + length - 1,
            startFrame = math.floor((w.start - segStart) * frameRate),
            endFrame   = math.floor((w.endt  - segStart) * frameRate),
        })
        charIndex = charIndex + length
    end
    return result
end

-- Colour specific words (the keyword-highlight feature) in a caption clip.
-- Writes a single static CharacterLevelStyling keyframe that overrides the
-- Fill colour of every keyword-matching word, mirroring the macro's own
-- StyledText format (INTERNAL_CODES Red=2401/Green=2402/Blue=2403/Enabled=2000,
-- style Index 0 = Fill). Applied AFTER the macro's SetAnimations/ToggleHighlight
-- so it is not immediately overwritten; for karaoke styles it therefore takes
-- precedence over the time-based sweep. Best-effort + fully pcall-guarded — the
-- Fusion object model varies by Resolve version, and this must never break a
-- clip. hex is "#RRGGBB"; kwset maps lowercased/punct-stripped word -> true.
local function apply_keyword_highlight(comp, seg_words, tfps, segStart, kwset, hex)
    local r, g, b = hex_rgb3(hex)
    if not r then return end
    local wt = to_word_timing(seg_words, tfps, segStart)
    local array = {}
    local any = false
    for i, w in ipairs(seg_words) do
        local key = tostring(w.word):lower():gsub("%p", "")
        if key ~= "" and kwset[key] and wt[i] then
            any = true
            local si, ei = wt[i].startIndex, wt[i].endIndex
            array[#array + 1] = { 2401, si, ei, Value = r, __flags = 256, Index = 0 }
            array[#array + 1] = { 2402, si, ei, Value = g, __flags = 256, Index = 0 }
            array[#array + 1] = { 2403, si, ei, Value = b, __flags = 256, Index = 0 }
            array[#array + 1] = { 2000, si, ei, Value = 1, __flags = 256, Index = 0 }
        end
    end
    if not any then return end
    local follower = comp:FindTool("Follower1")
    if not follower then return end
    local cls = follower.Text:GetConnectedOutput():GetTool()
    local spline = cls.CharacterLevelStyling:GetConnectedOutput():GetTool()
    local keyframes = {
        [0] = { 0, Value = { __ctor = "StyledText", Array = array,
                Flags = { StepIn = true, LockedY = true, __flags = 256 } } },
    }
    spline:SetKeyFrames(keyframes, true)
end

local function find_media_clip(folder, name)
    for _, sub in ipairs(folder:GetSubFolderList() or {}) do
        local f = find_media_clip(sub, name)
        if f then return f end
    end
    for _, clip in ipairs(folder:GetClipList() or {}) do
        local props = clip:GetClipProperty() or {}
        if props["Clip Name"] == name then return clip end
    end
    return nil
end

-- Media-pool clip name of the caption template inside caption-bin.drb.
-- White-labelled to "Animated Caption" (the bin used to ship it as
-- "AutoSubs Caption"); must match the <Name> in caption-bin.drb exactly.
local ANIMATED_CAPTION = "Animated Caption"

-- Animation presets for the AutoSubs macro, selected in the Caption Style
-- dropdown. Each is a combination of the macro's published animation inputs:
--   fade/pop/slide = entry effect (0/1)   hi = per-word karaoke highlight (0/1)
--   mode = 0 In / 1 Out / 2 Both          len = animation duration (seconds)
local ANIM_PRESETS = {
    karaoke   = { fade = 1, pop = 0, slide = 0, mode = 2, len = 0.30, hi = 1 },
    fade      = { fade = 1, pop = 0, slide = 0, mode = 2, len = 0.40, hi = 0 },
    pop       = { fade = 0, pop = 1, slide = 0, mode = 0, len = 0.30, hi = 0 },
    slide     = { fade = 0, pop = 0, slide = 1, mode = 0, len = 0.40, hi = 0 },
    fadepop   = { fade = 1, pop = 1, slide = 0, mode = 2, len = 0.35, hi = 0 },
    fadeslide = { fade = 1, pop = 0, slide = 1, mode = 2, len = 0.40, hi = 0 },
    karpop    = { fade = 1, pop = 1, slide = 0, mode = 2, len = 0.30, hi = 1 },
    fastpop   = { fade = 0, pop = 1, slide = 0, mode = 0, len = 0.18, hi = 0 },
    smooth    = { fade = 1, pop = 0, slide = 0, mode = 2, len = 0.60, hi = 0 },
    slidehi   = { fade = 1, pop = 0, slide = 1, mode = 0, len = 0.40, hi = 1 },
}
-- Places one Text+ template clip per caption segment on a new video track and
-- fills in text, per-word highlight timing, font and per-speaker colour using
-- the macro's published inputs. Returns ok, info|errMessage. Any per-clip
-- failure is counted, not fatal; a total failure returns false so the caller
-- can fall back to the reliable SRT import.
local function add_animated()
    local segs = caption.segments
    if #segs == 0 then return false, "caption sidecar has no segments" end

    local templateItem = find_media_clip(mp:GetRootFolder(), ANIMATED_CAPTION)
    if not templateItem then
        pcall(function() mp:ImportFolderFromFile(PROJECT_DIR .. SEP .. "caption-bin.drb") end)
        templateItem = find_media_clip(mp:GetRootFolder(), ANIMATED_CAPTION)
    end
    if not templateItem then
        return false, "'" .. ANIMATED_CAPTION .. "' template not found and caption-bin.drb import failed"
    end
    local tfps = tonumber((templateItem:GetClipProperty() or {})["FPS"]) or caption.fps

    -- Target video track: reuse the one the user picked, else create a new
    -- top track. video_track is "New track" or "Track N: <name>".
    local vtrack = nil
    local pick = video_track and video_track:match("^Track (%d+)")
    if pick then
        local idx = tonumber(pick)
        if idx and idx >= 1 and idx <= timeline:GetTrackCount("video") then
            vtrack = idx
        end
    end
    local created_new_track = false
    if not vtrack then
        vtrack = timeline:GetTrackCount("video") + 1
        timeline:AddTrack("video")
        created_new_track = true
    end

    local joinThreshold = fps
    local clipList = {}
    for i, seg in ipairs(segs) do
        local start_frame = seg.start * fps
        local end_frame   = seg.endt * fps
        local pos = tlStart + start_frame
        local dur = end_frame - start_frame
        if i < #segs then
            local between = (tlStart + segs[i + 1].start * fps) - (pos + dur)
            if between < joinThreshold then dur = dur + between + 1 end
        end
        if dur < 1 then dur = 1 end
        table.insert(clipList, {
            mediaPoolItem = templateItem, mediaType = 1,
            startFrame = 0, endFrame = (dur / fps) * tfps,
            recordFrame = pos, trackIndex = vtrack,
        })
    end

    local placed = mp:AppendToTimeline(clipList)
    if type(placed) ~= "table" or #placed == 0 then
        return false, "AppendToTimeline placed no caption clips (Resolve may be incompatible with the macro)"
    end

    local failed = 0
    for i, item in ipairs(placed) do
        local seg = segs[i]
        local ok = pcall(function()
            local n = item:GetFusionCompCount()
            if not n or n == 0 then error("clip has no Fusion composition") end
            local comp = item:GetFusionCompByIndex(1)

            local parts = {}
            for _, w in ipairs(seg.words) do parts[#parts + 1] = w.word end
            local text = table.concat(parts, " ")

            local autosubsTool = comp:FindTool("AutoSubs")
            local template = comp:FindTool("Template") or comp:FindToolByID("TextPlus") or autosubsTool
            if autosubsTool then
                pcall(function() autosubsTool:SetData("WordTiming",
                    to_word_timing(seg.words, tfps, seg.start)) end)
                pcall(function() autosubsTool:SetInput("Text", text) end)
                -- Strip the "AutoSubs Caption" logo/branding banner from the
                -- Text+ inspector (it's a LabelControl holding an HTML <img>).
                -- Depending on the macro build the banner control can sit on
                -- the macro instance OR on its inner Template/Follower tools,
                -- and be named Icon / Logo / Banner / Header, so we sweep every
                -- tool in the comp and drop any of those UserControls. Cosmetic
                -- only and fully pcall-guarded — a miss must never abort a clip.
                strip_branding_banner(comp)
            end
            if template and template ~= autosubsTool then
                pcall(function() template:SetInput("Text", text) end)
            end

            -- Font: explicit dialog choice wins, else script-aware default.
            local font = font_override or (has_devanagari(text) and devFont or defFont)
            if autosubsTool and font and font ~= "" then
                pcall(function() autosubsTool:SetInput("Font", font) end)
            end
            -- Weight/style ("Medium", "Bold", ...) via the Text+ Style input.
            if autosubsTool and style_override then
                pcall(function() autosubsTool:SetInput("Style", style_override) end)
            end

            -- Colour: manual dialog colour wins over the per-speaker colour.
            local rgb = nil
            if cap_color ~= "" then
                rgb = hex_to_rgb(cap_color)
            else
                local sp = seg.spk and seg.spk > 0 and caption.speakers[seg.spk]
                rgb = sp and hex_to_rgb(sp.hex) or nil
            end
            if rgb and autosubsTool then
                pcall(function()
                    autosubsTool:SetInput("FillColorRed",   rgb.r)
                    autosubsTool:SetInput("FillColorGreen", rgb.g)
                    autosubsTool:SetInput("FillColorBlue",  rgb.b)
                    autosubsTool:SetInput("FillEnabled",    1)
                    -- The FillColor* controls are just the macro's inspector
                    -- values — they are NOT wired to the rendered text. The
                    -- macro only pushes them into the underlying Text+ (its
                    -- Template + Follower1 tools) when its "Update Fill Color"
                    -- button runs the UpdateStyleColor script. Setting the
                    -- inputs without running it is exactly why the caption
                    -- colour never changed. Run it here, mirroring how we run
                    -- SetAnimations / ToggleHighlight below.
                    local usc = autosubsTool:GetData("UpdateStyleColor")
                    if usc and usc ~= "" then
                        loadstring(usc)()(comp, autosubsTool, "Fill")
                    end
                end)
            end

            -- Animation preset + outline/shadow, applied to the macro's
            -- published inputs. Wrapped so an unknown input on a future macro
            -- version can't abort the whole clip.
            local p = ANIM_PRESETS[animation] or ANIM_PRESETS.karaoke
            if autosubsTool then
                pcall(function()
                    autosubsTool:SetInput("FadeEnabled",      p.fade)
                    autosubsTool:SetInput("PopInEnabled",     p.pop)
                    autosubsTool:SetInput("SlideUpEnabled",   p.slide)
                    autosubsTool:SetInput("AnimationMode",    p.mode)
                    autosubsTool:SetInput("AnimationLength",  p.len)
                    autosubsTool:SetInput("HighlightEnabled", p.hi)
                    autosubsTool:SetInput("OutlineEnabled",   (outline == "1") and 1 or 0)
                    autosubsTool:SetInput("ShadowEnabled",    (shadow == "1") and 1 or 0)
                    -- Text+ size is the macro's native relative scale (the
                    -- Fusion inspector "Size", e.g. 0.18) — set directly from
                    -- the dialog's Text+ size field, clamped to a sane range.
                    local rel = tonumber(tp_size)
                    if rel and rel > 0 then
                        if rel < 0.02 then rel = 0.02 elseif rel > 0.5 then rel = 0.5 end
                        autosubsTool:SetInput("TextSize", rel)
                    end
                    -- Optional caption position (0-1 screen coords); blank
                    -- fields keep the template's own position.
                    local px, py = tonumber(tp_posx), tonumber(tp_posy)
                    if px and py then
                        autosubsTool:SetInput("TextPosition", {px, py})
                    end
                end)
                -- Outline thickness/colour + shadow colour from the dialog,
                -- pushed into the rendered Text+ via the macro's
                -- UpdateStyleColor script (same as its "Update ... Color"
                -- buttons — setting the inputs alone changes nothing).
                pcall(function()
                    local ot = tonumber(outline_thick)
                    if ot and ot >= 0 then
                        autosubsTool:SetInput("OutlineThickness", ot)
                    end
                    if outline_color ~= "" then
                        local r, g, b = hex_rgb3(outline_color)
                        if r then
                            autosubsTool:SetInput("OutlineColorRed",   r)
                            autosubsTool:SetInput("OutlineColorGreen", g)
                            autosubsTool:SetInput("OutlineColorBlue",  b)
                        end
                    end
                    if shadow_color ~= "" then
                        local r, g, b = hex_rgb3(shadow_color)
                        if r then
                            autosubsTool:SetInput("ShadowColorRed",   r)
                            autosubsTool:SetInput("ShadowColorGreen", g)
                            autosubsTool:SetInput("ShadowColorBlue",  b)
                        end
                    end
                    local usc = autosubsTool:GetData("UpdateStyleColor")
                    if usc and usc ~= "" then
                        local f = loadstring(usc)()
                        pcall(f, comp, autosubsTool, "Outline")
                        pcall(f, comp, autosubsTool, "Shadow")
                    end
                end)
                -- Rebuild the animation + highlight keyframes from the new
                -- inputs (mirrors the macro's "Update Animation" button).
                pcall(function()
                    local sa = autosubsTool:GetData("SetAnimations")
                    if sa and sa ~= "" then loadstring(sa)()(comp, autosubsTool) end
                end)
                pcall(function()
                    local th = autosubsTool:GetData("ToggleHighlight")
                    if th and th ~= "" then loadstring(th)()(comp, autosubsTool) end
                end)
                -- Keyword highlight LAST so it survives the animation rebuild.
                if hl_enabled then
                    pcall(function()
                        apply_keyword_highlight(comp, seg.words, tfps,
                                                seg.start, hl_set, hl_color)
                    end)
                end
            end

            item:SetClipColor("Green")
        end)
        if not ok then failed = failed + 1 end
    end
    return true, { track = vtrack, placed = #placed, failed = failed,
                   created = created_new_track }
end

-- ── Choose insertion path ──────────────────────────────────────────────
local usedAnimated = false
local animInfo = nil
local animError = nil
if caption_style == "animated" then
    local ok, res = add_animated()
    if ok then
        usedAnimated = true
        animInfo = res
    else
        animError = tostring(res)
        log("Animated captions failed (" .. animError .. ") — falling back to SRT import.")
    end
end

local count = 0
local hindiCount = 0

if usedAnimated then
    count = animInfo.placed
    os.remove(srtPath); os.remove(capPath)
    log(string.format("Done. Placed %d animated caption clips on video track %d (%d failed).",
        animInfo.placed, animInfo.track, animInfo.failed))
    -- Record for Undo, but only when WE created the track (placing onto an
    -- existing track the user picked must never be undoable — we'd delete
    -- their own track). Otherwise clear any stale record.
    if animInfo.created then
        local gf = io.open(lastGenPath, "w")
        if gf then
            gf:write(string.format("video %d %s", animInfo.track,
                     timeline:GetName()))
            gf:close()
        end
    else
        os.remove(lastGenPath)
    end
else
    -- SRT import onto a fresh subtitle track (default and fallback path).
    for i = timeline:GetTrackCount("subtitle"), 1, -1 do
        timeline:DeleteTrack("subtitle", i)
    end
    timeline:AddTrack("subtitle")
    timeline:SetCurrentTimecode(tc)   -- anchor so SRT times align with timeline start

    local imported = mp:ImportMedia({srtPath})
    if not imported or #imported == 0 then
        alert_error("Audio to SRT", "Resolve could not import the SRT file.")
        os.remove(srtPath); os.remove(capPath)
        return
    end

    if not mp:AppendToTimeline({imported[1]}) then
        alert_error("Audio to SRT", "Resolve could not append SRT to the timeline.")
        os.remove(srtPath); os.remove(capPath)
        return
    end
    os.remove(srtPath); os.remove(capPath)
    -- Record the fresh subtitle track (always track 1 here) for Undo.
    local gf = io.open(lastGenPath, "w")
    if gf then
        gf:write(string.format("subtitle 1 %s", timeline:GetName()))
        gf:close()
    end

    local items = timeline:GetItemListInTrack("subtitle", 1) or {}
    count = #items
    if items[1] then
        log(string.format("SRT clip at frame %d (expected %d, delta %d)",
            items[1]:GetStart(), tlStart, items[1]:GetStart() - tlStart))
    end

    -- "Plain Text" imports the same timed subtitle clips as "SRT (colored)"
    -- but skips every styling step below (font/color/stroke/shadow/weight) —
    -- Resolve's own default subtitle appearance is left untouched, so the
    -- track carries plain, unformatted text with no per-speaker coloring.
    if caption_style ~= "plain" then
        for _, item in ipairs(items) do
            local text = item:GetName() or ""
            if font_override then
                item:SetProperty("fontFace", font_override)
                if has_devanagari(text) then hindiCount = hindiCount + 1 end
            elseif has_devanagari(text) then
                item:SetProperty("fontFace", devFont)
                hindiCount = hindiCount + 1
            else
                item:SetProperty("fontFace", defFont)
            end
            for k, v in pairs(style) do
                if not skipKeys[k] then item:SetProperty(k, v) end
            end
            -- User style overrides from the dialog: text size + outline/shadow.
            local ts = tonumber(text_size)
            if ts and ts > 0 then item:SetProperty("fontSize", ts) end
            item:SetProperty("strokeEnabled", (outline == "1") and 1 or 0)
            item:SetProperty("shadowEnabled", (shadow == "1") and 1 or 0)
            -- Weight/style: subtitle items only expose bold/italic flags, so map
            -- the chosen weight onto those ("Medium"/"Light" -> regular weight).
            if style_override then
                local lsty = style_override:lower()
                item:SetProperty("bold",   lsty:find("bold")   and 1 or 0)
                item:SetProperty("italic", lsty:find("italic") and 1 or 0)
            end
            -- Colour: a manual dialog colour applies to every cue; otherwise fall
            -- back to the per-speaker colour matched by time.
            local col = nil
            if cap_color ~= "" then
                local r, g, b = hex_rgb3(cap_color)
                if r then col = { r = r, g = g, b = b, a = 1 } end
            else
                local sec = (item:GetStart() - tlStart) / fps
                local seg = segment_for_time(sec)
                col = speaker_color(seg and seg.spk or 0)
            end
            if col then pcall(function() item:SetProperty("color", col) end) end
        end
    end

    pm:SaveProject()
    log("Done. Imported " .. count .. " subtitle cues ("
        .. hindiCount .. " Devanagari -> " .. devFont .. ").")
end

if usedAnimated then pm:SaveProject() end

-- ── Optional: silence cut (renders a tightened copy + matching SRT) ──────
-- Resolve cannot ripple-delete via scripting, so instead of cutting the
-- timeline we render a clean "silence removed" copy of the primary clip plus
-- a re-timed SRT and import both into the Media Pool. The words JSON written
-- by transcribe.py is reused so this never re-bills the ElevenLabs API.
local silenceMsg = ""
if silence == "1" then
    log("Silence cut requested: thr=" .. sil_thr .. " gap=" .. sil_gap .. " pad=" .. sil_pad)

    local audioDir = audioPath:match("^(.*)[/\\][^/\\]+$") or PROJECT_DIR
    local exportsDir = audioDir .. SEP .. "audio_to_srt_exports"
    if IS_WINDOWS then
        os.execute('if not exist "' .. exportsDir .. '" mkdir "' .. exportsDir .. '"')
    else
        os.execute('mkdir -p "' .. exportsDir .. '"')
    end

    local fname = audioPath:match("([^/\\]+)$") or "clip"
    local ext   = fname:match("(%.[^.]+)$") or ".mp4"
    local base  = fname:gsub("%.[^.]+$", "")
    local outMedia = exportsDir .. SEP .. base .. "_nosilence" .. ext
    local outSrt   = exportsDir .. SEP .. base .. "_nosilence.srt"

    local silCmd = string.format(
        '%q -u %q %q --out %q --srt-out %q --words %q --threshold %q --min-silence %q --pad %q --max-chars %s --max-lines %s --max-secs %s > %q 2>&1',
        PYTHON3, SILENCE_PY, audioPath, outMedia, outSrt, wordsPath,
        sil_thr, sil_gap, sil_pad,
        tostring(maxChars), tostring(maxLines), tostring(maxSecs),
        LOG_FILE .. ".silence")
    log("Running silence: " .. silCmd)
    local sok = run_shell(silCmd)
    local ssuccess = (sok == true) or (type(sok) == "number" and sok == 0)

    local mediaCheck = io.open(outMedia)
    if ssuccess and mediaCheck then
        mediaCheck:close()
        mp:ImportMedia({ outMedia })
        local sc = io.open(outSrt)
        if sc then sc:close(); mp:ImportMedia({ outSrt }) end
        silenceMsg = "\n\nSilence removed — tightened clip + SRT imported to Media Pool:\n" .. outMedia
        log("Silence cut imported: " .. outMedia)
        pm:SaveProject()
    else
        if mediaCheck then mediaCheck:close() end
        local ef = io.open(LOG_FILE .. ".silence")
        local em = ef and ef:read("*a") or ""
        if ef then ef:close() end
        if ssuccess and em:find('"nothing_to_cut"', 1, true) then
            silenceMsg = "\n\nNo silence found above the threshold — nothing was cut."
            log("Silence: nothing to cut")
        else
            silenceMsg = "\n\n(Silence cut failed — is ffmpeg installed? See logs/audio_to_srt.log.silence)"
            log("Silence cut failed: " .. (em or ""):sub(1, 300))
        end
    end
end
os.remove(wordsPath)

-- Write completion message for the loader window to display in-place,
-- then wait for the user to click OK before exiting.
local result_msg
if usedAnimated then
    result_msg = count .. " animated caption clips added on a new video track."
        .. (animInfo.failed > 0 and ("\n" .. animInfo.failed .. " clip(s) could not be styled.") or "")
        .. silenceMsg
        .. "\n\nTimeline: " .. timeline:GetName() .. "\nProject saved."
else
    result_msg = count .. " subtitle cues imported."
        .. (animError and ("\n\nAnimated captions were unavailable, so subtitles were imported as SRT instead.\n(" .. animError:sub(1, 160) .. ")") or "")
        .. silenceMsg
        .. "\n\nTimeline: " .. timeline:GetName() .. "\nProject saved."
end
local rw = io.open(resultPath, "w")
if rw then rw:write(result_msg); rw:close() end

local ack_deadline = os.time() + 300
while true do
    local ak = io.open(ackPath, "r")
    if ak then ak:close(); os.remove(ackPath); break end
    if os.time() > ack_deadline then break end
    sleep_ms(250)
end
os.remove(resultPath)
