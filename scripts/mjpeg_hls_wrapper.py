#!/usr/bin/env python3
"""Expose an MJPEG source as browser-friendly HLS.

This utility runs ffmpeg to transcode an MJPEG URL into a local HLS playlist
and serves generated files via a tiny HTTP server.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
from time import sleep


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Wrap MJPEG URL into HLS output served over HTTP."
    )
    parser.add_argument("--input-url", required=True, help="MJPEG source URL.")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="HTTP bind address for HLS files (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8899,
        help="HTTP port for HLS files (default: 8899).",
    )
    parser.add_argument(
        "--segment-duration",
        type=float,
        default=1.0,
        help="HLS segment duration in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--playlist-size",
        type=int,
        default=6,
        help="Number of segments kept in playlist (default: 6).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated HLS files. Uses temp dir if omitted.",
    )
    return parser


def _ffmpeg_command(
    input_url: str,
    playlist_path: Path,
    segment_pattern: str,
    segment_duration: float,
    playlist_size: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-i",
        input_url,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "25",
        "-sc_threshold",
        "0",
        "-f",
        "hls",
        "-hls_time",
        str(segment_duration),
        "-hls_list_size",
        str(playlist_size),
        "-hls_flags",
        "delete_segments+append_list+independent_segments",
        "-hls_segment_filename",
        segment_pattern,
        str(playlist_path),
    ]


def _start_http_server(bind: str, port: int, root_dir: Path) -> ThreadingHTTPServer:
    handler = partial(SimpleHTTPRequestHandler, directory=str(root_dir))
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    return server


def _print_start_info(bind: str, port: int, playlist_name: str, output_dir: Path) -> None:
    print("MJPEG -> HLS wrapper started")
    print(f"HLS URL: http://{bind}:{port}/{playlist_name}")
    print(f"HLS files directory: {output_dir}")
    print("Press Ctrl+C to stop.")


def main() -> int:
    args = _build_arg_parser().parse_args()

    if shutil.which("ffmpeg") is None:
        print("Error: ffmpeg is not installed or not in PATH.", file=sys.stderr)
        return 2

    owns_output_dir = args.output_dir is None
    output_dir = (
        Path(mkdtemp(prefix="mjpeg-hls-wrapper-"))
        if owns_output_dir
        else Path(args.output_dir).expanduser().resolve()
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    playlist_name = "stream.m3u8"
    playlist_path = output_dir / playlist_name
    segment_pattern = str(output_dir / "segment_%05d.ts")

    ffmpeg_cmd = _ffmpeg_command(
        args.input_url,
        playlist_path,
        segment_pattern,
        args.segment_duration,
        args.playlist_size,
    )
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd)

    server = _start_http_server(args.bind, args.port, output_dir)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    _print_start_info(args.bind, args.port, playlist_name, output_dir)

    stop_requested = False

    def _request_stop(_signum: int, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGTERM, _request_stop)

    try:
        while not stop_requested:
            code = ffmpeg_proc.poll()
            if code is not None:
                print(f"ffmpeg exited with code {code}", file=sys.stderr)
                return code if code != 0 else 1
            sleep(0.2)
        return 0
    finally:
        server.shutdown()
        server.server_close()
        if ffmpeg_proc.poll() is None:
            ffmpeg_proc.terminate()
            try:
                ffmpeg_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                ffmpeg_proc.kill()
        if owns_output_dir:
            for file_path in output_dir.glob("*"):
                try:
                    if file_path.is_file():
                        file_path.unlink()
                except OSError:
                    pass
            try:
                output_dir.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
