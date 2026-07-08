-- audio_to_srt.lua  —  Workspace -> Scripts -> audio_to_srt
-- Transcribes the first clip on the selected audio track via ElevenLabs and imports subtitles.
-- Cross-platform: Mac + Windows. All dialogs use dialog.py (tkinter).

-- ── Detect OS and set paths ────────────────────────────────────────────────────
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
    -- Fallback if setup was never run
    return IS_WINDOWS
        and (userprofile .. "\\DaVinci-Audio2SRT")
        or  (home .. "/DaVinci-Audio2SRT")
end

local PROJECT_DIR = get_project_dir()

local TRANSCRIBE_PY = PROJECT_DIR .. SEP .. "transcribe.py"
local SILENCE_PY    = PROJECT_DIR .. SEP .. "silence.py"
local DIALOG_PY     = PROJECT_DIR .. SEP .. "dialog.py"
local LOG_FILE      = PROJECT_DIR .. SEP .. "logs" .. SEP .. "audio_to_srt.log"
local DIALOG_LOG    = PROJECT_DIR .. SEP .. "logs" .. SEP .. "audio_to_srt.log.dialog"

local function shell_quote(s)
    s = tostring(s or "")
    if IS_WINDOWS then
        -- cmd.exe: backslashes are literal, so plain quotes are enough
        -- (Lua's %q doubles backslashes, which corrupts UNC/trailing-slash
        -- paths). Windows filenames cannot contain quotes; strip just in case.
        return '"' .. s:gsub('"', "") .. '"'
    end
    -- POSIX: single quotes disable every metacharacter ($ ` \ etc.).
    return "'" .. s:gsub("'", "'\\''") .. "'"
end

-- cmd.exe's /c parser can strip the opening quote of a command line that
-- starts with a quoted path ("C:\Program Files\...") and then split on the
-- space. Wrapping the whole line in one extra pair of quotes disables that
-- (documented in `cmd /?`). io.popen/os.execute both go through cmd /c.
local function run_shell(cmd)
    if IS_WINDOWS then cmd = '"' .. cmd .. '"' end
    return os.execute(cmd)
end

local function popen_read(cmd)
    if IS_WINDOWS then cmd = '"' .. cmd .. '"' end
    local h = io.popen(cmd)
    if not h then return "" end
    local out = h:read("*a") or ""
    h:close()
    return out
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

-- Find Python: check common install locations, fall back to bare command on PATH
local function command_exists(cmd)
    local check
    if IS_WINDOWS then
        check = string.format('where %s >nul 2>nul', cmd)
    else
        check = string.format('command -v %s >/dev/null 2>/dev/null', cmd)
    end
    local ok = os.execute(check)
    return (ok == true) or (type(ok) == "number" and ok == 0)
end

local function find_python()
    local localappdata = os.getenv("LOCALAPPDATA") or ""
    local candidates = IS_WINDOWS and {
        localappdata .. "\\Programs\\Python\\Python312\\python.exe",
        localappdata .. "\\Programs\\Python\\Python311\\python.exe",
        localappdata .. "\\Programs\\Python\\Python310\\python.exe",
        "C:\\Python312\\python.exe",
        "C:\\Python311\\python.exe",
        "C:\\Python310\\python.exe",
        "python",
        "py -3",
    } or {
        "/opt/homebrew/bin/python3",   -- Apple Silicon Homebrew
        "/usr/local/bin/python3",      -- Intel Mac Homebrew / manual
        "/usr/bin/python3",            -- system Python
        "python3",
    }
    for _, p in ipairs(candidates) do
        if p:find("[\\/]") then
            local f = io.open(p)
            if f then f:close(); return shell_quote(p) end
        else
            local base = p:match("^([^%s]+)")
            if base and command_exists(base) then
                return p
            end
        end
    end
    return candidates[#candidates]
end

local PYTHON3 = find_python()

-- ── Logging ────────────────────────────────────────────────────────────────────
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

-- ── Dialogs via dialog.py (tkinter) — same on Mac and Windows ─────────────────
local function pydialog(...)
    local args = {...}
    local cmd = string.format('%s -u %s', PYTHON3, shell_quote(DIALOG_PY))
    for _, a in ipairs(args) do
        cmd = cmd .. " " .. shell_quote(a)
    end
    cmd = cmd .. " 2>> " .. shell_quote(DIALOG_LOG)
    local result = popen_read(cmd)
    return result:match("^%s*(.-)%s*$")
end

local function alert(title, msg)
    pydialog("alert", title, msg)
end

local function alert_error(title, msg)
    pydialog("alert_error", title, msg)
end

-- ── Get Resolve objects ────────────────────────────────────────────────────────
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
        "  Workspace  →  Scripts  →  audio_to_srt\n\n" ..
        "Make sure:\n" ..
        "• DaVinci Resolve is open\n" ..
        "• The script is in one of these folders:\n" ..
        "  ~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/\n" ..
        "  /Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/\n" ..
        "  %APPDATA%\\Blackmagic Design\\DaVinci Resolve\\Support\\Fusion\\Scripts\\Utility\\\n" ..
        "  %ProgramData%\\Blackmagic Design\\DaVinci Resolve\\Fusion\\Scripts\\Utility\\\n" ..
        "• You are running it via the Workspace > Scripts menu — NOT from Terminal or Finder")
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

-- ── Pick audio track ───────────────────────────────────────────────────────────
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

local chosen = pydialog("pick", "Audio to SRT", "Select audio track to transcribe:",
                         table.unpack(trackItems))

if not chosen or chosen == "" then
    log("User cancelled track selection")
    return
end

local trackIndex = tonumber(chosen:match("^Track (%d+)")) or 1
log("Selected track index: " .. trackIndex)

-- ── Get audio file path ────────────────────────────────────────────────────────
local clips = timeline:GetItemListInTrack("audio", trackIndex)
if not clips or #clips == 0 then
    alert_error("Audio to SRT", "No clips on the selected audio track.")
    return
end

table.sort(clips, function(a, b)
    return (a:GetStart() or 0) < (b:GetStart() or 0)
end)

local clip = clips[1]
local mpi = clip:GetMediaPoolItem()
if not mpi then
    alert_error("Audio to SRT", "Cannot access media pool item for this clip.")
    return
end

local audioPath = (mpi:GetClipProperty() or {})["File Path"] or ""
if audioPath == "" then
    alert_error("Audio to SRT", "Clip has no source file path.")
    return
end

log("Audio file: " .. audioPath)

local fps = tonumber(timeline:GetSetting("timelineFrameRate")) or 24
local timelineStartFrame = tonumber(timeline:GetStartFrame()) or 0
local clipStartFrame = tonumber(clip:GetStart()) or timelineStartFrame
local sourceStartSecs = tonumber(clip:GetSourceStartTime()) or 0
local sourceEndSecs = tonumber(clip:GetSourceEndTime()) or 0
local timelineOffsetSecs = (clipStartFrame - timelineStartFrame) / fps

log(string.format("Clip timing: start_frame=%s source_start=%.3f source_end=%.3f offset=%.3f",
    tostring(clipStartFrame), sourceStartSecs, sourceEndSecs, timelineOffsetSecs))

-- ── Subtitle settings ──────────────────────────────────────────────────────────
local settings = pydialog("input", "Audio to SRT",
    "Subtitle settings\nMax chars per line, max lines, max seconds\n(comma-separated)",
    "10,1,5")

if not settings or settings == "" then
    log("User cancelled settings")
    return
end

local parts = {}
for p in settings:gmatch("[^,]+") do parts[#parts + 1] = p:match("^%s*(.-)%s*$") end
local maxChars = tonumber(parts[1]) or 10
local maxLines = tonumber(parts[2]) or 1
local maxSecs  = tonumber(parts[3]) or 5

if maxChars < 1 or maxLines < 1 or maxSecs <= 0 then
    alert_error("Audio to SRT", "Invalid subtitle settings. Use positive numbers like: 10,1,5")
    return
end

-- ── Optional: auto-cut silence ──────────────────────────────────────────────────
-- Leave blank to skip (subtitle behaviour is unchanged). If filled in, after the
-- subtitles are added the script also renders a "silence removed" copy of the clip
-- (plus a matching SRT) and imports both into the Media Pool. The original timeline
-- is never touched.
local silenceSpec = pydialog("input", "Audio to SRT",
    "Auto-cut silence too?  (optional)\nEnter: thresholdDB,minGap,pad   e.g.  -30,0.5,0.05\nLeave blank to skip.",
    "")

local doSilence = false
local silThr, silGap, silPad = "-30dB", "0.5", "0.05"
if silenceSpec and silenceSpec:match("%S") then
    doSilence = true
    local sp = {}
    for p in silenceSpec:gmatch("[^,]+") do sp[#sp + 1] = p:match("^%s*(.-)%s*$") end
    if sp[1] and sp[1] ~= "" then
        silThr = sp[1]
        if not silThr:lower():match("db$") then silThr = silThr .. "dB" end
    end
    if sp[2] and sp[2] ~= "" then silGap = sp[2] end
    if sp[3] and sp[3] ~= "" then silPad = sp[3] end
end

-- ── Call Python for transcription ──────────────────────────────────────────────
local srtPath   = temp_path(".srt")
local wordsPath = temp_path(".words.json")

log("Transcribing...")

local cmd = string.format(
    '%s -u %s %s %s %d %d %s %s %s %s --words-out=%s > %s 2>&1',
    PYTHON3,
    shell_quote(TRANSCRIBE_PY),
    shell_quote(audioPath),
    shell_quote(srtPath),
    maxChars,
    maxLines,
    tostring(maxSecs),
    tostring(sourceStartSecs),
    tostring(sourceEndSecs),
    tostring(timelineOffsetSecs),
    shell_quote(wordsPath),
    shell_quote(LOG_FILE .. ".transcribe")
)
log("Running: " .. cmd)
local ok = run_shell(cmd)
-- Lua 5.1 returns exit code (number); Lua 5.3+ returns true/nil
local success = (ok == true) or (type(ok) == "number" and ok == 0)

if not success then
    local errFile = io.open(LOG_FILE .. ".transcribe")
    local errMsg  = errFile and errFile:read("*a") or "Unknown error"
    if errFile then errFile:close() end
    alert_error("Audio to SRT — Transcription failed", errMsg:sub(1, 400))
    return
end

local check = io.open(srtPath)
if not check then
    alert_error("Audio to SRT", "Transcription produced no output. Check:\n" .. LOG_FILE .. ".transcribe")
    return
end
check:close()

log("SRT written to: " .. srtPath)

-- ── Import SRT into timeline ───────────────────────────────────────────────────
local mp  = project:GetMediaPool()
local sf  = timeline:GetStartFrame()
local fr  = sf % fps
local t   = math.floor(sf / fps)
local tc  = string.format("%02d:%02d:%02d:%02d",
                math.floor(t / 3600), math.floor((t / 60) % 60), t % 60, fr)

for i = timeline:GetTrackCount("subtitle"), 1, -1 do
    timeline:DeleteTrack("subtitle", i)
end
timeline:AddTrack("subtitle")
timeline:SetCurrentTimecode(tc)

local imported = mp:ImportMedia({ srtPath })
if not imported or #imported == 0 then
    alert_error("Audio to SRT", "Resolve could not import the SRT file.")
    os.remove(srtPath)
    return
end

if not mp:AppendToTimeline({ imported[1] }) then
    alert_error("Audio to SRT", "Resolve could not append SRT to the timeline.")
    os.remove(srtPath)
    return
end

os.remove(srtPath)

-- ── Apply subtitle style ───────────────────────────────────────────────────────
local items = timeline:GetItemListInTrack("subtitle", 1) or {}
local count = #items

-- Style. Font: "Noto Serif Devanagari" ships with macOS but not Windows —
-- there it renders as tofu boxes; "Nirmala UI" is Windows' bundled Devanagari
-- font. Size/position were tuned on 1080p; scale them to the timeline
-- resolution or subtitles land off-frame / oversized on 720p and 4K.
local resH = tonumber(timeline:GetSetting("timelineResolutionHeight")) or 1080
local styleScale = resH / 1080
local fontFace = IS_WINDOWS and "Nirmala UI" or "Noto Serif Devanagari"
for _, item in ipairs(items) do
    item:SetProperty("fontFace",          fontFace)
    item:SetProperty("bold",              1)
    item:SetProperty("fontSize",          math.floor(35 * styleScale + 0.5))
    item:SetProperty("strokeEnabled",     1)
    item:SetProperty("strokeOutsideOnly", 1)
    item:SetProperty("customPosition",    1)
    item:SetProperty("posY",              math.floor(620 * styleScale + 0.5))
    item:SetProperty("shadowEnabled",     1)
    item:SetProperty("shadowXOffset",     math.max(1, math.floor(3 * styleScale + 0.5)))
    item:SetProperty("shadowYOffset",     math.max(1, math.floor(3 * styleScale + 0.5)))
    item:SetProperty("shadowOpacity",     100)
end

-- ── Optional: silence cut (renders a tightened copy + matching SRT) ─────────────
local silenceMsg = ""
if doSilence then
    log("Silence cut requested: thr=" .. silThr .. " gap=" .. silGap .. " pad=" .. silPad)

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
        '%s -u %s %s --out %s --srt-out %s --words %s --threshold %s --min-silence %s --pad %s --max-chars %d --max-lines %d --max-secs %s > %s 2>&1',
        PYTHON3,
        shell_quote(SILENCE_PY),
        shell_quote(audioPath),
        shell_quote(outMedia),
        shell_quote(outSrt),
        shell_quote(wordsPath),
        shell_quote(silThr),
        shell_quote(silGap),
        shell_quote(silPad),
        maxChars, maxLines, tostring(maxSecs),
        shell_quote(LOG_FILE .. ".silence")
    )
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

pm:SaveProject()
log("Done. Imported " .. count .. " subtitle cues.")

alert("Audio to SRT — Done",
    count .. " subtitle cues imported.\n\nTimeline: " .. timeline:GetName() .. "\nProject saved." .. silenceMsg)
