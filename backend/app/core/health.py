"""
Media stack health check (Harpocrates).

Reports the state of the video/image media stack so a deployment can verify
the PyAV/FFmpeg toolchain is present and that the OpenCV/PyAV FFmpeg-collision
mitigation is in effect (see WORK_AND_FAILURES §4.2 and
``modules.capacity.video_capacity._require_cv2``).

Design rule: this module must NOT import OpenCV. The video path uses PyAV
exclusively; importing cv2 here would reintroduce the dual-libavdevice
collision the app deliberately avoids. It only checks whether cv2 is *already*
loaded (it should not be during normal operation).
"""
from __future__ import annotations

import sys
from typing import Dict


#: Dependency versions this build was developed and tested against. The running
#: environment may differ (see ``media_health`` -> ``version_drift``); the app
#: is written to tolerate the drift, but the tested set is recorded here so
#: deployments (e.g. the Hostinger Docker image) can pin a known-good stack.
TESTED_VERSIONS = {
    "av": "13.1.0",
    "numpy": "1.26.3 (dev/CI); tolerant of 2.0.x",
    "opencv-python": "4.9.0.80 (fallback prober only; lazy-imported)",
}


def _pkg_version(mod_name: str) -> str:
    try:
        mod = __import__(mod_name)
        return getattr(mod, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({type(exc).__name__})"


def media_health() -> Dict[str, object]:
    """Return a JSON-serializable snapshot of the media stack.

    ``ok`` is True when PyAV is importable and exposes an H.264 encoder (the
    engine's re-encode path). Never raises: a broken stack is reported, not
    thrown, so ``/api/healthz`` still answers.
    """
    health: Dict[str, object] = {
        "pyav_available": False,
        "h264_encoder": False,
        "libav_version": None,
        "numpy_version": _pkg_version("numpy"),
        "cv2_loaded": "cv2" in sys.modules,  # should be False in normal operation
        "tested_versions": TESTED_VERSIONS,
        "warnings": [],
    }

    try:
        import av  # local import: keep module import side-effect free

        health["pyav_available"] = True
        health["av_version"] = getattr(av, "__version__", "unknown")
        try:
            # libav* build strings PyAV was linked against.
            health["libav_version"] = {
                name: ".".join(str(x) for x in ver)
                for name, ver in av.library_versions.items()
            }
        except Exception:  # noqa: BLE001
            health["libav_version"] = None
        try:
            from av.codec import Codec

            Codec("h264", "w")  # raises if no encoder is available
            health["h264_encoder"] = True
        except Exception as exc:  # noqa: BLE001
            health["warnings"].append(f"no usable h264 encoder: {exc}")
    except Exception as exc:  # noqa: BLE001
        health["warnings"].append(f"PyAV unavailable: {exc}")

    if health["cv2_loaded"]:
        health["warnings"].append(
            "OpenCV is already imported; it should only load lazily as a video "
            "prober fallback. Eager cv2 import can trigger the PyAV/OpenCV "
            "libavdevice collision."
        )

    health["ok"] = bool(health["pyav_available"] and health["h264_encoder"])
    return health
