"""
CVOLVE PRO — Text-to-Speech
============================
Uses Puter.js (free, no API key needed) for TTS via st.components.v1.html.
Falls back to browser SpeechSynthesis when Puter is unavailable.
"""

import os
import logging


def tts_component_html(question_text: str) -> str:
    """Full HTML page for st.components.v1.html — loads Puter.js and plays speech."""
    escaped = question_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            display: flex; align-items: center; justify-content: flex-start;
            min-height: 50px; background: transparent;
        }}
        .speak-btn {{
            padding: 10px 24px; border-radius: 26px; border: none;
            background: linear-gradient(135deg, #6c5ce7, #a29bfe);
            color: white; font-weight: 700; cursor: pointer; font-size: 15px;
            letter-spacing: 0.3px;
            transition: transform 0.2s, box-shadow 0.2s;
            box-shadow: 0 3px 10px rgba(108,92,231,0.3);
        }}
        .speak-btn:hover {{ transform: scale(1.05); }}
        .speak-btn:active {{ transform: scale(0.97); }}
        .speak-btn.playing {{ background: linear-gradient(135deg, #e94560, #c0392b); }}
        #status {{ font-size: 12px; color: #888; margin-top: 4px; min-height: 18px; }}
    </style>
</head>
<body>
    <div>
        <button class="speak-btn" id="speakBtn">🔊 Play Question</button>
        <div id="status"></div>
    </div>

    <script src="https://js.puter.com/v2/"></script>
    <script>
    (function() {{
        var btn = document.getElementById('speakBtn');
        var status = document.getElementById('status');
        var speaking = false;
        var text = `{escaped}`;

        btn.addEventListener('click', function() {{
            if (speaking) return;
            speaking = true;
            btn.classList.add('playing');
            btn.textContent = '🔊 Playing...';
            status.textContent = '';

            if (typeof puter !== 'undefined' && puter.ai && puter.ai.txt2speech) {{
                puter.ai.txt2speech(text)
                    .then(function(audio) {{
                        audio.play();
                        audio.addEventListener('ended', function() {{
                            speaking = false;
                            btn.classList.remove('playing');
                            btn.textContent = '🔊 Play Question';
                        }});
                    }})
                    .catch(function(err) {{
                        console.error('Puter TTS error:', err);
                        fallbackSpeak(text);
                    }});
            }} else {{
                fallbackSpeak(text);
            }}
        }});

        function fallbackSpeak(t) {{
            if (window.speechSynthesis) {{
                window.speechSynthesis.cancel();
                var u = new SpeechSynthesisUtterance(t);
                u.rate = 0.95; u.pitch = 1.05;
                u.onend = function() {{
                    speaking = false;
                    btn.classList.remove('playing');
                    btn.textContent = '🔊 Play Question';
                }};
                window.speechSynthesis.speak(u);
            }} else {{
                status.textContent = '❌ TTS not available';
                speaking = false;
                btn.classList.remove('playing');
                btn.textContent = '🔊 Play Question';
            }}
        }}
    }})();
    </script>
</body>
</html>
    """


def voice_recorder_component_html(textarea_key: str) -> str:
    """Full HTML page — records voice via MediaRecorder and transcribes via Deepgram API."""
    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            padding: 8px; background: transparent;
        }}
        .container {{ text-align: center; }}
        .btn {{
            padding: 8px 18px; border-radius: 24px; border: none;
            cursor: pointer; font-size: 14px; font-weight: 600;
            transition: all 0.2s; margin: 3px 4px;
        }}
        .btn-record {{ background: linear-gradient(135deg,#e94560,#c0392b); color: #fff; }}
        .btn-stop {{ background: linear-gradient(135deg,#2ecc71,#16a085); color: #fff; display: none; }}
        .btn:hover {{ transform: scale(1.05); }}
        #status {{ font-size: 13px; color: #555; margin-top: 6px; min-height: 20px; }}
        #transcript {{
            margin-top: 8px; padding: 10px; border-radius: 8px;
            background: #f8f9ff; border: 1px solid #dee2ff;
            font-size: 14px; line-height: 1.5; min-height: 40px;
            white-space: pre-wrap; text-align: left; word-wrap: break-word;
        }}
        .pulse {{ animation: pulse 1s infinite; }}
        @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.4}} }}
        .copy-btn {{
            padding: 6px 14px; border-radius: 20px; border: none; cursor: pointer;
            background: #3498db; color: white; font-size: 12px; font-weight: 600;
            margin-top: 8px; transition: transform 0.2s;
        }}
        .copy-btn:hover {{ transform: scale(1.05); }}
        .copy-btn.copied {{ background: #2ecc71; }}
    </style>
</head>
<body>
    <div class="container">
        <button class="btn btn-record" id="startBtn">🎙️ Start Speaking</button>
        <button class="btn btn-stop" id="stopBtn">⏹️ Stop</button>
        <div id="status"></div>
        <div id="transcript" style="display:none"></div>
        <div id="copyArea" style="display:none;margin-top:6px">
            <button class="copy-btn" id="copyBtn">📋 Copy & Auto-Fill</button>
        </div>
    </div>

    <script>
    (function() {{
        var startBtn = document.getElementById('startBtn');
        var stopBtn = document.getElementById('stopBtn');
        var statusEl = document.getElementById('status');
        var transcriptEl = document.getElementById('transcript');
        var copyArea = document.getElementById('copyArea');
        var copyBtn = document.getElementById('copyBtn');
        var stream = null;
        var mediaRecorder = null;
        var audioChunks = [];
        var finalText = '';
        var API_URL = 'http://localhost:8000/api/transcribe';

        function setTranscript(val) {{
            try {{
                window.parent.location.search = '?v=' + encodeURIComponent(val);
            }} catch(e) {{
                try {{
                    window.location.search = '?v=' + encodeURIComponent(val);
                }} catch(e2) {{}}
            }}
        }}

        function copyToClipboard(text) {{
            var ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.left = '-9999px';
            document.body.appendChild(ta);
            ta.select();
            try {{
                document.execCommand('copy');
                copyBtn.textContent = '✅ Copied!';
                copyBtn.classList.add('copied');
                setTimeout(function() {{
                    copyBtn.textContent = '📋 Copy & Auto-Fill';
                    copyBtn.classList.remove('copied');
                }}, 2000);
            }} catch(e) {{}}
            document.body.removeChild(ta);
        }}

        startBtn.addEventListener('click', async function() {{
            try {{
                stream = await navigator.mediaDevices.getUserMedia({{ audio: true }});
            }} catch(e) {{
                statusEl.textContent = e.name === 'NotAllowedError' ? '❌ Mic blocked' : 'Error: '+e.message;
                return;
            }}
            audioChunks = [];
            finalText = '';
            transcriptEl.style.display = 'none';
            copyArea.style.display = 'none';
            var mime = MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : 'audio/ogg';
            mediaRecorder = new MediaRecorder(stream, {{ mimeType: mime }});
            mediaRecorder.ondataavailable = function(e) {{ if (e.data.size > 0) audioChunks.push(e.data); }};
            mediaRecorder.onstop = async function() {{
                startBtn.style.display = 'inline-flex';
                stopBtn.style.display = 'none';
                statusEl.innerHTML = '⏳ Transcribing...';
                var blob = new Blob(audioChunks, {{ type: mediaRecorder.mimeType }});
                try {{
                    var resp = await fetch(API_URL, {{ method: 'POST', headers: {{ 'Content-Type': blob.type }}, body: blob }});
                    var data = await resp.json();
                    var txt = (data.transcript || '').trim();
                    if (txt) {{
                        finalText = txt;
                        transcriptEl.textContent = txt;
                        transcriptEl.style.display = 'block';
                        statusEl.innerHTML = '✅ Done! Click <b>Copy & Auto-Fill</b> to send to editor.';
                        copyArea.style.display = 'block';
                        setTranscript(txt);
                    }} else {{
                        statusEl.innerHTML = data.error || 'No speech detected. Try again or type your answer.';
                    }}
                }} catch(e) {{
                    statusEl.innerHTML = 'Error: ' + e.message;
                }}
                if (stream) {{ stream.getTracks().forEach(function(t){{ t.stop(); }}); stream = null; }}
            }};
            startBtn.style.display = 'none';
            stopBtn.style.display = 'inline-flex';
            statusEl.innerHTML = '<span class="pulse" style="color:#e94560;font-weight:600">🔴 Recording...</span>';
            mediaRecorder.start();
        }});

        stopBtn.addEventListener('click', function() {{
            if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
        }});

        copyBtn.addEventListener('click', function() {{
            var txt = finalText.trim() || transcriptEl.textContent.trim();
            if (txt) {{
                copyToClipboard(txt);
                setTranscript(txt);
            }}
        }});
    }})();
    </script>
</body>
</html>
    """
