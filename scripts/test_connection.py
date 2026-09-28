#!/usr/bin/env python3
"""Quick test of FsosDriver against real switch."""

import socket
import sys
import time

sys.path.insert(0, "/tmp/napalm-lib")
sys.path.insert(0, "/home/staz/Programming/napalm-fsos")

HOST = "10.208.1.13"
PORT = 23
USERNAME = "admin"
PASSWORD = "admin"

def strip_telnet(data):
    """Remove telnet IAC bytes."""
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0xFF:  # IAC
            if i + 1 < len(data):
                i += 3 if i + 2 < len(data) else 2
            else:
                i += 1
        else:
            result.append(data[i])
            i += 1
    return bytes(result)

def test_connection():
    print("Connecting...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((HOST, PORT))
    time.sleep(2)

    # Read banner
    data = b""
    sock.settimeout(0.5)
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        except:
            break
    
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"Banner: {repr(clean[:200])}")

    # Login
    sock.send(b"admin\r")
    time.sleep(1)
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        except:
            break
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"After username: {repr(clean[:200])}")

    sock.send(b"admin\r")
    time.sleep(2)
    data = b""
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        except:
            break
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"After password: {repr(clean[-100:])}")

    # Enable mode
    sock.send(b"enable\r")
    time.sleep(1)
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        except:
            break
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"After enable: {repr(clean[-100:])}")

    # Disable pagination
    sock.send(b"terminal length 0\r")
    time.sleep(1)
    data = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        except:
            break
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"After pagination: {repr(clean[-100:])}")

    # Show version
    sock.send(b"show version\r")
    time.sleep(3)
    data = b""
    while True:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
        except:
            break
    clean = strip_telnet(data).decode('utf-8', errors='replace')
    print(f"\n=== show version ===")
    print(clean[:500])

    sock.close()

if __name__ == "__main__":
    test_connection()
