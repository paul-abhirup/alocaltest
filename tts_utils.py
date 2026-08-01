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
