"""Transcribe a local interview excerpt, preserving seconds on the source audio clock."""

import argparse
import json
import math
from importlib.metadata import version
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("--model", default="base.en", help="Model name or local CTranslate2 directory")
    parser.add_argument("--language", default="en")
    parser.add_argument("--device", choices=["cuda", "cpu"], default="cpu")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.start) or args.start < 0 or not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("start must be nonnegative and seconds must be positive and finite")

    started = perf_counter()
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    rate = 16000
    first = round(args.start * rate)
    samples = decode_audio(str(args.media), sampling_rate=rate)[first:first + round(args.seconds * rate)]
    if not samples.size:
        parser.error("empty audio window")
    offset = first / rate
    duration = samples.size / rate
    compute = "float16" if args.device == "cuda" else "int8"
    model = WhisperModel(args.model, device=args.device, compute_type=compute)
    segments, _ = model.transcribe(samples, language=args.language, beam_size=5)
    rows = [{"start": round(offset + s.start, 3), "end": round(offset + min(s.end, duration), 3),
             "text": s.text.strip()} for s in segments if s.start < duration]
    elapsed = perf_counter() - started
    print(json.dumps({"media": args.media.name, "start": offset, "seconds": duration,
                      "model": Path(args.model).name, "language": args.language, "device": args.device,
                      "compute_type": compute, "faster_whisper": version("faster-whisper"),
                      "elapsed_seconds": round(elapsed, 3), "realtime_multiple": round(duration / elapsed, 2),
                      "segments": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
