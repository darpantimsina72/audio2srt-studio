-- audio_to_srt_reaper.lua
-- Transcribe the first selected media item via ElevenLabs and export an SRT
-- file to a user-chosen location. No subtitle items are created in REAPER.

local IS_WINDOWS = package.config:sub(1, 1) == "\\"
local SEP = IS_WINDOWS and "\\" or "/"

local function shell_quote(s)
    return string.format("%q", tostring(s))
end

local function trim(s)
    return (tostring(s or ""):match("^%s*(.-)%s*$"))
end

local function get_project_dir()
    local home = os.getenv("HOME") or ""
    local userprofile = os.getenv("USERPROFILE") or home
    local config = IS_WINDOWS
        and (userprofile .. "\\.audio_to_srt_reaper_path")
        or (home .. "/.audio_to_srt_reaper_path")
    local f = io.open(config, "r")
    if f then
        local path = trim(f:read("*l"))
        f:close()
        if path ~= "" then
            return path
        end
    end
    return debug.getinfo(1, "S").source:sub(2):match("^(.*)[/\\].-$") or "."
end

local PROJECT_DIR = get_project_dir()
local TRANSCRIBE_PY = PROJECT_DIR .. SEP .. "transcribe.py"
local LOG_DIR = PROJECT_DIR .. SEP .. "logs"
local LOG_FILE = LOG_DIR .. SEP .. "audio_to_srt_reaper.log"

local function ensure_dir(path)
    if reaper and reaper.RecursiveCreateDirectory then
        reaper.RecursiveCreateDirectory(path, 0)
    elseif IS_WINDOWS then
        os.execute('if not exist "' .. path .. '" mkdir "' .. path .. '"')
    else
        os.execute('mkdir -p "' .. path .. '"')
    end
end

local function log(msg)
    ensure_dir(LOG_DIR)
    local f = io.open(LOG_FILE, "a")
    if f then
        f:write(os.date("%Y-%m-%d %H:%M:%S") .. "  " .. tostring(msg) .. "\n")
        f:close()
    end
end

local function alert(title, msg)
    reaper.ShowMessageBox(tostring(msg), tostring(title), 0)
end

local function alert_error(title, msg)
    reaper.ShowMessageBox(tostring(msg), tostring(title), 0)
end

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
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
        "python3",
    }

    for _, p in ipairs(candidates) do
        if p:find("[\\/]") then
            local f = io.open(p, "r")
            if f then
                f:close()
                return shell_quote(p)
            end
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

local function split_csv(s)
    local out = {}
    for part in tostring(s):gmatch("[^,]+") do
        out[#out + 1] = trim(part)
    end
    return out
end

local function sanitize_filename(name)
    local cleaned = tostring(name or "")
        :gsub("[/\\:*?\"<>|]", "_")
        :gsub("%s+", "_")
    cleaned = cleaned:gsub("^_+", ""):gsub("_+$", "")
    if cleaned == "" then
        cleaned = "subtitle_export"
    end
    return cleaned
end

local function basename(path)
    local name = tostring(path or ""):match("([^/\\]+)$") or tostring(path or "")
    return name:gsub("%.[^.]+$", "")
end

local function build_output_dir()
    local project_path = reaper.GetProjectPath("")
    local base_path = trim(project_path)
    if base_path == "" then
        base_path = PROJECT_DIR
    end
    local output_dir = base_path .. SEP .. "audio_to_srt_exports"
    ensure_dir(output_dir)
    return output_dir
end

local function script_path_ok()
    local f = io.open(TRANSCRIBE_PY, "r")
    if not f then
        return false
    end
    f:close()
    return true
end

if not script_path_ok() then
    alert_error(
        "Audio to SRT for REAPER",
        "Cannot find transcribe.py.\n\nExpected at:\n" .. TRANSCRIBE_PY ..
        "\n\nRun the setup script inside the Reaper version folder first."
    )
    return
end

log("Script started")

local selected_count = reaper.CountSelectedMediaItems(0)
if selected_count < 1 then
    alert_error(
        "Audio to SRT for REAPER",
        "Select one media item first, then run the script."
    )
    return
end

if selected_count > 1 then
    alert_error(
        "Audio to SRT for REAPER",
        "Select only one media item at a time."
    )
    return
end

local item = reaper.GetSelectedMediaItem(0, 0)
if not item then
    alert_error("Audio to SRT for REAPER", "Could not read the selected item.")
    return
end

local take = reaper.GetActiveTake(item)
if not take then
    alert_error("Audio to SRT for REAPER", "The selected item has no active take.")
    return
end

if reaper.TakeIsMIDI and reaper.TakeIsMIDI(take) then
    alert_error("Audio to SRT for REAPER", "The selected item is MIDI. Select an audio or video item.")
    return
end

local source = reaper.GetMediaItemTake_Source(take)
if not source then
    alert_error("Audio to SRT for REAPER", "Could not access the selected item's source media.")
    return
end

local audio_path = reaper.GetMediaSourceFileName(source, "")
audio_path = trim(audio_path)
if audio_path == "" then
    alert_error("Audio to SRT for REAPER", "The selected item does not have a readable source file path.")
    return
end

local item_position = reaper.GetMediaItemInfo_Value(item, "D_POSITION") or 0
local item_length = reaper.GetMediaItemInfo_Value(item, "D_LENGTH") or 0
local _, item_guid = reaper.GetSetMediaItemInfo_String(item, "GUID", "", false)

log("Selected source: " .. audio_path)
log(string.format("Item timing: position=%.3f length=%.3f", item_position, item_length))

local ok_inputs, raw_inputs = reaper.GetUserInputs(
    "Audio to SRT for REAPER",
    3,
    "Max chars per line,Max lines,Max seconds",
    "42,2,7"
)

if not ok_inputs then
    log("User cancelled settings")
    return
end

local parts = split_csv(raw_inputs)
local max_chars = tonumber(parts[1]) or 42
local max_lines = tonumber(parts[2]) or 2
local max_secs = tonumber(parts[3]) or 7

if max_chars < 1 or max_lines < 1 or max_secs <= 0 then
    alert_error(
        "Audio to SRT for REAPER",
        "Invalid subtitle settings. Use positive values like 42,2,7."
    )
    return
end

local output_dir = build_output_dir()
local guid_stub = trim(item_guid):gsub("[%{%}%-%s%@:/\\]", ""):sub(1, 12)
if guid_stub == "" then
    guid_stub = tostring(math.floor(item_position * 1000))
end

local file_stem = sanitize_filename(basename(audio_path) .. "__" .. guid_stub)
local default_srt_path = output_dir .. SEP .. file_stem .. ".srt"
local transcribe_log = LOG_FILE .. ".transcribe"

local ok_path, srt_path = reaper.GetUserInputs(
    "Audio to SRT for REAPER",
    1,
    "Output SRT path",
    default_srt_path
)

if not ok_path then
    log("User cancelled output path selection")
    return
end

srt_path = trim(srt_path)
if srt_path == "" then
    alert_error("Audio to SRT for REAPER", "Output SRT path cannot be empty.")
    return
end

if not srt_path:lower():match("%.srt$") then
    srt_path = srt_path .. ".srt"
end

local target_dir = srt_path:match("^(.*)[/\\][^/\\]+$")
if target_dir and trim(target_dir) ~= "" then
    ensure_dir(target_dir)
end

ensure_dir(output_dir)
ensure_dir(LOG_DIR)

local cmd = string.format(
    '%s -u %s %s %s %d %d %s %s %s %s > %s 2>&1',
    PYTHON3,
    shell_quote(TRANSCRIBE_PY),
    shell_quote(audio_path),
    shell_quote(srt_path),
    max_chars,
    max_lines,
    tostring(max_secs),
    "0",
    "0",
    "0",
    shell_quote(transcribe_log)
)

log("Source media: " .. audio_path)
log("Output SRT: " .. srt_path)
log("Running: " .. cmd)
local result = os.execute(cmd)
local success = (result == true) or (type(result) == "number" and result == 0)

if not success then
    local err_file = io.open(transcribe_log, "r")
    local err_msg = err_file and err_file:read("*a") or "Unknown transcription error."
    if err_file then
        err_file:close()
    end
    alert_error("Audio to SRT for REAPER", trim(err_msg):sub(1, 600))
    return
end

local check = io.open(srt_path, "r")
if not check then
    alert_error("Audio to SRT for REAPER", "Transcription finished but no SRT file was created.")
    return
end
check:close()

log("Done. SRT exported successfully.")

alert(
    "Audio to SRT for REAPER",
    "Subtitle file exported successfully.\n\nSaved to:\n" .. srt_path
)
