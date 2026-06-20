"""Speech-to-text transcribers.

Two providers are supported, chosen by ``config.json`` ``provider`` field:

* ``siliconflow`` (default, free) -- SiliconFlow SenseVoiceSmall, OpenAI-compatible.
* ``volcengine``               -- Volcengine (Doubao) Seed-ASR BigModel.
"""

import base64
import time
import uuid

import requests


# --------------------------------------------------------------------------- #
# SiliconFlow (free, default)
# --------------------------------------------------------------------------- #

class SiliconFlowTranscriber:
    """SiliconFlow /audio/transcriptions (OpenAI-compatible, multipart upload)."""

    def __init__(self, api_key, base_url, model, *, use_system_proxy=False):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.session = requests.Session()
        self.session.trust_env = use_system_proxy

    def transcribe(self, wav_bytes: bytes) -> str:
        if not wav_bytes:
            raise RuntimeError("Empty audio")
        if not self.api_key:
            raise RuntimeError("SiliconFlow api_key not set in config.json")

        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model}
        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            resp = self.session.post(
                self.base_url,
                headers=headers,
                files=files,
                data=data,
                timeout=90,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"SiliconFlow network error: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"SiliconFlow failed HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            text = (resp.json().get("text") or "").strip()
        except ValueError as exc:
            raise RuntimeError(f"SiliconFlow bad response: {resp.text[:200]}") from exc

        if not text:
            raise RuntimeError("SiliconFlow returned empty text")
        return text


# --------------------------------------------------------------------------- #
# Volcengine / Doubao Seed-ASR 2.0 BigModel (HTTP submit + poll)
# --------------------------------------------------------------------------- #

_RESOURCE_ID = "volc.seedasr.auc"
_POLL_INTERVAL = 0.3
_POLL_MAX = 60
_SUBMIT_TIMEOUT = 90
_QUERY_TIMEOUT = 30


class VolcengineTranscriber:
    def __init__(self, api_key, base_url, *, use_system_proxy=False):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.trust_env = use_system_proxy
        base = base_url.rstrip("/")
        for suffix in ("/submit", "/query"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        self.submit_url = base + "/submit"
        self.query_url = base + "/query"

    def _headers(self, request_id: str, *, submit: bool = False) -> dict:
        h = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": _RESOURCE_ID,
            "X-Api-Request-Id": request_id,
            "x-api-key": self.api_key,
        }
        if submit:
            h["X-Api-Sequence"] = "-1"
        return h

    def transcribe(self, wav_bytes: bytes) -> str:
        if not wav_bytes:
            raise RuntimeError("Empty audio")
        if not self.api_key:
            raise RuntimeError("Volcengine api_key not set in config.json")

        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
        request_id = str(uuid.uuid4())

        body = {
            "user": {"uid": "voice-input"},
            "audio": {"data": audio_b64, "format": "wav"},
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
            },
        }

        try:
            resp = self.session.post(
                self.submit_url,
                headers=self._headers(request_id, submit=True),
                json=body,
                timeout=_SUBMIT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Submit network error: {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(f"Submit failed HTTP {resp.status_code}: {resp.text[:200]}")

        status = resp.headers.get("X-Api-Status-Code", "")
        if status not in ("20000000", "20000001", "20000002"):
            msg = resp.headers.get("X-Api-Message", "")
            raise RuntimeError(f"Submit error: {status} {msg}")

        query_headers = self._headers(request_id, submit=False)
        for _ in range(_POLL_MAX):
            time.sleep(_POLL_INTERVAL)
            try:
                resp = self.session.post(
                    self.query_url,
                    headers=query_headers,
                    json={},
                    timeout=_QUERY_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise RuntimeError(f"Query network error: {exc}") from exc

            if resp.status_code != 200:
                raise RuntimeError(f"Query failed HTTP {resp.status_code}")

            status = resp.headers.get("X-Api-Status-Code", "")
            if status == "20000000":
                return self._extract_text(resp.json())
            if status == "20000003":
                raise RuntimeError("No speech detected (silence)")
            if status not in ("20000001", "20000002"):
                msg = resp.headers.get("X-Api-Message", "")
                raise RuntimeError(f"Query error: {status} {msg}")

        raise RuntimeError("Transcription timed out")

    @staticmethod
    def _extract_text(data: dict) -> str:
        result = data.get("result") if isinstance(data, dict) else None
        text = ""
        if isinstance(result, dict):
            text = (result.get("text") or "").strip()
            if not text and isinstance(result.get("utterances"), list):
                text = "".join(
                    str(u.get("text") or "") for u in result["utterances"] if isinstance(u, dict)
                ).strip()
        if not text:
            text = (data.get("text") or "").strip()
        if not text:
            raise RuntimeError("ASR returned empty text")
        return text


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #

# Service endpoints are fixed here so users only need to fill in an api_key.
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
SILICONFLOW_MODEL = "FunAudioLLM/SenseVoiceSmall"
VOLCENGINE_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel"


def make_transcriber(cfg: dict):
    """Build a transcriber from the loaded config dict.

    Users only set ``provider`` and the matching ``*_api_key``. URLs/models are
    fixed above.
    """
    provider = (cfg.get("provider") or "siliconflow").lower()
    use_proxy = cfg.get("use_system_proxy", False)

    if provider == "siliconflow":
        return SiliconFlowTranscriber(
            cfg.get("siliconflow_api_key", ""),
            SILICONFLOW_URL,
            SILICONFLOW_MODEL,
            use_system_proxy=use_proxy,
        )

    if provider == "volcengine":
        return VolcengineTranscriber(
            cfg.get("volcengine_api_key", ""),
            VOLCENGINE_URL,
            use_system_proxy=use_proxy,
        )

    raise RuntimeError(f"Unknown provider in config.json: {provider!r}")
