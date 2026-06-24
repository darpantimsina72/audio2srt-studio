/* main.js — panel logic for the Premiere "Audio to SRT" extension.
 *
 * Runs in CEP with Node enabled (--enable-nodejs --mixed-context). It drives the
 * ExtendScript host (audio_to_srt.jsx) and runs the shared Python scripts
 * (transcribe.py / silence.py) that live in the distributable folder.
 */

(function () {
  "use strict";

  // ── Node modules (CEP exposes require when nodejs is enabled) ────────────────
  var _require = (typeof window.cep_node !== "undefined" && window.cep_node.require)
    ? window.cep_node.require
    : (typeof require !== "undefined" ? require : null);

  if (!_require) {
    document.getElementById("status").textContent =
      "ERROR: Node.js is not enabled for this panel. Re-run setup, then fully quit and reopen Premiere.";
    return;
  }

  var fs = _require("fs");
  var os = _require("os");
  var path = _require("path");
  var cp = _require("child_process");

  // ── ExtendScript bridge (raw CEP API — no CSInterface dependency) ────────────
  function evalES(script) {
    return new Promise(function (resolve) {
      window.__adobe_cep__.evalScript(script, function (res) { resolve(res); });
    });
  }
  function parseES(res) {
    res = String(res || "");
    var bar = res.indexOf("|");
    var tag = bar >= 0 ? res.slice(0, bar) : res;
    var body = bar >= 0 ? res.slice(bar + 1) : "";
    return { ok: tag === "OK", body: body };
  }

  // ── Status helpers ───────────────────────────────────────────────────────────
  var statusEl = document.getElementById("status");
  function setStatus(msg, cls) {
    statusEl.textContent = msg;
    statusEl.className = cls || "";
  }
  function append(msg) { statusEl.textContent += "\n" + msg; statusEl.scrollTop = statusEl.scrollHeight; }

  // ── Locate the distributable folder (transcribe.py / silence.py / .env) ──────
  function projectDir() {
    var cfg = path.join(os.homedir(),
      process.platform === "win32" ? ".audio_to_srt_premiere_path" : ".audio_to_srt_premiere_path");
    try {
      var p = fs.readFileSync(cfg, "utf8").trim();
      if (p && fs.existsSync(path.join(p, "transcribe.py"))) return p;
    } catch (e) {}
    return null;
  }

  // ── Find Python ──────────────────────────────────────────────────────────────
  function findPython() {
    var candidates = process.platform === "win32"
      ? [
          path.join(process.env.LOCALAPPDATA || "", "Programs", "Python", "Python312", "python.exe"),
          path.join(process.env.LOCALAPPDATA || "", "Programs", "Python", "Python311", "python.exe"),
          path.join(process.env.LOCALAPPDATA || "", "Programs", "Python", "Python310", "python.exe"),
          "C:\\Python312\\python.exe", "C:\\Python311\\python.exe", "C:\\Python310\\python.exe",
          "python.exe", "python"
        ]
      : [
          "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3", "python3"
        ];
    for (var i = 0; i < candidates.length; i++) {
      var c = candidates[i];
      if (c.indexOf("/") >= 0 || c.indexOf("\\") >= 0) {
        try { if (fs.existsSync(c)) return c; } catch (e) {}
      } else {
        return c; // bare command, rely on PATH
      }
    }
    return process.platform === "win32" ? "python" : "python3";
  }

  function runPython(scriptName, args) {
    return new Promise(function (resolve) {
      var dir = projectDir();
      if (!dir) {
        resolve({ code: 1, out: "", err: "Could not find the Audio-to-SRT folder. Re-run setup." });
        return;
      }
      var py = findPython();
      var full = [path.join(dir, scriptName)].concat(args);
      var child = cp.execFile(py, full, { maxBuffer: 1024 * 1024 * 16, cwd: dir },
        function (error, stdout, stderr) {
          resolve({ code: error ? (error.code || 1) : 0, out: stdout || "", err: stderr || "" });
        });
    });
  }

  function tmpFile(name) { return path.join(os.tmpdir(), name); }

  function exportDir(mediaPath) {
    var d = path.join(path.dirname(mediaPath), "audio_to_srt_exports");
    try { fs.mkdirSync(d, { recursive: true }); } catch (e) {}
    return d;
  }

  function stem(p) { return path.basename(p).replace(/\.[^.]+$/, ""); }

  // ── UI wiring ──────────────────────────────────────────────────────────────
  var trackSel = document.getElementById("track");
  var btnRefresh = document.getElementById("refresh");
  var btnGen = document.getElementById("generate");
  var btnCut = document.getElementById("cutsilence");

  function busy(on) {
    btnGen.disabled = on; btnCut.disabled = on; btnRefresh.disabled = on;
  }

  function refreshTracks() {
    setStatus("Reading sequence…");
    return evalES("listAudioTracks()").then(function (res) {
      var r = parseES(res);
      trackSel.innerHTML = "";
      if (!r.ok) { setStatus(r.body, "err"); return; }
      var rows = r.body.split("\n");
      for (var i = 0; i < rows.length; i++) {
        var parts = rows[i].split("::");
        var opt = document.createElement("option");
        opt.value = parts[0];
        opt.textContent = "Track " + (parseInt(parts[0], 10) + 1) + ": " + parts[1] + " (" + parts[2] + " clips)";
        trackSel.appendChild(opt);
      }
      setStatus("Ready. " + rows.length + " audio track(s).", "ok");
    });
  }

  function getClip() {
    return evalES("getClipInfo(" + trackSel.value + ")").then(function (res) {
      var r = parseES(res);
      if (!r.ok) throw new Error(r.body);
      var p = r.body.split("|");
      return { media: p[0], offset: p[1] || "0", srcStart: p[2] || "0", srcEnd: p[3] || "0" };
    });
  }

  // Generate Subtitles ----------------------------------------------------------
  btnGen.addEventListener("click", function () {
    busy(true);
    setStatus("Reading selected clip…");
    var chars = document.getElementById("chars").value || "42";
    var lines = document.getElementById("lines").value || "2";
    var secs = document.getElementById("secs").value || "7";

    getClip().then(function (c) {
      var srt = tmpFile("a2srt_" + Date.now() + ".srt");
      append("Transcribing: " + path.basename(c.media));
      append("(ElevenLabs — needs internet, please wait)");
      return runPython("transcribe.py",
        [c.media, srt, chars, lines, secs, c.srcStart, c.srcEnd, c.offset])
        .then(function (res) {
          if (res.code !== 0 || !fs.existsSync(srt)) {
            throw new Error((res.out + "\n" + res.err).trim().slice(0, 700) || "Transcription failed.");
          }
          append("Transcribed. Importing caption…");
          return evalES("importFile(" + JSON.stringify(srt) + ")");
        })
        .then(function (imp) {
          var r = parseES(imp);
          if (!r.ok) throw new Error(r.body);
          return evalES("addCaptionToTimeline(" + JSON.stringify(srt) + ")");
        })
        .then(function (capRes) {
          var r = parseES(capRes);
          if (r.ok) {
            setStatus("✓ Done — captions added to the timeline.", "ok");
          } else {
            setStatus("✓ SRT imported to the Project panel.\nDrag it onto your timeline to add captions.\n(" + r.body + ")", "ok");
          }
        });
    }).catch(function (e) {
      setStatus("✗ " + e.message, "err");
    }).then(function () { busy(false); });
  });

  // Cut Silence -----------------------------------------------------------------
  btnCut.addEventListener("click", function () {
    busy(true);
    setStatus("Reading selected clip…");
    var thr = (document.getElementById("thr").value || "-30") + "dB";
    var gap = document.getElementById("gap").value || "0.5";
    var pad = document.getElementById("pad").value || "0.05";
    var withSubs = document.getElementById("withsubs").checked;
    var chars = document.getElementById("chars").value || "42";
    var lines = document.getElementById("lines").value || "2";
    var secs = document.getElementById("secs").value || "7";

    getClip().then(function (c) {
      var dir = exportDir(c.media);
      var ext = path.extname(c.media) || ".mp4";
      var base = stem(c.media);
      var outMedia = path.join(dir, base + "_nosilence" + ext);
      var outSrt = path.join(dir, base + "_nosilence.srt");
      var wordsJson = tmpFile("a2srt_words_" + Date.now() + ".json");
      var throwSrt = tmpFile("a2srt_throw_" + Date.now() + ".srt");

      var chain = Promise.resolve();
      if (withSubs) {
        append("Transcribing for synced subtitles…");
        chain = runPython("transcribe.py",
          [c.media, throwSrt, chars, lines, secs, "0", "0", "0", "--words-out=" + wordsJson])
          .then(function (res) {
            if (res.code !== 0) throw new Error((res.out + "\n" + res.err).trim().slice(0, 700));
          });
      }

      return chain.then(function () {
        append("Detecting + cutting silence (ffmpeg)…");
        var args = [c.media, "--out", outMedia, "--threshold", thr, "--min-silence", gap, "--pad", pad];
        if (withSubs) {
          args = args.concat(["--srt-out", outSrt, "--words", wordsJson,
            "--max-chars", chars, "--max-lines", lines, "--max-secs", secs]);
        }
        return runPython("silence.py", args);
      }).then(function (res) {
        var line = (res.out || "").trim().split("\n").pop();
        var info = {};
        try { info = JSON.parse(line); } catch (e) {}
        if (res.code !== 0 || info.status === undefined) {
          throw new Error((res.out + "\n" + res.err).trim().slice(0, 700) || "Silence cut failed.");
        }
        if (info.status === "nothing_to_cut") {
          setStatus("No silence found above the threshold. Nothing cut.", "ok");
          return;
        }
        append("Removed " + info.removed_secs + "s of silence. Importing tightened clip…");
        return evalES("importFile(" + JSON.stringify(outMedia) + ")").then(function (imp) {
          var r = parseES(imp);
          var extra = "";
          if (info.srt_out) {
            return evalES("importFile(" + JSON.stringify(outSrt) + ")").then(function () {
              setStatus("✓ Tightened clip + synced SRT imported.\nSaved in:\n" + dir +
                "\nRemoved " + info.removed_secs + "s · kept " + info.kept_secs + "s.", "ok");
            });
          }
          setStatus("✓ Tightened clip imported.\nSaved in:\n" + dir +
            "\nRemoved " + info.removed_secs + "s · kept " + info.kept_secs + "s.", "ok");
        });
      });
    }).catch(function (e) {
      setStatus("✗ " + e.message, "err");
    }).then(function () { busy(false); });
  });

  btnRefresh.addEventListener("click", refreshTracks);

  // ── Boot ─────────────────────────────────────────────────────────────────────
  evalES("ping()").then(function (res) {
    var r = parseES(res);
    if (!projectDir()) {
      setStatus("Setup needed: the Audio-to-SRT folder was not found.\nRun setup.command / setup.bat once, then reopen this panel.", "err");
      busy(true);
      return;
    }
    refreshTracks();
  });
})();
