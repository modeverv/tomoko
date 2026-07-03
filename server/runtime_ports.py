from __future__ import annotations

import errno
import socket
import subprocess
from dataclasses import dataclass, field


@dataclass(slots=True)
class PortGuardResult:
    host: str
    port: int
    available: bool
    listeners: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""

    def message(self) -> str:
        if self.available:
            return f"Tomoko internal WebSocket port {self.port} is available on {self.host}."
        lines = [
            (
                f"Tomoko internal WebSocket port {self.port} is already in use "
                f"on {self.host}."
            ),
            (
                "Set TOMOKO_INTERNAL_WS_PORT to a free port, and keep "
                "TOMOKO_INTERNAL_WS_URL pointed at the same port."
            ),
        ]
        if self.error:
            lines.append(f"bind_error: {self.error}")
        if self.listeners:
            lines.append("listeners:")
            lines.extend(f"  {line}" for line in self.listeners)
        return "\n".join(lines)


def check_tcp_port_available(host: str, port: int) -> PortGuardResult:
    bind_error = ""
    for family, socktype, proto, _canonname, sockaddr in socket.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    ):
        with socket.socket(family, socktype, proto) as probe:
            try:
                probe.bind(sockaddr)
            except OSError as exc:
                bind_error = str(exc)
                if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
                    return PortGuardResult(
                        host=host,
                        port=port,
                        available=False,
                        listeners=_tcp_listener_lines(port),
                        error=bind_error,
                    )
                continue
            return PortGuardResult(host=host, port=port, available=True)
    return PortGuardResult(
        host=host,
        port=port,
        available=False,
        listeners=_tcp_listener_lines(port),
        error=bind_error or "no bindable address found",
    )


def _tcp_listener_lines(port: int) -> tuple[str, ...]:
    try:
        completed = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if completed.returncode != 0:
        return ()
    return tuple(
        line.strip()
        for line in completed.stdout.splitlines()[1:]
        if line.strip()
    )
