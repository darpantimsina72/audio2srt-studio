/*
 * audio_to_srt.jsx — ExtendScript host for the Premiere Pro "Audio to SRT" panel.
 *
 * Every function returns a delimited string ("OK|..." or "ERR|...") because
 * ExtendScript in Premiere has no reliable built-in JSON. The panel (main.js)
 * parses these strings.
 */

function _ok(s)  { return "OK|"  + (s === undefined ? "" : s); }
function _err(s) { return "ERR|" + (s === undefined ? "" : s); }

/* List audio tracks of the active sequence as "index::name::clipCount" rows. */
function listAudioTracks() {
    var seq = app.project ? app.project.activeSequence : null;
    if (!seq) return _err("No active sequence. Open a sequence in Premiere first.");

    var rows = [];
    for (var i = 0; i < seq.audioTracks.numTracks; i++) {
        var t = seq.audioTracks[i];
        var n = 0;
        try { n = t.clips.numItems; } catch (e) { n = 0; }
        var name = "Audio " + (i + 1);
        try { if (t.name && t.name.length) name = t.name; } catch (e2) {}
        rows.push(i + "::" + name + "::" + n);
    }
    if (!rows.length) return _err("The active sequence has no audio tracks.");
    return _ok(rows.join("\n"));
}

/* Earliest clip on the given audio track:  mediaPath|timelineOffset|srcStart|srcEnd */
function getClipInfo(trackIndex) {
    trackIndex = parseInt(trackIndex, 10);
    var seq = app.project ? app.project.activeSequence : null;
    if (!seq) return _err("No active sequence.");

    var track = seq.audioTracks[trackIndex];
    if (!track) return _err("Audio track not found.");
    if (track.clips.numItems === 0) return _err("No clips on the selected audio track.");

    var clip = track.clips[0];
    for (var i = 1; i < track.clips.numItems; i++) {
        if (track.clips[i].start.seconds < clip.start.seconds) clip = track.clips[i];
    }

    var proj = clip.projectItem;
    var mediaPath = "";
    try { mediaPath = proj.getMediaPath(); } catch (e) { mediaPath = ""; }
    if (!mediaPath) return _err("Clip has no readable source file path.");

    var start = clip.start.seconds;       // sequence position  -> timeline_offset
    var inPoint = 0, outPoint = 0;
    try { inPoint = clip.inPoint.seconds; } catch (e2) {}   // source in  -> source_start
    try { outPoint = clip.outPoint.seconds; } catch (e3) {} // source out -> source_end

    return _ok(mediaPath + "|" + start + "|" + inPoint + "|" + outPoint);
}

/* Import any file (SRT caption item or tightened media) into the project root. */
function importFile(filePath) {
    try {
        if (!app.project) return _err("No project open.");
        var ok = app.project.importFiles([filePath], true, app.project.rootItem, false);
        if (!ok) return _err("Premiere could not import:\n" + filePath);
        return _ok("imported");
    } catch (e) {
        return _err("Import error: " + e.toString());
    }
}

/* Best-effort: drop an already-imported caption onto a new caption track of the
 * active sequence. Premiere's scripting caption API varies by version, so this
 * may fail silently — the panel always falls back to "drag it from the bin". */
function addCaptionToTimeline(srtPath) {
    try {
        var seq = app.project ? app.project.activeSequence : null;
        if (!seq) return _err("No active sequence.");

        // Find the caption project item we just imported (matches file name).
        var root = app.project.rootItem;
        var target = null;
        var leaf = srtPath.replace(/\\/g, "/").split("/").pop();
        for (var i = 0; i < root.children.numItems; i++) {
            var it = root.children[i];
            var nm = "";
            try { nm = it.name; } catch (e) {}
            if (nm && (nm === leaf || nm.indexOf(leaf.replace(/\.srt$/i, "")) === 0)) {
                target = it; break;
            }
        }
        if (!target) return _err("Caption item not found in project.");

        if (typeof seq.createCaptionTrack === "function") {
            seq.createCaptionTrack(target, 0);
            return _ok("caption_track_added");
        }
        return _err("createCaptionTrack unavailable in this Premiere version.");
    } catch (e) {
        return _err("Caption placement error: " + e.toString());
    }
}

/* Sanity ping from the panel on load. */
function ping() {
    try {
        var v = (app && app.version) ? app.version : "?";
        return _ok("Premiere " + v);
    } catch (e) {
        return _err(e.toString());
    }
}
