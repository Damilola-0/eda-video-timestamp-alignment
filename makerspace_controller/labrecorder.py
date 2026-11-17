import socket


def tcp_command(host, port, command, timeout=5):
    """Send one LabRecorder RCS command and require its two-byte OK acknowledgment."""
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall((command + "\n").encode("utf-8"))
        response = b""
        while len(response) < 2:
            chunk = sock.recv(2 - len(response))
            if not chunk:
                raise RuntimeError("LabRecorder connection closed after " + repr(command))
            response += chunk
    if response != b"OK":
        raise RuntimeError("LabRecorder did not acknowledge " + repr(command) + ": " + repr(response))
    return response
