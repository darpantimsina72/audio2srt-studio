-- audio_to_srt.lua  —  Resolve bridge for "Audio2SRT Studio"
-- Installed by the app. Calls the bundled app binary (no system Python / ffmpeg).
-- Workspace -> Scripts -> audio_to_srt

local IS_WINDOWS = package.config:sub(1, 1) == "\\"

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

local function trim(s)
    return (tostring(s or ""):match("^%s*(.-)%s*$"))
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

-- ── Find the installed app via the marker file ~/.audio2srt_app ──────────────────
local function home()
    return os.getenv("HOME") or os.getenv("USERPROFILE") or ""
end

local MARKER = home() .. (IS_WINDOWS and "\\.audio2srt_app" or "/.audio2srt_app")

local function read_marker()
    local f = io.open(MARKER, "r")
    if not f then return nil end
    local data = f:read("*a"); f:close()
    local exe = data:match('"exe"%s*:%s*"(.-)"')
    if exe then exe = exe:gsub('\\\\', '\\') end           -- unescape JSON backslashes
    local py  = data:match('"python"%s*:%s*"(.-)"')
    if py then py = py:gsub('\\\\', '\\') end
    local frozen = data:match('"frozen"%s*:%s*(%a+)') == "true"
    return { exe = exe, python = py, frozen = frozen }
end

local APP = read_marker()

-- ── Logging ──────────────────────────────────────────────────────────────────────
local LOG_DIR  = home() .. (IS_WINDOWS and "\\.audio2srt_logs" or "/.audio2srt_logs")
local LOG_FILE = LOG_DIR .. (IS_WINDOWS and "\\resolve.log" or "/resolve.log")
local function log(msg)
    if IS_WINDOWS then os.execute('if not exist "' .. LOG_DIR .. '" mkdir "' .. LOG_DIR .. '"')
    else os.execute('mkdir -p "' .. LOG_DIR .. '"') end
    local f = io.open(LOG_FILE, "a")
    if f then f:write(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. tostring(msg) .. "\n"); f:close() end
end

-- ── Build the command prefix that invokes the app ────────────────────────────────
local function app_prefix()
    if not APP or not APP.exe then return nil end
    if APP.frozen then
        return shell_quote(APP.exe)
    end
    local py = APP.python or (IS_WINDOWS and "python" or "python3")
    return shell_quote(py) .. " " .. shell_quote(APP.exe)
end

local PREFIX = app_prefix()

-- ── Dialogs via the app's bundled tkinter dialog ─────────────────────────────────
local function pydialog(...)
    if not PREFIX then return "" end
    local cmd = PREFIX .. " dialog"
    for _, a in ipairs({ ... }) do cmd = cmd .. " " .. shell_quote(a) end
    return trim(popen_read(cmd))
end

local function alert(title, msg) pydialog("alert", title, msg) end
local function alert_error(title, msg) pydialog("alert_error", title, msg) end

-- ── Connect to Resolve ─────────────────────────────────────────────────────────
local function get_resolve()
    if resolve then return resolve end
    if fusion and fusion.GetResolve then return fusion:GetResolve() end
    if type(Resolve) == "function" then
        local ok, app = pcall(Resolve)
        if ok and app then return app end
    end
    return nil
end

if not PREFIX then
    -- Can't even show a styled dialog without the app; use Resolve's own if present.
    local r0 = get_resolve()
    print("Audio2SRT: app not found. Open Audio2SRT Studio and click 'Install Resolve script'.")
    return
end

local r = get_resolve()
if not r then
    alert_error("Audio to SRT",
        "Run this from inside DaVinci Resolve:\nWorkspace -> Scripts -> audio_to_srt")
    return
end

log("Script started")
local pm = r:GetProjectManager()
local project = pm:GetCurrentProject()
if not project then alert_error("Audio to SRT", "No project is open.") return end

local timeline = project:GetCurrentTimeline()
if not timeline then alert_error("Audio to SRT", "No active timeline. Open one in the Edit page.") return end

-- ── Pick audio track ─────────────────────────────────────────────────────────────
local trackCount = timeline:GetTrackCount("audio")
if trackCount == 0 then alert_error("Audio to SRT", "No audio tracks in the timeline.") return end

local trackItems = {}
for i = 1, trackCount do
    trackItems[#trackItems + 1] = "Track " .. i .. ": " .. (timeline:GetTrackName("audio", i) or ("Audio " .. i))
end

local chosen = pydialog("pick", "Audio to SRT", "Select audio track to transcribe:", table.unpack(trackItems))
if not chosen or chosen == "" then log("Cancelled track selection") return end
local trackIndex = tonumber(chosen:match("^Track (%d+)")) or 1

-- ── Get audio file + timing ───────────────────────────────────────────────────────
local clips = timeline:GetItemListInTrack("audio", trackIndex)
if not clips or #clips == 0 then alert_error("Audio to SRT", "No clips on that audio track.") return end
table.sort(clips, function(a, b) return (a:GetStart() or 0) < (b:GetStart() or 0) end)

local clip = clips[1]
local mpi = clip:GetMediaPoolItem()
if not mpi then alert_error("Audio to SRT", "Cannot access media pool item.") return end
local audioPath = (mpi:GetClipProperty() or {})["File Path"] or ""
if audioPath == "" then alert_error("Audio to SRT", "Clip has no source file path.") return end

local fps = tonumber(timeline:GetSetting("timelineFrameRate")) or 24
local timelineStartFrame = tonumber(timeline:GetStartFrame()) or 0
local clipStartFrame = tonumber(clip:GetStart()) or timelineStartFrame
local sourceStartSecs = tonumber(clip:GetSourceStartTime()) or 0
local sourceEndSecs   = tonumber(clip:GetSourceEndTime()) or 0
local timelineOffsetSecs = (clipStartFrame - timelineStartFrame) / fps
log("Audio: " .. audioPath)

-- ── Subtitle settings ─────────────────────────────────────────────────────────────
local settings = pydialog("input", "Audio to SRT",
    "Subtitle settings\nMax chars, max lines, max seconds (comma-separated)", "10,1,5")
if not settings or settings == "" then log("Cancelled settings") return end
local parts = {}
for p in settings:gmatch("[^,]+") do parts[#parts + 1] = trim(p) end
local maxChars = tonumber(parts[1]) or 10
local maxLines = tonumber(parts[2]) or 1
local maxSecs  = tonumber(parts[3]) or 5
if maxChars < 1 or maxLines < 1 or maxSecs <= 0 then
    alert_error("Audio to SRT", "Invalid settings. Use positive numbers like 10,1,5") return
end

-- ── Optional silence cut ──────────────────────────────────────────────────────────
local silenceSpec = pydialog("input", "Audio to SRT",
    "Auto-cut silence too? (optional)\nthresholdDB,minGap,pad   e.g. -30,0.5,0.05\nLeave blank to skip.", "")
local doSilence = silenceSpec and silenceSpec:match("%S")
local silThr, silGap, silPad = "-30dB", "0.5", "0.05"
if doSilence then
    local sp = {}
    for p in silenceSpec:gmatch("[^,]+") do sp[#sp + 1] = trim(p) end
    if sp[1] and sp[1] ~= "" then silThr = sp[1]; if not silThr:lower():match("db$") then silThr = silThr .. "dB" end end
    if sp[2] and sp[2] ~= "" then silGap = sp[2] end
    if sp[3] and sp[3] ~= "" then silPad = sp[3] end
end

-- ── Transcribe ─────────────────────────────────────────────────────────────────────
local srtPath   = temp_path(".srt")
local wordsPath = temp_path(".words.json")
log("Transcribing...")
local cmd = string.format(
    '%s transcribe %s %s %d %d %s %s %s %s --words-out %s > %s 2>&1',
    PREFIX, shell_quote(audioPath), shell_quote(srtPath),
    maxChars, maxLines, tostring(maxSecs),
    tostring(sourceStartSecs), tostring(sourceEndSecs), tostring(timelineOffsetSecs),
    shell_quote(wordsPath), shell_quote(LOG_FILE .. ".transcribe"))
log("Run: " .. cmd)
local ok = run_shell(cmd)
local success = (ok == true) or (type(ok) == "number" and ok == 0)
local check = io.open(srtPath)
if not (success and check) then
    if check then check:close() end
    local ef = io.open(LOG_FILE .. ".transcribe"); local em = ef and ef:read("*a") or "Unknown error"
    if ef then ef:close() end
    alert_error("Audio to SRT — Transcription failed", trim(em):sub(1, 400))
    return
end
check:close()

-- ── Import SRT into the timeline ───────────────────────────────────────────────────
local mp = project:GetMediaPool()
local sf = timeline:GetStartFrame()
local t  = math.floor(sf / fps)
local tc = string.format("%02d:%02d:%02d:%02d", math.floor(t / 3600), math.floor((t / 60) % 60), t % 60, sf % fps)
for i = timeline:GetTrackCount("subtitle"), 1, -1 do timeline:DeleteTrack("subtitle", i) end
timeline:AddTrack("subtitle")
timeline:SetCurrentTimecode(tc)
local imported = mp:ImportMedia({ srtPath })
if not imported or #imported == 0 then alert_error("Audio to SRT", "Resolve could not import the SRT.") os.remove(srtPath) return end
if not mp:AppendToTimeline({ imported[1] }) then alert_error("Audio to SRT", "Resolve could not append the SRT.") os.remove(srtPath) return end
os.remove(srtPath)

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
    item:SetProperty("fontFace", fontFace)
    item:SetProperty("bold", 1)
    item:SetProperty("fontSize", math.floor(35 * styleScale + 0.5))
    item:SetProperty("strokeEnabled", 1); item:SetProperty("strokeOutsideOnly", 1)
    item:SetProperty("customPosition", 1)
    item:SetProperty("posY", math.floor(620 * styleScale + 0.5))
    item:SetProperty("shadowEnabled", 1)
    item:SetProperty("shadowXOffset", math.max(1, math.floor(3 * styleScale + 0.5)))
    item:SetProperty("shadowYOffset", math.max(1, math.floor(3 * styleScale + 0.5)))
    item:SetProperty("shadowOpacity", 100)
end

-- ── Silence: markers (Resolve API can't ripple-delete) + tightened clip export ─────
local silenceMsg = ""
if doSilence then
    -- 1) Mark each silent region on the timeline so you can ripple-delete fast.
    local detectCmd = string.format('%s detect %s --lines --threshold %s --min-silence %s --pad %s 2>%s',
        PREFIX, shell_quote(audioPath), shell_quote(silThr), shell_quote(silGap), shell_quote(silPad),
        shell_quote(LOG_FILE .. ".detect"))
    local out = popen_read(detectCmd)
    local marks = 0
    -- If the source out-point could not be read (0), treat the clip as
    -- untrimmed — otherwise every marker would be silently skipped.
    local srcEndEff = (sourceEndSecs > sourceStartSecs) and sourceEndSecs or math.huge
    for line in tostring(out):gmatch("[^\r\n]+") do
        local cs, ce = line:match("^([%-%d%.]+)%s+([%-%d%.]+)$")
        if cs then
            cs, ce = tonumber(cs), tonumber(ce)
            if cs and ce and cs >= sourceStartSecs and cs <= srcEndEff then
                local seqSecs = (cs - sourceStartSecs) + timelineOffsetSecs
                local frameId = math.floor(seqSecs * fps + 0.5)
                local dur = math.max(1, math.floor((ce - cs) * fps + 0.5))
                if timeline:AddMarker(frameId, "Red", "Silence", "auto-detected silence", dur) then
                    marks = marks + 1
                end
            end
        end
    end
    log("Silence markers added: " .. marks)

    -- 2) Render a tightened copy + matching SRT, import both to the Media Pool.
    local audioDir = audioPath:match("^(.*)[/\\][^/\\]+$") or home()
    local exportsDir = audioDir .. (IS_WINDOWS and "\\audio_to_srt_exports" or "/audio_to_srt_exports")
    if IS_WINDOWS then os.execute('if not exist "' .. exportsDir .. '" mkdir "' .. exportsDir .. '"')
    else os.execute('mkdir -p "' .. exportsDir .. '"') end
    local fname = audioPath:match("([^/\\]+)$") or "clip"
    local ext = fname:match("(%.[^.]+)$") or ".mp4"
    local base = fname:gsub("%.[^.]+$", "")
    local sep = IS_WINDOWS and "\\" or "/"
    local outMedia = exportsDir .. sep .. base .. "_nosilence" .. ext
    local outSrt   = exportsDir .. sep .. base .. "_nosilence.srt"
    local silCmd = string.format(
        '%s silence %s --out %s --srt-out %s --words %s --threshold %s --min-silence %s --pad %s --max-chars %d --max-lines %d --max-secs %s > %s 2>&1',
        PREFIX, shell_quote(audioPath), shell_quote(outMedia), shell_quote(outSrt), shell_quote(wordsPath),
        shell_quote(silThr), shell_quote(silGap), shell_quote(silPad),
        maxChars, maxLines, tostring(maxSecs), shell_quote(LOG_FILE .. ".silence"))
    local sok = run_shell(silCmd)
    local ssuccess = (sok == true) or (type(sok) == "number" and sok == 0)
    local mc = io.open(outMedia)
    if ssuccess and mc then
        mc:close()
        mp:ImportMedia({ outMedia })
        local sc = io.open(outSrt); if sc then sc:close(); mp:ImportMedia({ outSrt }) end
        silenceMsg = "\n\nSilence: " .. marks .. " markers added on the timeline.\nTightened clip + SRT imported to Media Pool:\n" .. outMedia
    else
        if mc then mc:close() end
        local ef = io.open(LOG_FILE .. ".silence")
        local em = ef and ef:read("*a") or ""
        if ef then ef:close() end
        if ssuccess and em:find('"nothing_to_cut"', 1, true) then
            silenceMsg = "\n\nNo silence found above the threshold — nothing was cut."
        else
            silenceMsg = "\n\nSilence: " .. marks .. " markers added (tightened-clip render failed — see logs)."
        end
    end
end

os.remove(wordsPath)
pm:SaveProject()
log("Done. " .. count .. " cues.")
alert("Audio to SRT — Done",
    count .. " subtitle cues imported.\n\nTimeline: " .. timeline:GetName() .. "\nProject saved." .. silenceMsg)
