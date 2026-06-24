/* main.js — Premiere panel for "Audio2SRT Studio".
 * Calls the bundled app binary (found via ~/.audio2srt_app) — no system Python.
 * Subtitles -> timeline; silence -> in-place ripple-cut (QE DOM) + tightened export.
 */

(function () {
  "use strict";

  var _require = (typeof window.cep_node !== "undefined" && window.cep_node.require)
    ? window.cep_node.require
    : (typeof require !== "undefined" ? require : null);

  var statusEl = document.getElementById("status");
  if (!_require) {
    statusEl.textContent = "ERROR: Node.js not enabled. Quit and reopen Premiere; if it persists, reinstall from Audio2SRT Studio.";
    return;
  }

  var fs = _require("fs");
  var os = _require("os");
  var path = _require("path");
  var cp = _require("child_process");

  // ── ExtendScript bridge ────────────────────────────────────────────────────
  function evalES(script) {
    return new Promise(function (resolve) {
      window.__adobe_cep__.evalScript(script, function (res) { resolve(res); });
    });
  }
  function parseES(res) {
    res = String(res || "");
    var bar = res.indexOf("|");
    return { ok: res.slice(0, bar) === "OK", body: bar >= 0 ? res.slice(bar + 1) : "" };
  }

  function setStatus(m, c) { statusEl.textContent = m; statusEl.className = c || ""; }
  function append(m) { statusEl.textContent += "\n" + m; statusEl.scrollTop = statusEl.scrollHeight; }

  // ── Find the installed app (marker written by Audio2SRT Studio) ──────────────
  function readMarker() {
    try {
      var p = path.join(os.homedir(), ".audio2srt_app");
      var m = JSON.parse(fs.readFileSync(p, "utf8"));
      if (m && m.exe) return m;
    } catch (e) {}
    return null;
  }
  var APP = readMarker();

  function runApp(subcmd, args) {
    return new Promise(function (resolve) {
      if (!APP) { resolve({ code: 1, out: "", err: "Audio2SRT Studio not found. Open the app and click 'Install Premiere panel'." }); return; }
      var bin, full;
      if (APP.frozen) { bin = APP.exe; full = [subcmd].concat(args); }
      else { bin = APP.python || "python3"; full = [APP.exe, subcmd].concat(args); }
      cp.execFile(bin, full, { maxBuffer: 1024 * 1024 * 32 }, function (error, stdout, stderr) {
        resolve({ code: error ? (error.code || 1) : 0, out: stdout || "", err: stderr || "" });
      });
    });
  }

  function lastJson(s) {
    var line = (s || "").trim().split("\n").pop();
    try { return JSON.parse(line); } catch (e) { return null; }
  }
  function tmp(name) { return path.join(os.tmpdir(), name); }

  // ── UI ───────────────────────────────────────────────────────────────────────
  var trackSel = document.getElementById("track");
  var btnRefresh = document.getElementById("refresh");
  var btnGen = document.getElementById("generate");
  var btnCut = document.getElementById("cutsilence");
  function busy(on) { btnGen.disabled = on; btnCut.disabled = on; btnRefresh.disabled = on; }

  function refreshTracks() {
    setStatus("Reading sequence…");
    return evalES("listAudioTracks()").then(function (res) {
      var r = parseES(res); trackSel.innerHTML = "";
      if (!r.ok) { setStatus(r.body, "err"); return; }
      r.body.split("\n").forEach(function (row) {
        var p = row.split("::"); var o = document.createElement("option");
        o.value = p[0]; o.textContent = "Track " + (parseInt(p[0], 10) + 1) + ": " + p[1] + " (" + p[2] + " clips)";
        trackSel.appendChild(o);
      });
      setStatus("Ready.", "ok");
    });
  }

  function getClip() {
    return evalES("getClipInfo(" + trackSel.value + ")").then(function (res) {
      var r = parseES(res); if (!r.ok) throw new Error(r.body);
      var p = r.body.split("|");
      return { media: p[0], offset: parseFloat(p[1]) || 0, srcStart: parseFloat(p[2]) || 0, srcEnd: parseFloat(p[3]) || 0 };
    });
  }

  // Generate Subtitles ----------------------------------------------------------
  btnGen.addEventListener("click", function () {
    busy(true); setStatus("Reading clip…");
    var chars = document.getElementById("chars").value || "42";
    var lines = document.getElementById("lines").value || "2";
    var secs = document.getElementById("secs").value || "7";
    getClip().then(function (c) {
      var srt = tmp("a2srt_" + Date.now() + ".srt");
      append("Transcribing " + path.basename(c.media) + " …");
      return runApp("transcribe", [c.media, srt, chars, lines, secs,
        String(c.srcStart), String(c.srcEnd), String(c.offset)]).then(function (res) {
        if (res.code !== 0 || !fs.existsSync(srt)) throw new Error((res.out + "\n" + res.err).trim().slice(0, 700) || "Transcription failed.");
        append("Importing caption…");
        return evalES("importFile(" + JSON.stringify(srt) + ")");
      }).then(function (imp) {
        var r = parseES(imp); if (!r.ok) throw new Error(r.body);
        return evalES("addCaptionToTimeline(" + JSON.stringify(srt) + ")");
      }).then(function (capRes) {
        var r = parseES(capRes);
        if (r.ok) setStatus("✓ Captions added to the timeline.", "ok");
        else setStatus("✓ SRT imported to Project panel.\nDrag it onto your timeline.\n(" + r.body + ")", "ok");
      });
    }).catch(function (e) { setStatus("✗ " + e.message, "err"); }).then(function () { busy(false); });
  });

  // Cut Silence (in-place ripple via QE DOM) + tightened export -----------------
  btnCut.addEventListener("click", function () {
    busy(true); setStatus("Reading clip…");
    var thr = (document.getElementById("thr").value || "-30") + "dB";
    var gap = document.getElementById("gap").value || "0.5";
    var pad = document.getElementById("pad").value || "0.05";
    var rippleEl = document.getElementById("ripplelive");
    var rippleLive = rippleEl && rippleEl.checked;
    getClip().then(function (c) {
      append("Detecting silence…");
      return runApp("detect", [c.media, "--threshold", thr, "--min-silence", gap, "--pad", pad]).then(function (res) {
        var info = lastJson(res.out);
        if (res.code !== 0 || !info) throw new Error((res.out + "\n" + res.err).trim().slice(0, 700) || "Detect failed.");
        // Map source-time cuts -> sequence-time, clamped to the clip's used range.
        var seqCuts = [];
        (info.cuts || []).forEach(function (cut) {
          var cs = cut[0], ce = cut[1];
          if (ce <= c.srcStart || cs >= c.srcEnd) return;
          var s = Math.max(cs, c.srcStart) - c.srcStart + c.offset;
          var e = Math.min(ce, c.srcEnd) - c.srcStart + c.offset;
          if (e - s > 0.02) seqCuts.push([s, e]);
        });
        append("Found " + seqCuts.length + " silent region(s) in the clip.");
        if (rippleLive && seqCuts.length) {
          var csv = seqCuts.map(function (x) { return x[0].toFixed(4) + ":" + x[1].toFixed(4); }).join(",");
          return evalES("applyRippleCuts(" + JSON.stringify(csv) + ")").then(function (rr) {
            var r = parseES(rr);
            if (r.ok) append("✓ Ripple-cut " + r.body + " region(s) from the timeline (beta — verify the edit).");
            else append("Live ripple-cut unavailable (" + r.body + "). Tightened clip will still be made.");
          });
        }
      });
    }).then(function () {
      // Always also produce a reliable tightened clip + synced SRT.
      return getClip().then(function (c) {
        var dir = path.join(path.dirname(c.media), "audio_to_srt_exports");
        try { fs.mkdirSync(dir, { recursive: true }); } catch (e) {}
        var base = path.basename(c.media).replace(/\.[^.]+$/, "");
        var ext = path.extname(c.media) || ".mp4";
        var outMedia = path.join(dir, base + "_nosilence" + ext);
        var outSrt = path.join(dir, base + "_nosilence.srt");
        var words = tmp("a2srt_w_" + Date.now() + ".json");
        var chars = document.getElementById("chars").value || "42";
        var lines = document.getElementById("lines").value || "2";
        var secs = document.getElementById("secs").value || "7";
        append("Rendering tightened clip…");
        return runApp("transcribe", [c.media, tmp("a2srt_t_" + Date.now() + ".srt"),
          chars, lines, secs, "0", "0", "0", "--words-out", words]).then(function () {
          return runApp("silence", [c.media, "--out", outMedia, "--srt-out", outSrt,
            "--words", words, "--threshold", thr, "--min-silence", gap, "--pad", pad,
            "--max-chars", chars, "--max-lines", lines, "--max-secs", secs]);
        }).then(function (res) {
          var info = lastJson(res.out);
          if (info && info.status === "ok") {
            evalES("importFile(" + JSON.stringify(outMedia) + ")");
            if (info.srt_out) evalES("importFile(" + JSON.stringify(outSrt) + ")");
            setStatus("✓ Done. Tightened clip + SRT imported (removed " + info.removed_secs + "s).", "ok");
          } else if (info && info.status === "nothing_to_cut") {
            setStatus("No silence found above the threshold.", "ok");
          } else {
            setStatus("✗ " + ((res.out + " " + res.err).trim().slice(0, 400) || "Silence cut failed."), "err");
          }
        });
      });
    }).catch(function (e) { setStatus("✗ " + e.message, "err"); }).then(function () { busy(false); });
  });

  btnRefresh.addEventListener("click", refreshTracks);

  // Boot
  if (!APP) { setStatus("Audio2SRT Studio not found.\nOpen the app and click 'Install Premiere panel', then reopen this panel.", "err"); busy(true); }
  else refreshTracks();
})();
